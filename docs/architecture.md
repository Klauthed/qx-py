# Architecture

This document describes the architecture of services built with the
**qx-python** framework — what the parts are, how they fit together, and
why we made the choices we did.

The framework is opinionated. That's the point: services that adopt it pay
some boilerplate up front in exchange for a consistent operational story
across a fleet, and a development experience where most of the day-2
concerns (observability, transactions, idempotency, retries) are already
solved.

---

## 1. Layered architecture

Every qx service is structured in four layers. The boundaries are
strict; dependencies flow inward only.

```
┌──────────────────────────────────────────────────────────────────┐
│  Presentation  ── FastAPI routes, gRPC stubs, CLI entrypoints   │
│  (qx-http, qx-grpc)                                  │
└──────────────────────────────────────────────────────────────────┘
                ↓
┌──────────────────────────────────────────────────────────────────┐
│  Application  ── Command/Query handlers, pipeline behaviors      │
│  (qx-cqrs)                                                 │
└──────────────────────────────────────────────────────────────────┘
                ↓
┌──────────────────────────────────────────────────────────────────┐
│  Domain  ── Aggregates, entities, value objects, domain events  │
│  (qx-core)                                                 │
└──────────────────────────────────────────────────────────────────┘
                ↑
┌──────────────────────────────────────────────────────────────────┐
│  Infrastructure  ── Repos, outbox, NATS, Redis, observability   │
│  (qx-db, qx-cache, qx-events, ...)            │
└──────────────────────────────────────────────────────────────────┘
```

**Why this order?** The domain layer is the heart of the service —
business rules expressed without any awareness of how they'll be stored or
delivered. Application orchestrates use cases by coordinating domain
objects and infrastructure ports. Presentation translates between wire
formats and application requests. Infrastructure implements the ports
domain/application define.

The arrow from infrastructure points *up* into domain because
infrastructure implements domain-defined interfaces (repository abstract
bases, dispatcher protocols). Domain doesn't know infrastructure exists;
infrastructure knows everything domain exposes.

---

## 2. Request lifecycle

Walking through a single HTTP POST that creates a new user:

```
┌───────────────────────────────────────────────────────────────────┐
│  1. Client → FastAPI route handler                               │
│     ↓ correlation-id minted/extracted                           │
│     ↓ RequestContext opened (contextvars)                       │
│     ↓ OpenTelemetry span started                                │
│                                                                  │
│  2. Route handler builds Command, calls Mediator.send(cmd)      │
│     ↓ pipeline behaviors wrap the handler call                  │
│       ↓ Logging (outermost)                                     │
│       ↓ Metrics                                                  │
│       ↓ Authorization (PolicyEvaluator)                         │
│       ↓ Idempotency check                                       │
│       ↓ Transaction (UnitOfWork opens session + txn)            │
│         ↓ CommandHandler.handle(cmd)                            │
│           ↓ load aggregate via Repository                       │
│           ↓ aggregate.do_something()  (records domain event)    │
│           ↓ Repository.save(aggregate)  → UoW tracks aggregate │
│           ↓ return Result.success(dto)                          │
│         ↑ on success: UoW drains events from aggregates        │
│           ↑ in-process DomainEvent → Mediator.publish          │
│           ↑ IntegrationEvent → Outbox INSERT (same txn)        │
│           ↑ COMMIT                                              │
│       ↑ pipeline exits, releasing resources                    │
│                                                                  │
│  3. Route handler unwrap()s Result → ApiResponse envelope      │
│     ↓ failure paths surface via exception handlers              │
│                                                                  │
│  4. Background: OutboxRelay polls table → publishes to NATS    │
│  5. Background: WorkerRuntime consumes integration events       │
└───────────────────────────────────────────────────────────────────┘
```

Every step is observable: the correlation-id flows end to end and into the
outbox so downstream consumers can correlate.

---

## 3. Why CQRS + Mediator

Commands and queries are separate types. They share no base class
(structurally; mechanically both extend a private `_MessageBase`) and are
dispatched through different handler registries.

Three reasons to keep them separate:

1. **Different cross-cutting concerns apply.** Commands need transactions,
   idempotency, the outbox. Queries need caching, read replicas, response
   shaping. Mixing them forces the pipeline to be too configurable.

2. **Different scaling stories.** Queries often outnumber commands 100:1
   and can be served from caches, read replicas, search indices. Commands
   write through the authoritative store and need ACID guarantees.

3. **Different testing stories.** Command handler tests verify side
   effects (the right events got emitted, the right repository state).
   Query handler tests verify shape (the result matches the expected DTO
   for a given fixture). Treating them uniformly makes both test kinds
   harder to write.

The Mediator pattern (similar to .NET's MediatR or Java Spring's
`ApplicationEventPublisher` style) decouples senders from receivers.
HTTP controllers say "send this command" and don't know which class will
handle it; the handler stays focused on the business logic without
knowing what triggered it.

