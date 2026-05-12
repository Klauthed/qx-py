# qx-worker

NATS JetStream consumer runtime for Qx integration-event handlers. Pulls messages in configurable batches, deserialises them via `EventRegistry`, dispatches to handlers through the Mediator, and acks/naks based on the handler result.

## What lives here

- **`qx.worker.WorkerRuntime`** — the main consumer loop. Fetches batches concurrently, opens a per-message DI scope, sets `RequestContext` from the event envelope (correlation_id, tenant_id, trace context), calls `mediator.consume_integration()`, and acks on success or naks on failure (letting JetStream handle retry and DLQ semantics via `max_deliver`).

## Usage

```python
from qx.events import EventRegistry, NatsConsumer, NatsSettings, create_nats_connection
from qx.worker import WorkerRuntime
import asyncio, signal

async def main() -> None:
    nc = await create_nats_connection(settings.nats.url)
    consumer = NatsConsumer(nc, settings.nats)

    registry = EventRegistry()
    registry.register(UserRegisteredIntegration)

    worker = WorkerRuntime(
        container=container,
        consumer=consumer,
        registry=registry,
        mediator=mediator,
        concurrency=8,
    )

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, lambda: asyncio.create_task(worker.stop()))

    await worker.run()
```

### Integration event handler (registered via Mediator)

```python
from qx.cqrs import integration_event_handler

@integration_event_handler(UserRegisteredIntegration)
class SendWelcomeEmailHandler:
    async def handle(self, event: UserRegisteredIntegration) -> Result[None]:
        await self._mailer.send_welcome(event.email, event.name)
        return Result.success(None)
```

## Design rules

- **No in-process retry loop** — the worker only decides ack vs nak. JetStream redelivers nak'd messages with exponential backoff up to `max_deliver`; after that, the message lands in the DLQ stream.
- **Concurrent processing** — `concurrency` controls how many messages are processed in parallel per `fetch()` batch using `asyncio.gather`. Each message gets its own DI scope so shared state (sessions, connections) does not leak between concurrent handlers.
- **Observability** — each message processing opens an OTel span linked to the incoming trace context extracted from NATS headers, so the full trace chain (HTTP → outbox relay → worker) is visible in your tracing backend.
- **Graceful shutdown** — `worker.stop()` sets an internal `asyncio.Event`; the loop drains the current batch before exiting.
