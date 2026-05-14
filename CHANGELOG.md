# Changelog

All notable changes to qx-py are documented here.

Format: `[version] — date` / `Added` · `Changed` · `Fixed` · `Breaking`.

---

## [0.7.0] — 2026-05-14

### Added
- **qx-worker** — Dead Letter Queue: after `max_deliver` naks, persists exhausted messages to `qx_dead_letters` Postgres table and publishes to `qx_dlq.<event_name>` NATS subject. `WorkerRuntime` accepts `dlq: DeadLetterStore | None`. `qx_worker_dlq_total` Prometheus counter.
- **qx-cli** — `qx dlq list` / `qx dlq replay <id>`: inspect and re-publish dead letters from the CLI.
- **qx-saga** — `lock_factory` parameter on `SagaManager`: duck-typed distributed lock (compatible with `qx-cache` `DistributedLock`) prevents duplicate timeout firings under concurrent workers.
- **qx-saga** — `compensate()` exponential-backoff retry: configurable `compensate_max_attempts` (default 3) and `compensate_base_delay_seconds` (default 0.1).
- **qx-eventstore** — `qx_eventstore_version_conflicts_total{aggregate_type}` Prometheus counter incremented on optimistic-concurrency violations in `EventStore.append()`.
- **qx-db** — `advisory_lock(session, key)` (session-level) and `advisory_xact_lock(session, key)` (transaction-level) async context managers. `advisory_key(name)` derives a stable `bigint` from a string via SHA-256.

### Fixed
- **qx-auth** — `RevocationStore.revoke()` used wrong keyword argument (`ttl` → `ttl_seconds`); fixed and aligned with `Cache.set_json` signature.
- **examples/order-service** — Routes imported non-existent `raise_for_error`; replaced with `unwrap()`. `PlaceOrderHandler` propagated failures incorrectly.

---

## [0.6.0] — 2026-05-14

### Added
- **qx-http** — W3C `traceparent` extraction in `RequestContextMiddleware`: populates `RequestContext.trace_id` from the incoming header, enabling distributed trace correlation across services.
- **qx-cqrs** — `Mediator(trace_behaviors=True)`: wraps each pipeline behavior in a child OpenTelemetry span. Lazy import — no hard dependency on `opentelemetry-sdk`.
- **qx-grpc** — `MetricsInterceptor(per_method_buckets={...})`: per-method Prometheus latency histograms with configurable bucket boundaries. Cached after first call per method.
- **qx-events** — `NatsConsumer.num_pending()`: returns JetStream `num_pending` for the durable consumer (used as a Prometheus gauge polled every 15 s).

---

## [0.5.0] — 2026-05-14

### Added
- **qx-cli** — `qx projections rebuild <name>`: resets a projection checkpoint to 0, triggering full replay on next service start. Accepts `--yes` to skip confirmation prompt.
- **qx-cli** — `qx projections status`: displays per-projection checkpoint vs. maximum event sequence with colour-coded lag indicators.
- **qx-cli** — `qx doctor` expansion: connectivity checks (Postgres, Redis, NATS) enabled by default; `--no-connectivity` skips them. New "Version requirements" section validates installed qx package versions.
- **qx-cli** — `qx generate esaggregate <Name>`: scaffold an `EventSourcedAggregate` subclass with domain events, `create()` factory, and `apply_*` stubs.
- **qx-cli** — `qx new service` scaffold verification: generated service passes `uv run pytest` and `uv run mypy` out of the box.

---

## [0.4.0] — 2026-05-13

### Added
- **examples/order-service** — Second reference example demonstrating the event-sourced pattern: `Order` aggregate (`place`, `confirm`, `cancel`), `OrderSummaryProjection`, `OrderFulfillmentSaga` (30-min timeout + compensation), REST endpoints, and full DI composition root.
- **qx-db** — Multi-tenancy E2E: 12 integration tests covering RLS policy lifecycle, GUC isolation, `SET LOCAL ROLE` enforcement, `Repository` tenant stamping, `TenantSchemaManager` provision/drop/list, and schema-level row isolation.
- **qx-search** — `bulk_index()` (sequential default + OpenSearch bulk API override). `scroll()` async generator (page-based default + OpenSearch scroll API with `finally`-guaranteed cleanup). `BulkIndexResult` / `BulkIndexError` value types.

### Fixed
- `TenantSchemaManager.provision()` used `SET search_path` globally; changed to `SET LOCAL search_path` to prevent `create_all` from finding `public` tables and skipping tenant schema creation.

---

## [0.3.0] — 2026-05-12

