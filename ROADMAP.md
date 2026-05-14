# ROADMAP.md

> **How to use:** Mark tasks `[x]` as they are completed. Update the version header with the release date when a version ships.

---

## v0.1.0 — Framework Foundation ✅ Released

Initial release of all 21 packages with core capabilities.

- [x] `qx-core` — `Result[T]`, `Error` hierarchy, `Entity`, `AggregateRoot`, `DomainEvent`, `IntegrationEvent`, `RequestContext`, `QxSettings`, pagination shapes
- [x] `qx-di` — async DI container (SINGLETON / SCOPED / TRANSIENT), child scopes, cycle detection
- [x] `qx-cqrs` — `Command`, `Query`, `Mediator` with pipeline behaviors (logging, tracing, metrics, idempotency, exception mapping)
- [x] `qx-db` — SQLAlchemy 2 async, generic `Repository[TEntity]`, `UnitOfWork` with outbox routing, cursor + offset pagination
- [x] `qx-http` — FastAPI envelope, `Inject()` DI bridge, `scope_dep`, Metrics + RequestContext middleware
- [x] `qx-observability` — `setup_observability()`: structlog, OpenTelemetry tracing, Prometheus metrics, health probes
- [x] `qx-events` — `EventRegistry`, NATS JetStream publisher/consumer, `OutboxRelay` with leader election
- [x] `qx-worker` — NATS consumer runtime, ack/nak/drop semantics, signal handling
- [x] `qx-cache` — Redis client, `IdempotencyStore` (Lua-atomic), `DistributedLock`
- [x] `qx-auth` — JWT validation, OIDC discovery, RBAC, `PolicyEvaluator`, token-bucket rate limiter
- [x] `qx-grpc` — gRPC server factory, `RequestContextInterceptor`, `ExceptionInterceptor`, `MetricsInterceptor`
- [x] `qx-search` — `SearchRepository[TDoc]` abstract base, `OpenSearchRepository`
- [x] `qx-flags` — `FlagClient`, `InMemoryProvider`, OpenFeature evaluation with `RequestContext` targeting
- [x] `qx-regions` — `RegionRouter`, `StaticRegionResolver`, `DbRegionResolver`, `RegionRedirectMiddleware`
- [x] `qx-eventstore` — `EventSourcedAggregate`, `EventStore` (append/load), snapshot support
- [x] `qx-saga` — `Saga`, `SagaManager`, `@on`, `@on_timeout`, durable state in `qx_saga_instances`
- [x] `qx-projections` — `Projection`, `ProjectionRunner`, incremental checkpointing
- [x] `qx-testing` — `RepositoryStub`, `MediatorStub`, `FlagClientStub`, `OutboxAssert`, testcontainers fixtures
- [x] `qx-cli` — `qx new service`, `qx generate aggregate/command/query/event`, `qx dev up/down`, `qx doctor`
- [x] `qx-devtools` — shared ruff / mypy / pre-commit / editorconfig configs via `write_configs()`
- [x] `qx-py` — meta-package installing all 21 packages
- [x] `examples/identity-service` — full vertical slice: HTTP → Command → Aggregate → Repository → UoW → Outbox → Worker
- [x] `deploy/docker-compose.yml` — Postgres, Redis, NATS, Prometheus, Tempo, Grafana, MailHog, MinIO

---

## v0.2.0 — Multi-tenancy, Regions & Release Infrastructure ✅ Released

- [x] `qx-db` multi-tenancy: RLS middleware, schema-per-tenant migration fan-out, database-per-tenant router
- [x] `qx-regions` schema isolation: `TenantSchemaManager`, `open_schema_session`
- [x] `qx-events` sharded outbox relay (hash-based partitioning for HA)
- [x] `qx-auth` HTTP middleware: JWT extraction from request, `RequestContext` population
- [x] Token revocation store in `qx-auth`
- [x] Integration tests for auth middleware
- [x] `examples/identity-service` v3: feature flags (`FlagClient`), region redirect, profile endpoint
- [x] `LLM_GUIDE.md` and `CAPABILITIES.md` documentation
- [x] Version bump scripts (`scripts/version-bump.sh`, `scripts/publish.sh`)
- [x] PyPI release: 7 of 21 packages live (`qx-core`, `qx-auth`, `qx-cache`, `qx-cqrs`, `qx-db`, `qx-cli`, `qx-py`)
- [ ] **BLOCKED** — remaining 14 packages rate-limited by PyPI new-project throttle (retrying hourly)

