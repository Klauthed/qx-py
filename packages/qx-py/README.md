# qx-py

Full-stack Qx framework — installs all 20 sub-packages in one command.

```bash
pip install qx-py
```

## What's included

| Package | Purpose |
|---|---|
| `qx-core` | Result, Error, Entity, AggregateRoot, RequestContext |
| `qx-di` | Async DI container (SINGLETON / SCOPED / TRANSIENT) |
| `qx-cqrs` | Command / Query mediator + pipeline behaviors |
| `qx-db` | SQLAlchemy 2 async, Repository, UnitOfWork, outbox |
| `qx-cache` | Redis client, IdempotencyStore, DistributedLock |
| `qx-events` | NATS JetStream publisher/consumer, OutboxRelay |
| `qx-http` | FastAPI envelope, middleware, DI bridge, health probes |
| `qx-worker` | NATS consumer runtime with ack / nak / drop |
| `qx-observability` | structlog, OpenTelemetry, Prometheus |
| `qx-auth` | JWT, OIDC, RBAC, rate limiting |
| `qx-grpc` | gRPC server factory + interceptors |
| `qx-search` | OpenSearch async client + SearchRepository |
| `qx-saga` | Process managers / orchestrated sagas with compensation |
| `qx-eventstore` | Event-sourced aggregates with snapshot support |
| `qx-projections` | Incremental read-model projections from the event stream |
| `qx-flags` | Feature flags via OpenFeature |
| `qx-regions` | Multi-region tenant routing and cross-region event replication |
| `qx-testing` | testcontainers helpers, MediatorStub, OutboxAssert |
| `qx-cli` | `qx` CLI: scaffold service, generate aggregates/commands/queries |
| `qx-devtools` | Shared ruff / mypy / pre-commit configs |

## Cherry-picking

Each package is independently installable. For a lightweight service
that only needs the core CQRS + HTTP stack:

```bash
pip install qx-core qx-di qx-cqrs qx-db qx-http qx-observability
```

See each package's README for its own dependency requirements.
