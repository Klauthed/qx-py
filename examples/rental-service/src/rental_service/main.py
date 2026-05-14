"""rental-service entrypoint — vertical-slice layout.

Three slices: user · house · rent. Each slice owns its commands, queries,
domain objects, infrastructure adapters, and routes.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from qx.core import QxSettings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI
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
from qx.observability import setup_observability

import rental_service
from rental_service.presentation.house import router as house_router
from rental_service.presentation.rent import router as rent_router
from rental_service.presentation.user import router as user_router


def build_app() -> FastAPI:
    settings = QxSettings(app={"name": "rental-service"})
    metrics, health = setup_observability(settings)
    container = Container()

    db_settings = DatabaseSettings()
    engine = create_engine(db_settings)
    session_factory = make_session_factory(engine)
    container.register_instance(SessionFactory, session_factory)

    mediator = Mediator(
        container,
        command_behaviors=(LoggingBehavior(), ExceptionTranslationBehavior()),
        query_behaviors=(LoggingBehavior(), ExceptionTranslationBehavior()),
    )
    container.register_instance(Mediator, mediator)

    event_registry = EventRegistry()
    container.register_instance(EventRegistry, event_registry)

    dispatcher = MediatorEventDispatcher(mediator)
    outbox = DefaultOutboxRecorder()

    def _uow_factory(sf: SessionFactory) -> UnitOfWork:
        return UnitOfWork(session_factory=sf, dispatcher=dispatcher, outbox=outbox)

    container.register_scoped(UnitOfWork, _uow_factory)

    # Auto-discover all @command_handler / @query_handler across all slices.
    mediator.register_decorated(rental_service)

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

    app.include_router(user_router, prefix="/v1")
    app.include_router(house_router, prefix="/v1")
    app.include_router(rent_router, prefix="/v1")
    return app


app = build_app() if not os.environ.get("PYTEST_CURRENT_TEST") else None