---

## v0.3.0 — gRPC Depth ✅ Implemented (pending PyPI)

- [x] `qx-grpc` interceptors handle all 4 gRPC handler shapes (unary_unary, unary_stream, stream_unary, stream_stream)
- [x] Deadline propagation: `context.time_remaining()` → `RequestContext.attributes["rpc.deadline_seconds"]`
- [x] `JwtAuthInterceptor` — reads `Authorization: Bearer` from gRPC metadata, stores `Principal` in request context; injected validator keeps qx-grpc decoupled from qx-auth
- [x] `asyncio.CancelledError` re-raised correctly in `ExceptionInterceptor` (not swallowed)
- [x] 22 unit tests covering all shapes, deadline injection, cancellation propagation, JWT auth flows

---

## v0.4.0 — Event-Sourced Reference Service ✅ Implemented (pending PyPI)

- [x] `examples/order-service` — second reference example demonstrating event-sourced pattern
  - [x] `Order` aggregate: `place()`, `confirm()`, `cancel()` commands; `apply_*` as only state mutators
  - [x] `PlaceOrderCommand` / `ConfirmOrderCommand` / `CancelOrderCommand` handlers
  - [x] `OrderSummaryProjection` — incremental read model with idempotent upserts
  - [x] `OrderFulfillmentSaga` — auto-confirm flow with 30-min timeout + cancellation compensation
  - [x] REST endpoints: `POST /orders`, `GET /orders/{id}`, `GET /orders` (paginated)
  - [x] Full composition root: DI wiring, background projection loop
  - [x] Unit tests: PlaceOrderHandler (success, integration event recording, empty items failure)
- [x] Multi-tenancy E2E demo — `test_multitenancy.py`: 12 integration tests covering RLS policy lifecycle, GUC isolation, `SET LOCAL ROLE` enforcement, `Repository` tenant stamping, `TenantSchemaManager` provision/drop/list, and schema-level row isolation; also fixed `TenantSchemaManager.provision()` to use `SET LOCAL search_path` (prevents `create_all` from finding `public` tables and skipping tenant schema creation)
- [x] `qx-search` hardening — `bulk_index()` (default sequential + OpenSearch bulk API override), `scroll()` async generator (default page-based + OpenSearch scroll API override with `finally`-guaranteed context cleanup); `BulkIndexResult` / `BulkIndexError` value types; 11 new tests

---

## v0.5.0 — CLI & Projection Tooling ✅ Implemented (pending PyPI)

- [x] `qx new service` scaffold verification — generated service must pass `uv run pytest` and `uv run mypy` out of the box; fixed `test_smoke.py.j2` (monkeypatched `CollectorRegistry` prevents duplicate Prometheus registration across tests) and `repository.py.j2` (`ClassVar[set[str]]` for `filterable_fields`/`sortable_fields`)
- [x] `qx generate` smoke test — scaffold → generate aggregate + command → pytest + mypy passes end-to-end (12 files, 0 errors)
- [x] `qx projections rebuild <name>` CLI subcommand — resets checkpoint to 0 in `qx_projection_checkpoints`; `--yes` skips prompt; spinner progress; service restart triggers replay
- [x] `qx projections status` — queries `qx_projection_checkpoints` + `qx_aggregate_events` max sequence; shows lag per projection with colour-coded indicators (green=0, yellow<1000, red≥1000)
- [x] `qx doctor` expansion — connectivity checks run by default (skip with `--no-connectivity`); new "Version requirements" section validates installed qx packages against minimum versions; reports mismatches
- [x] Template library: `qx generate esaggregate <Name>` — generates `EventSourcedAggregate` subclass with domain events, integration event, `create()` factory, and `apply_*` stub; no ORM mapping (event store handles persistence)

