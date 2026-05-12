# qx-python

A Spring-Boot-equivalent Python framework ecosystem for Qx services.
Fifteen packages covering domain modeling, CQRS with mediator, transactional
outbox, async SQLAlchemy persistence, JetStream messaging, observability,
auth, gRPC, search, testing, CLI scaffolding, and devtools. Ships with a
reference identity service implementing the full vertical slice end to end.

```
HTTP request
   ↓
[FastAPI envelope + middleware]
   ↓
[Mediator pipeline: logging → tracing → metrics → idempotency → exception translation]
   ↓
[Command Handler]
   ↓
[UnitOfWork: aggregate write + outbox INSERT in one transaction]
   ↓ COMMIT
[OutboxRelay]
   ↓ NATS JetStream
[Worker Runtime → Integration Handler]
```

## Status

- **154 tests passing**, 1 deliberately skipped (integration-only path).
- 15 framework packages + 1 reference service.
- Production-grade Docker Compose, K8s manifests, Helm chart.
- Two long-form docs (~16k words combined): architecture/CQRS/deployment
  guides + a junior-to-senior backend engineering handbook.

## Reproducing

```bash
cd qx-python
uv sync
uv run pytest packages/ examples/ -q   # → 154 passed, 1 skipped
uv run qx version                 # → qx-python 0.1.0
uv run qx new service my-svc      # scaffold a new service
```

## Packages

| Package | What it gives you |
|---|---|
| **qx-core** | Result[T], Error hierarchy, Entity/AggregateRoot, DomainEvent/IntegrationEvent, RequestContext (contextvars), pagination, QxSettings |
| **qx-di** | Custom async DI: SINGLETON/SCOPED/TRANSIENT lifetimes, scopes, child containers, override, cycle detection |
| **qx-cqrs** | Command/Query types, Mediator with 3 handler-registration modes (decorator scan, explicit, type-based), pipeline behaviors |
| **qx-observability** | structlog + OpenTelemetry tracing + Prometheus metrics + health checks in one `setup_observability()` call |
| **qx-db** | SQLAlchemy 2 async + imperative mapping + generic `Repository[TEntity]` + UnitOfWork with outbox routing + cursor pagination |
| **qx-cache** | Redis client + Lua-atomic `IdempotencyStore` + `DistributedLock` |
| **qx-events** | EventRegistry + NATS JetStream publisher/consumer + `OutboxRelay` with optional leader election |
| **qx-http** | FastAPI envelope + middleware (RequestContext, Metrics) + `Inject()` DI bridge + probes + `setup_qx_app()` |
| **qx-worker** | NATS consumer runtime with ack/nak/drop semantics + signal handling |
| **qx-auth** | JWT validation + OIDC discovery + RBAC with wildcards + PolicyEvaluator + token-bucket rate limit |
| **qx-grpc** | gRPC server factory + RequestContext/Metrics/Exception interceptors |
| **qx-search** | OpenSearch async client + `SearchRepository[TDoc]` abstract base |
| **qx-testing** | testcontainers helpers + MediatorStub + RepositoryStub + OutboxAssert |
| **qx-cli** | Typer CLI: `qx new service`, `generate aggregate/command/query/event`, `dev up/down` |
| **qx-devtools** | ruff / mypy / pre-commit / editorconfig configs |

## Reference service

`examples/identity-service/` — a complete user-registration service
demonstrating every framework concept. ~10 files of business logic, end-to-end
vertical slice from HTTP to outbox to worker. Read it; it's the fastest way
to learn the framework.

## Deployment

`deploy/docker-compose.yaml` — local development stack (Postgres, Redis, NATS
with JetStream, Prometheus, Tempo, Grafana, MailHog, MinIO).

`deploy/k8s/` — bare-bones K8s manifests (Deployment for API + worker +
outbox-relay, Service, HPA, ConfigMap, ExternalSecret, ServiceMonitor,
pre-deploy Migration Job).

`deploy/helm/qx-service/` — parametric Helm chart for any Qx
service. Production-shaped: rolling deploys, security-hardened pod specs,
External Secrets Operator integration, Prometheus Operator ServiceMonitor,
Alembic migration as a `pre-install/pre-upgrade` Helm hook.

## Documentation

`docs/` contains:

- `architecture.md` — layered design, request lifecycle, CQRS rationale,
  outbox pattern, DI, observability triad, multi-tenancy
- `getting-started.md` — hello-world service in five minutes
- `cqrs-guide.md` — commands vs queries vs events, pipeline ordering,
  anti-patterns
- `deployment.md` — local → Docker → K8s → Helm path
- `v2-design.md` — `qx-auth` / `qx-grpc` / `qx-search`
  scope, integration stories, deferred items
- `v3-design.md` — saga / event-sourcing / multi-region / advanced tenancy
- `Backend_Junior_To_Senior_Engineering.md` — 15,700-word handbook on
  Python runtime, async, types, domain modeling, layers, CQRS, outbox,
  errors-as-values, observability, HTTP, DB, caching, messaging,
  idempotency, auth, multi-tenancy, testing, performance, operations,
  senior trade-offs + 13 appendices

## License

Internal Qx project. Production-grade, extractable; pick what
you need.