We support three registration modes — decorator scan (default), explicit,
type-based-auto — so teams can pick what fits their style. None is
"better"; they coexist.

---

## 4. Domain layer: aggregates and events

Aggregates are the consistency boundary. Inside one aggregate, all
invariants hold synchronously. Across aggregates, consistency is eventual
(via integration events).

Two kinds of events:

- **DomainEvent**: in-process, handled inside the same transaction. Used
  for "when X happens, Y must also happen, atomically, in this service."
- **IntegrationEvent**: cross-process, handled via the outbox + broker.
  Used for "when X happens, the rest of the system should eventually
  hear about it."

The aggregate root holds a list of pending events. Mutation methods
record events; the unit-of-work drains them on commit. Aggregates never
publish events directly — that would couple them to infrastructure and
make replay (event sourcing) impossible.

---

## 5. The transactional outbox

The single most important architectural pattern in the framework.

**Problem:** when a command both writes to the DB and publishes a message
to a broker, you have a distributed-systems trap. Either:

- Write DB first, then publish: DB commits, broker call fails, downstream
  never hears about the change.
- Publish first, then write DB: broker delivers, DB write fails,
  downstream sees a phantom event.

**Solution:** the outbox.

1. The command writes to the aggregate table **and** inserts a row into
   `qx_outbox_events` *in the same SQL transaction*. Either both
   commit or both roll back. ACID guarantees this.

2. A separate background process (`OutboxRelay`) polls the outbox table,
   publishes pending rows to NATS, and marks them as published.

The decoupling means the outbox table is the source of truth for "what
happened" — even if NATS is down for an hour, no events are lost. When
NATS comes back, the relay catches up.

Consumers must handle idempotency on their end (events may be
redelivered). The framework supports this through the `IdempotencyStore`
in `qx-cache`, used inside integration event handlers.

---

## 6. Dependency injection

Custom container (`qx-di`) rather than a third-party one. Reasons:

- Full async support including async factories and async disposal.
- Three lifetimes (singleton/scoped/transient) with strict scope discipline.
- Child containers for test overrides.
- No magic: the container introspects `__init__` annotations and resolves;
  nothing else is implicit.

Lifetimes:

- **Singleton**: one instance per container, lazily constructed, shared by all
  resolutions. Engines, settings, the mediator itself.
- **Scoped**: one instance per scope (a scope is opened per HTTP request, per
  message, or per job). Sessions, unit-of-work, request-bound caches.
- **Transient**: a new instance every resolution. Stateless services where
  the cost of a new instance is negligible.

Scoped lifetime is where most of the framework's request-vs-shared
intuition lives. The HTTP middleware opens a scope at request start and
closes it at response end; everything resolved within that request gets
the same instances. The next request gets fresh ones.

---

## 7. Observability

Three signals, all enriched with the same context (correlation_id,
tenant_id, user_id, trace_id, span_id):

- **Logs**: structlog with JSON output in non-dev environments.
- **Metrics**: Prometheus counters/histograms with bounded label cardinality.
- **Traces**: OpenTelemetry spans with OTLP/gRPC export.

The same correlation_id ties together a log line, a metric label, and a
trace span — operators search by correlation_id and see the full story.

Health checks split into liveness (process up?) and readiness (can serve
traffic?), wired into Kubernetes probes via `/healthz` and `/readyz`.

---

## 8. Multi-tenancy

Tenant id flows through `RequestContext`. Repository base class filters
by tenant by default; aggregates carry tenant on creation. Cross-tenant
operations require explicit opt-out (a "system" actor in the context).

No physical isolation in V1 — single database, tenant_id discriminator
column. V3 introduces optional physical isolation (separate schemas /
databases per tenant) for compliance-driven cases.

---

## 9. Boundaries: what the framework does *not* do

- **No business logic.** The framework is a chassis; the engine is yours.
- **No service discovery.** Use Kubernetes service / mesh.
- **No config service.** Use env vars + secret manager.
- **No feature flagging.** Use Unleash / Flagsmith / your platform's own.
- **No saga orchestration in V1.** Coming in V3.
- **No event sourcing storage.** V3 if the demand is there.
- **No GraphQL.** REST + gRPC cover the use cases; GraphQL can sit in
  front via an aggregator BFF.

The framework's job is to make the right thing easy and the wrong thing
hard. It does not aspire to be a turnkey product.

---

## 10. Versioning roadmap

- **V1** (this release): core, di, cqrs, db, cache, events, observability,
  http, worker. Vertical slice from HTTP → handler → repo → outbox →
  worker works end-to-end.
- **V2**: auth (JWT/OIDC/RBAC), grpc, search. Authentication wired into
  the HTTP layer as middleware; gRPC server factory mirrors the HTTP one;
  search repository with OpenSearch backing.
- **V3**: saga orchestration, event sourcing storage, multi-region
  replication, advanced multi-tenancy (physical isolation per tenant).
