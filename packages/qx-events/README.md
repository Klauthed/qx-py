# qx-events

NATS JetStream publisher/consumer, transactional outbox relay, and mediator-bridge event dispatcher for the Qx framework.

## What lives here

- **`qx.events.OutboxRelay`** — polls `qx_outbox_events`, publishes unpublished events to NATS JetStream, and marks them delivered. Runs as a background task alongside the HTTP server or as a standalone process. Supports optional leader election so only one relay instance publishes at a time.
- **`qx.events.NatsPublisher`** — publishes a single `IntegrationEvent` to a JetStream subject derived from `event_name`.
- **`qx.events.NatsConsumer`** — durable pull consumer over a JetStream stream. Used by `WorkerRuntime` to fetch and ack/nak messages.
- **`qx.events.NatsSettings`** — Pydantic settings for NATS connection (URL, credentials, stream name, consumer name).
- **`qx.events.EventRegistry`** — maps `event_name` strings to concrete `IntegrationEvent` subclasses. Required by both the relay (for serialisation) and the worker (for deserialisation).
- **`qx.events.MediatorEventDispatcher`** — `EventDispatcher` implementation that routes domain events to their in-process handlers via the Mediator after a UnitOfWork commit.
- **`qx.events.create_nats_connection`** — async factory that opens a NATS connection with retry.

## Usage

### Outbox relay (alongside the API server)

```python
from qx.events import OutboxRelay, NatsPublisher, NatsSettings, EventRegistry

registry = EventRegistry()
registry.register(UserRegisteredIntegration)

publisher = NatsPublisher(nc, registry)
relay = OutboxRelay(session_factory, publisher, registry)

# Run in background
asyncio.create_task(relay.run())
```

### Consuming events in a worker

```python
from qx.events import NatsConsumer, NatsSettings

settings = NatsSettings(url="nats://localhost:4222", stream="events", consumer="identity-worker")
consumer = NatsConsumer(nc, settings)

# WorkerRuntime handles the fetch/ack loop
worker = WorkerRuntime(container, consumer, registry, mediator)
await worker.run()
```

## Design rules

- **At-least-once delivery** — the outbox guarantees every `INSERT`ed event is eventually published. Consumers are expected to be idempotent (use `IdempotencyStore` from `qx-cache`).
- **Transactional outbox** — `UnitOfWork` (in `qx-db`) writes events to `qx_outbox_events` in the same transaction as the aggregate; the relay reads and publishes asynchronously. No event is lost even if the process crashes between commit and publish.
- **Event envelope** — each NATS message carries `event_name`, `event_version`, payload JSON, `correlation_id`, `tenant_id`, and OTel trace context headers for full observability continuity.
