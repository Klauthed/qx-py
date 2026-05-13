"""order-service entrypoint.

Composition root: wires DI, mediator, eventstore, projections, saga, and
FastAPI.  ``uvicorn order_service.main:app`` boots the HTTP server.

Architecture overview::

    HTTP layer (FastAPI)
        ↓  mediator.send(command/query)
    Application layer (command/query handlers)
        ↓  EventStore.append / EventStore.load
    Domain layer (Order aggregate + events)
        ↓  outbox (integration events)
    NATS (via OutboxRelay, run separately)
        ↓  SagaManager.handle (in worker)
    OrderFulfillmentSaga → ConfirmOrderCommand

Read model::

    EventStore events
        ↓  ProjectionRunner (background task)
    qx_order_summaries (read model)
        ↓  GetOrderQuery reads from here
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from qx.core import QxSettings
from qx.cqrs import ExceptionTranslationBehavior, LoggingBehavior, Mediator
from qx.db import DatabaseSettings, SessionFactory, create_engine, make_session_factory
from qx.db.outbox import DefaultOutboxRecorder
from qx.di import Container
from qx.eventstore import include_eventstore_tables
from qx.http import setup_qx_app
from qx.observability import setup_observability
from qx.projections import ProjectionRunner, include_projection_tables
from sqlalchemy import MetaData

from order_service.application.commands.cancel_order import CancelOrderCommand, CancelOrderHandler
from order_service.application.commands.confirm_order import (
    ConfirmOrderCommand,
    ConfirmOrderHandler,
)
from order_service.application.commands.place_order import PlaceOrderCommand, PlaceOrderHandler
from order_service.application.queries.get_order import GetOrderHandler, GetOrderQuery
from order_service.infrastructure.projections.order_summary import (
    OrderSummaryProjection,
    include_order_summaries_table,
)
from order_service.presentation.routes.orders import router as orders_router

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI

logger = logging.getLogger("order_service")


def build_app() -> FastAPI:
    settings = QxSettings(app={"name": "order-service"})
    metrics, health = setup_observability(settings)

    # ---- Database ----
    db_settings = DatabaseSettings()
    engine = create_engine(db_settings)
    session_factory = make_session_factory(engine)

    # ---- Tables ----
    meta = MetaData()
    events_table, snapshots_table = include_eventstore_tables(meta)
    checkpoints_table = include_projection_tables(meta)
    order_summaries_table = include_order_summaries_table(meta)

    # ---- DI container ----
    container = Container()
    container.register_instance(SessionFactory, session_factory)

    # ---- Mediator ----
    mediator = Mediator(
        container,
        command_behaviors=(LoggingBehavior(), ExceptionTranslationBehavior()),
        query_behaviors=(LoggingBehavior(), ExceptionTranslationBehavior()),
    )
    container.register_instance(Mediator, mediator)

    # ---- Outbox ----
    outbox = DefaultOutboxRecorder()

    # ---- Register command handlers ----
    container.register(PlaceOrderHandler, PlaceOrderHandler)
    container.register(ConfirmOrderHandler, ConfirmOrderHandler)
    container.register(CancelOrderHandler, CancelOrderHandler)
    container.register(GetOrderHandler, GetOrderHandler)

    # Wire the tables and outbox into the handlers via a factory approach:
    # We pre-bind the dependencies that aren't in the DI container.
    def _make_place_handler() -> PlaceOrderHandler:
        return PlaceOrderHandler(session_factory, events_table, snapshots_table, outbox)

    def _make_confirm_handler() -> ConfirmOrderHandler:
        return ConfirmOrderHandler(session_factory, events_table, snapshots_table)

    def _make_cancel_handler() -> CancelOrderHandler:
        return CancelOrderHandler(session_factory, events_table, snapshots_table)

    def _make_get_handler() -> GetOrderHandler:
        return GetOrderHandler(session_factory, order_summaries_table)

    container.register(PlaceOrderHandler, _make_place_handler)
    container.register(ConfirmOrderHandler, _make_confirm_handler)
    container.register(CancelOrderHandler, _make_cancel_handler)
    container.register(GetOrderHandler, _make_get_handler)

    mediator.register_command(PlaceOrderCommand, PlaceOrderHandler)
    mediator.register_command(ConfirmOrderCommand, ConfirmOrderHandler)
    mediator.register_command(CancelOrderCommand, CancelOrderHandler)
    mediator.register_query(GetOrderQuery, GetOrderHandler)

    # ---- Projection ----
    order_summary_projection = OrderSummaryProjection(engine, order_summaries_table)
    runner = ProjectionRunner(checkpoints_table, events_table)
    runner.register(order_summary_projection)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Ensure tables exist (dev / first boot)
        async with engine.begin() as conn:
            await conn.run_sync(meta.create_all)

        # Background projection loop
        _stop = asyncio.Event()

        async def _project() -> None:
            while not _stop.is_set():
                try:
                    async with session_factory() as session, session.begin():
                        await runner.run_once(session)
                except Exception:
                    logger.exception("projection runner error")
                await asyncio.sleep(5)

        task = asyncio.create_task(_project())
        try:
            yield
        finally:
            _stop.set()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await engine.dispose()

    app = setup_qx_app(
        container,
        settings,
        metrics=metrics,
        health=health,
        extra_lifespan=lifespan,
    )
    app.include_router(orders_router)
    return app


app = build_app()
