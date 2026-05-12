# qx-py

> A Spring-Boot-equivalent Python framework ecosystem for building production-grade backend services.

[![CI](https://github.com/klauthed/qx-py/actions/workflows/ci.yml/badge.svg)](https://github.com/klauthed/qx-py/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-%3E%3D3.14-blue)](https://www.python.org/)
[![uv](https://img.shields.io/badge/packaged%20with-uv-orange)](https://docs.astral.sh/uv/)

Fifteen composable packages covering domain modeling, CQRS/Mediator, transactional outbox, async SQLAlchemy persistence, NATS JetStream messaging, observability (OTel + Prometheus + structlog), auth, gRPC, search, testing utilities, CLI scaffolding, and devtools. Ships with a reference identity service demonstrating every concept end to end.

---

## Request lifecycle

```
HTTP request
   ↓
[FastAPI envelope + middleware]  ← RequestContext, Metrics
   ↓
[Mediator pipeline]             ← logging → tracing → metrics → idempotency → exception translation
   ↓
[Command / Query Handler]
   ↓
[UnitOfWork]                    ← aggregate write + outbox INSERT in one transaction
   ↓ COMMIT
[OutboxRelay]                   ← publishes to NATS JetStream
   ↓
[Worker Runtime → Integration Handler]
```

---

## Packages

| Package | Description |
|---|---|
| **qx-core** | `Result[T]`, `Error` hierarchy, `Entity` / `AggregateRoot`, `DomainEvent` / `IntegrationEvent`, `Identifier` (UUID v7 default), `RequestContext`, pagination shapes, `QxSettings` |
| **qx-di** | Async DI container — `SINGLETON` / `SCOPED` / `TRANSIENT` lifetimes, child scopes, override, cycle detection |
| **qx-cqrs** | `Command` / `Query` types, `Mediator` with pipeline behaviors (logging, tracing, metrics, idempotency, exception mapping) |
| **qx-db** | SQLAlchemy 2 async, generic `Repository[TEntity]`, `UnitOfWork` with domain-event → outbox routing, cursor + offset pagination |
| **qx-http** | FastAPI envelope, `Inject()` DI bridge, `scope_dep`, `unwrap`, `envelope_success`, Metrics + RequestContext middleware |
| **qx-observability** | One-call `setup_observability()` — structlog, OpenTelemetry tracing, Prometheus metrics, health probes |
| **qx-events** | `EventRegistry`, NATS JetStream publisher/consumer, `OutboxRelay` with optional leader election |
| **qx-worker** | NATS consumer runtime, ack/nak/drop semantics, signal handling |
| **qx-cache** | Redis client, Lua-atomic `IdempotencyStore`, `DistributedLock` |
| **qx-auth** | JWT validation, OIDC discovery, RBAC with wildcards, `PolicyEvaluator`, token-bucket rate limiter |
| **qx-grpc** | gRPC server factory, `RequestContext` / Metrics / Exception interceptors |
| **qx-search** | OpenSearch async client, `SearchRepository[TDoc]` abstract base |
| **qx-testing** | testcontainers helpers, `MediatorStub`, `RepositoryStub`, `OutboxAssert` |
| **qx-cli** | `qx new service`, `generate aggregate/command/query/event`, `dev up/down` |
| **qx-devtools** | Shared ruff / mypy / pre-commit / editorconfig configs |

---

## Quick start

**Prerequisites:** Python 3.14+, [uv](https://docs.astral.sh/uv/), Docker (for integration tests and local stack).

```bash
git clone https://github.com/klauthed/qx-py.git
cd qx-py
uv sync

# Run all tests
uv run pytest packages/ examples/ -q

# Check CLI
uv run qx version

# Scaffold a new service
uv run qx new service my-svc
```

---

## Reference service

[`examples/identity-service/`](examples/identity-service/) is a complete user-registration microservice demonstrating the full vertical slice — HTTP → Command → Domain Aggregate → Repository → UnitOfWork → Outbox → Worker → Integration Event.

```
src/identity_service/
├── domain/aggregates/user/    # User aggregate + domain events
├── application/
│   ├── commands/              # CreateUser, ChangeEmail handlers
│   └── queries/               # GetUser, ListUsers handlers
├── infrastructure/persistence/ # UserRepository + SQLAlchemy mapping
└── presentation/routes/       # FastAPI routes using Mediator
```

Integration tests run against a real Postgres container via testcontainers — see [`tests/integration/`](examples/identity-service/tests/integration/).

---

## Local development stack

```bash
cd deploy
docker compose up -d
# Postgres · Redis · NATS JetStream · Prometheus · Tempo · Grafana · MailHog · MinIO
```

---

## Key design choices

- **`Result[T]` not exceptions** — handlers return `Result.success(value)` or `Result.failure(error)`. HTTP layer translates errors to status codes. No try/except in business logic.
- **UUID v7 by default** — `Identifier.new()` uses UUID v7 (time-ordered, B-tree friendly). Use `Identifier.new_v4()` for non-sequential IDs (tokens, API keys).
- **Optimistic concurrency** — every aggregate has a `version` column; `Repository.save()` uses `WHERE id = ? AND version = ?` and returns `ConflictError` on mismatch.
- **Transactional outbox** — domain events are written to `qx_outbox_events` in the same transaction as the aggregate; `OutboxRelay` polls and publishes. At-least-once delivery, idempotency key on consumers.
- **No magic imports** — DI wiring is explicit; no decorator scanning at startup by default.

---

## Documentation

| Doc | Contents |
|---|---|
| [`docs/architecture.md`](docs/architecture.md) | Layered design, request lifecycle, CQRS rationale, outbox, DI, observability, multi-tenancy |
| [`docs/getting-started.md`](docs/getting-started.md) | Hello-world service in five minutes |
| [`docs/cqrs-guide.md`](docs/cqrs-guide.md) | Commands vs queries vs events, pipeline ordering, anti-patterns |
| [`docs/deployment.md`](docs/deployment.md) | Local → Docker → Kubernetes → Helm |
| [`docs/v2-design.md`](docs/v2-design.md) | `qx-auth` / `qx-grpc` / `qx-search` scope |
| [`docs/v3-design.md`](docs/v3-design.md) | Sagas, event sourcing, multi-region, advanced tenancy |
| [`Backend_Junior_To_Senior_Engineering.md`](Backend_Junior_To_Senior_Engineering.md) | 15,700-word engineering handbook |

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Security

Please report vulnerabilities privately — see [SECURITY.md](.github/SECURITY.md).

## License

[MIT](LICENSE) © 2025 Klauthed
