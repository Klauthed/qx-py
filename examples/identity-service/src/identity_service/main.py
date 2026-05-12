"""identity-service entrypoint.

Composition root: wires DI, mediator, database, observability, and FastAPI.
``uvicorn identity_service.main:app`` boots the HTTP server.

A separate ``identity_service.worker:main`` boots the worker (NATS consumer).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from qx.core import QxSettings
from qx.cqrs import ExceptionTranslationBehavior, LoggingBehavior, Mediator
from qx.db import (
    DatabaseSettings,
    SessionFactory,
    UnitOfWork,
    create_engine,
    make_session_factory,
)
from qx.db.outbox import DefaultOutboxRecorder
from qx.di import Container
from qx.events import EventRegistry, MediatorEventDispatcher
from qx.http import setup_qx_app
from qx.observability import HealthRegistry, Metrics, setup_observability

from identity_service.application import register_events, register_handlers
from identity_service.presentation import register_routes


def build_app() -> FastAPI:
    settings = QxSettings(app={"name": "identity-service"})  # type: ignore[arg-type]
    metrics, health = setup_observability(settings)

    container = Container()

    # ---- DB ----
    db_settings = DatabaseSettings()
    engine = create_engine(db_settings)
    session_factory = make_session_factory(engine)
    container.register_instance(SessionFactory, session_factory)

    # ---- Mediator ----
    mediator = Mediator(
        container,
        command_behaviors=(LoggingBehavior(), ExceptionTranslationBehavior()),
        query_behaviors=(LoggingBehavior(), ExceptionTranslationBehavior()),
    )
    container.register_instance(Mediator, mediator)

    # ---- Event registry ----
    registry = EventRegistry()
    register_events(registry)
    container.register_instance(EventRegistry, registry)

    # ---- UnitOfWork (scoped) ----
    dispatcher = MediatorEventDispatcher(mediator)
    outbox = DefaultOutboxRecorder()

    def _uow_factory(sf: SessionFactory) -> UnitOfWork:
        return UnitOfWork(session_factory=sf, dispatcher=dispatcher, outbox=outbox)

    container.register_scoped(UnitOfWork, _uow_factory)

    # ---- Register handlers (must come after UnitOfWork/SessionFactory are in DI) ----
    register_handlers(mediator, container)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await engine.dispose()

    app = setup_qx_app(
        container,
        settings,
        metrics=metrics,
        health=health,
        extra_lifespan=lifespan,
    )
    register_routes(app)
    return app


app = build_app()