---

## v0.6.0 — Observability Depth & Benchmarks ✅ Implemented (pending PyPI)

- [x] `qx-observability` trace propagation — extract W3C `traceparent` from HTTP headers in `RequestContextMiddleware` and attach to OTel span
- [x] `qx-observability` span auto-instrumentation for `Mediator` pipeline (each behavior gets its own child span via `trace_behaviors=True`)
- [x] `qx-grpc` `MetricsInterceptor` histogram buckets — configurable per-method latency buckets via `per_method_buckets`
- [x] `qx-worker` consumer lag metric — expose JetStream `num_pending` as a Prometheus gauge (polled every 15s)
- [x] Benchmark suite (`tests/benchmarks/`) — baseline throughput for Mediator dispatch, EventStore append/load, outbox relay throughput
- [x] Grafana dashboard definitions in `deploy/grafana/` — one dashboard per package (worker lag, mediator latency, DB pool, gRPC calls)

---

## v0.7.0 — Resilience & Concurrency Safety ✅ Implemented (pending PyPI)

- [x] `qx-worker` Dead Letter Queue (DLQ) — after N nak retries, move message to `qx_dlq` NATS subject + persist to `qx_dead_letters` table for manual inspection/replay
- [x] `qx-worker` `qx dlq list` / `qx dlq replay <id>` CLI commands — inspect and re-publish dead letters
- [x] `qx-saga` timeout scheduler concurrency safety — `lock_factory` param on `SagaManager`; per-instance distributed lock via `qx-cache` `DistributedLock` (duck-typed, no hard dep)
- [x] `qx-saga` `compensate()` retry — automatic retry with exponential backoff; configurable `compensate_max_attempts` + `compensate_base_delay_seconds`
- [x] `qx-eventstore` optimistic concurrency conflict metrics — `qx_eventstore_version_conflicts_total` Prometheus counter, incremented on every version conflict in `append()`
- [x] `qx-db` advisory lock helper — `advisory_lock(session, key)` (session-level) and `advisory_xact_lock(session, key)` (transaction-level) context managers; `advisory_key(name)` for string-to-bigint derivation

---

## v1.0.0 — Production-Ready Release

**Gate criteria — all must pass before tagging v1.0.0:**

- [ ] All 21 packages live on PyPI (unblocked from rate limiting)
- [x] Test coverage ≥ 80% across all packages (`uv run pytest --cov packages/ --cov-fail-under=80`) — 86% measured
- [x] Zero `mypy --strict` errors across all package source trees (test files excluded) — 88 files, 0 errors
- [x] `uv run ruff check .` exits 0 with no suppressions added after v0.4.0
- [x] `CHANGELOG.md` — one entry per released version with breaking changes highlighted
- [x] `qx new service` smoke test passes in CI (scaffold → test → type-check pipeline)
- [x] All example services (`identity-service`, `order-service`) pass integration tests in CI against real containers
- [x] GitHub Actions release workflow triggers PyPI publish on `v*` tag push
- [x] `README.md` / `BOOTSTRAP.md` / `CAPABILITIES.md` reflect v1.0.0 API surface

---

## Icebox — Future Consideration

Items not yet scheduled into a version:

- `qx-cli` plugin system — third-party `qx-` plugins loadable via entry points
- `qx-search` Elasticsearch backend alongside OpenSearch
- `qx-events` Kafka transport option (alongside NATS JetStream)
- `qx-db` read replica routing — automatic redirect of read-only queries to replica pool
- `qx-auth` API key authentication alongside JWT
- gRPC service reflection + `qx grpc call` interactive CLI
- `examples/payments-service` — saga-heavy example: payment + inventory + fulfilment coordination
- Helm chart for deploying any qx service to Kubernetes
