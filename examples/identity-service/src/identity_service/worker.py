"""identity-service worker.

Consumes integration events from NATS JetStream and dispatches to integration
handlers via the mediator. Run as a separate process:

    python -m identity_service.worker
"""

from __future__ import annotations

import asyncio
import signal

from qx.core import QxSettings
from qx.cqrs import ExceptionTranslationBehavior, LoggingBehavior, Mediator
from qx.db import (
    DatabaseSettings,
    SessionFactory,
    create_engine,
    make_session_factory,
)
from qx.di import Container
from qx.events import EventRegistry, NatsConsumer, NatsSettings, create_nats_connection
from qx.observability import get_logger, setup_observability
from qx.worker import WorkerRuntime

from identity_service.application import register_events, register_handlers


async def main() -> None:
    settings = QxSettings(app={"name": "identity-worker"})
    setup_observability(settings)
    log = get_logger("identity.worker")

    container = Container()
    db = DatabaseSettings()
    engine = create_engine(db)
    sessions = make_session_factory(engine)
    container.register_instance(SessionFactory, sessions)

    mediator = Mediator(
        container,
        command_behaviors=(LoggingBehavior(), ExceptionTranslationBehavior()),
        query_behaviors=(LoggingBehavior(), ExceptionTranslationBehavior()),
    )
    container.register_instance(Mediator, mediator)

    registry = EventRegistry()
    register_events(registry)
    container.register_instance(EventRegistry, registry)

    register_handlers(mediator, container)

    nats_settings = NatsSettings()
    nc = await create_nats_connection(nats_settings)
    consumer = NatsConsumer(
        nc,
        registry,
        stream="IDENTITY",
        durable_name="identity-worker",
        subject_filter="identity.>",
    )

    runtime = WorkerRuntime(container, consumer, registry, mediator, concurrency=4)

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()

    def _shutdown(*_: object) -> None:
        log.info("worker shutdown requested")
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown)

    run_task = asyncio.create_task(runtime.run())
    await stop.wait()
    await runtime.stop()
    await asyncio.wait_for(run_task, timeout=30)
    await nc.close()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
