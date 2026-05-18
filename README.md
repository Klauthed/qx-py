# qx-py

> A Spring-Boot-equivalent Python framework ecosystem for building production-grade backend services.

[![CI](https://github.com/klauthed/qx-py/actions/workflows/ci.yml/badge.svg)](https://github.com/klauthed/qx-py/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-%3E%3D3.14-blue)](https://www.python.org/)
[![uv](https://img.shields.io/badge/packaged%20with-uv-orange)](https://docs.astral.sh/uv/)

21 composable packages covering domain modeling, CQRS/Mediator, event sourcing, sagas, projections, transactional outbox, async SQLAlchemy persistence, NATS JetStream messaging, observability (OTel + Prometheus + structlog), auth, gRPC, search, feature flags, multi-region routing, testing utilities, CLI scaffolding, and devtools. Ships with two reference services demonstrating every concept end to end.

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
| **qx-cqrs** | `Command` / `Query` types, `Mediator` with pipeline behaviors (logging, tracing, metrics, idempotency, exception mapping); `trace_behaviors=True` for per-behavior OTel spans |
| **qx-db** | SQLAlchemy 2 async, generic `Repository[TEntity]`, `UnitOfWork` with outbox routing, cursor + offset pagination, multi-tenancy (RLS / schema / DB-per-tenant), `advisory_lock` / `advisory_xact_lock` context managers |
| **qx-http** | FastAPI envelope, `Inject()` DI bridge, `scope_dep`, `unwrap`, `envelope_success`, Metrics + RequestContext middleware (W3C `traceparent` extraction) |
| **qx-observability** | One-call `setup_observability()` — structlog, OpenTelemetry tracing, Prometheus metrics, health probes |
| **qx-events** | `EventRegistry`, NATS JetStream publisher/consumer, `OutboxRelay` with optional leader election and hash-based sharding |
| **qx-worker** | NATS consumer runtime, ack/nak/drop semantics, signal handling, Dead Letter Queue (persist exhausted messages to `qx_dead_letters`, `qx_worker_dlq_total` counter) |
| **qx-cache** | Redis client, Lua-atomic `IdempotencyStore`, `DistributedLock` |
| **qx-auth** | JWT validation, OIDC discovery, RBAC with wildcards, `PolicyEvaluator`, token-bucket rate limiter, HTTP middleware, token revocation |
| **qx-grpc** | gRPC server factory, `RequestContext` / Metrics / Exception / `JwtAuth` interceptors; per-method histogram buckets; all 4 handler shapes |
| **qx-search** | OpenSearch async client, `SearchRepository[TDoc]` abstract base, `bulk_index()`, `scroll()` |
| **qx-flags** | `FlagClient`, `InMemoryProvider`, OpenFeature evaluation with `RequestContext` targeting |
| **qx-regions** | `RegionRouter`, `StaticRegionResolver`, `DbRegionResolver`, `RegionRedirectMiddleware` |
| **qx-eventstore** | `EventSourcedAggregate`, `EventStore` (append / load), snapshot support, optimistic-concurrency conflict metrics |
| **qx-saga** | `Saga`, `SagaManager`, `@on`, `@on_timeout`, distributed lock via `lock_factory`, `compensate()` exponential-backoff retry |
| **qx-projections** | `Projection`, `ProjectionRunner`, incremental checkpointing |
| **qx-testing** | testcontainers helpers, `MediatorStub`, `RepositoryStub`, `OutboxAssert`, `InMemorySearchRepository` |
| **qx-cli** | `qx new service`, `generate aggregate/command/query/event/esaggregate`, `dev up/down`, `doctor`, `projections status/rebuild`, `dlq list/replay` |
| **qx-devtools** | Shared ruff / mypy / pre-commit / editorconfig configs via `write_configs()` |
| **qx-py** | Meta-package — installs all 20 packages above in one `pip install qx-py` |

---

## Quick start

### Install

**pip**
```bash
pip install qx-py          # installs all 20 packages in one shot
pip install qx-core qx-cqrs qx-db qx-http   # or pick only what you need
```

**Poetry**
```bash
poetry add qx-py           # all packages
poetry add qx-core qx-cqrs qx-db qx-http    # or pick only what you need
```

**uv** (recommended for new projects)
```bash
uv add qx-py               # all packages
uv add qx-core qx-cqrs qx-db qx-http        # or pick only what you need
```

> **Requirements:** Python 3.14+. Docker is required for integration tests and the local dev stack.

---

### Scaffold your first service

**1. Install the CLI**
```bash
pip install qx-py   # includes qx-cli
# or
poetry add qx-py
# or
uv tool install qx-py
```

**2. Create a new service**
```bash
# Layered layout (single aggregate focus)
qx new service my-svc

# Vertical-slice layout (multiple feature domains)
qx new service my-svc --slices --domain user
```

**3. Add commands, queries, and slices**
```bash
cd my-svc

# Layered
qx generate command CreateUser
qx generate query   GetUser
qx generate aggregate Invoice

# Vertical slices
qx generate command user/CreateUser
qx generate query   user/GetUser
qx generate slice   payment
```

**4. Run your service**
```bash
# With uv (if using uv for the project)
uv run uvicorn my_svc.main:app --reload

# With Poetry
poetry run uvicorn my_svc.main:app --reload

# With pip / virtualenv
uvicorn my_svc.main:app --reload
```

**5. Run tests**
```bash
# uv
uv run pytest -q

# Poetry
poetry run pytest -q

# pip
pytest -q
```

---

### Contributing / developing Qx itself

```bash
git clone https://github.com/klauthed/qx-py.git
cd qx-py
uv sync

# Run all tests
uv run pytest packages/ examples/ -q

# Check CLI
uv run qx version
```

---

## Reference services

### identity-service

[`examples/identity-service/`](examples/identity-service/) is a complete user-registration microservice demonstrating the full vertical slice — HTTP → Command → Domain Aggregate → Repository → UnitOfWork → Outbox → Worker → Integration Event. Includes feature flags, region redirect, JWT middleware, and multi-tenancy E2E tests.

```
src/identity_service/
├── domain/aggregates/user/     # User aggregate + domain events
├── application/
│   ├── commands/               # CreateUser, ChangeEmail handlers
│   └── queries/                # GetUser, ListUsers handlers
├── infrastructure/persistence/ # UserRepository + SQLAlchemy mapping
└── presentation/routes/        # FastAPI routes using Mediator
```

### order-service

[`examples/order-service/`](examples/order-service/) demonstrates the **event-sourced** pattern: `Order` aggregate with append-only event log, `OrderSummaryProjection` read model, and `OrderFulfillmentSaga` (30-min timeout + compensation).

```
src/order_service/
├── domain/aggregates/order/    # EventSourcedAggregate + OrderPlaced/Confirmed/Cancelled
├── application/
│   ├── commands/               # PlaceOrder, ConfirmOrder, CancelOrder
│   └── queries/                # GetOrder (reads from projection)
├── infrastructure/projections/ # OrderSummaryProjection → qx_order_summaries
├── sagas/                      # OrderFulfillmentSaga
└── presentation/routes/        # REST endpoints
```

Both services include integration tests that run against a real Postgres container via testcontainers.

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