### Added
- **qx-grpc** — All four gRPC handler shapes (unary_unary, unary_stream, stream_unary, stream_stream) handled by all interceptors.
- **qx-grpc** — Deadline propagation: `context.time_remaining()` → `RequestContext.attributes["rpc.deadline_seconds"]`.
- **qx-grpc** — `JwtAuthInterceptor`: reads `Authorization: Bearer` from gRPC metadata, stores `Principal` in request context. Validator is injected, keeping `qx-grpc` decoupled from `qx-auth`.
- **qx-grpc** — `asyncio.CancelledError` re-raised correctly in `ExceptionInterceptor` (previously swallowed).

---

## [0.2.0] — 2026-05-12

### Added
- **qx-db** — Multi-tenancy: Row-Level Security middleware (`RlsPolicyManager`, `open_rls_session`), schema-per-tenant migration fan-out (`TenantSchemaManager`), database-per-tenant router (`TenantDatabaseManager` / `TenantEngineRouter`).
- **qx-events** — Sharded outbox relay: hash-based partitioning for high-availability outbox processing.
- **qx-auth** — HTTP middleware: JWT extraction from request, `RequestContext` population with `Principal`.
- **qx-auth** — Token revocation store (`RedisRevocationStore`) backed by `qx-cache`.
- **examples/identity-service v3** — Feature flags (`FlagClient`), region redirect, profile endpoint.
- **scripts** — `version-bump.sh` and `publish.sh` for release automation.

### Changed
- All packages bumped from 0.1.0 → 0.2.0.

---

## [0.1.0] — 2026-05-11

Initial release of all 21 packages.

### Added
- **qx-core** — `Result[T]`, `Error` hierarchy, `Entity`, `AggregateRoot`, `DomainEvent`, `IntegrationEvent`, `RequestContext`, `QxSettings`, pagination shapes.
- **qx-di** — Async DI container (SINGLETON / SCOPED / TRANSIENT), child scopes, cycle detection.
- **qx-cqrs** — `Command`, `Query`, `Mediator` with pipeline behaviors (logging, tracing, metrics, idempotency, exception mapping).
- **qx-db** — SQLAlchemy 2 async, generic `Repository[TEntity]`, `UnitOfWork` with outbox routing, cursor + offset pagination.
- **qx-http** — FastAPI envelope, `Inject()` DI bridge, `scope_dep`, Metrics + RequestContext middleware.
- **qx-observability** — `setup_observability()`: structlog, OpenTelemetry tracing, Prometheus metrics, health probes.
- **qx-events** — `EventRegistry`, NATS JetStream publisher/consumer, `OutboxRelay` with leader election.
- **qx-worker** — NATS consumer runtime, ack/nak/drop semantics, signal handling.
- **qx-cache** — Redis client, `IdempotencyStore` (Lua-atomic), `DistributedLock`.
- **qx-auth** — JWT validation, OIDC discovery, RBAC, `PolicyEvaluator`, token-bucket rate limiter.
- **qx-grpc** — gRPC server factory, `RequestContextInterceptor`, `ExceptionInterceptor`, `MetricsInterceptor`.
- **qx-search** — `SearchRepository[TDoc]` abstract base, `OpenSearchRepository`.
- **qx-flags** — `FlagClient`, `InMemoryProvider`, OpenFeature evaluation with `RequestContext` targeting.
- **qx-regions** — `RegionRouter`, `StaticRegionResolver`, `DbRegionResolver`, `RegionRedirectMiddleware`.
- **qx-eventstore** — `EventSourcedAggregate`, `EventStore` (append/load), snapshot support.
- **qx-saga** — `Saga`, `SagaManager`, `@on`, `@on_timeout`, durable state in `qx_saga_instances`.
- **qx-projections** — `Projection`, `ProjectionRunner`, incremental checkpointing.
- **qx-testing** — `RepositoryStub`, `MediatorStub`, `FlagClientStub`, `OutboxAssert`, testcontainers fixtures.
- **qx-cli** — `qx new service`, `qx generate aggregate/command/query/event`, `qx dev up/down`, `qx doctor`.
- **qx-devtools** — Shared ruff / mypy / pre-commit / editorconfig configs via `write_configs()`.
- **qx-py** — Meta-package installing all 21 packages.
- **examples/identity-service** — Full vertical slice: HTTP → Command → Aggregate → Repository → UoW → Outbox → Worker.
- **deploy/docker-compose.yml** — Postgres, Redis, NATS, Prometheus, Tempo, Grafana, MailHog, MinIO.
