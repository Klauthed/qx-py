# V2 Design

This document describes the V2 additions to the Qx framework. V1
delivered the core vertical slice (CQRS, outbox, HTTP, worker). V2 fills
in security, additional transports, and search.

The packages exist in the workspace at V2 maturity — usable, tested, but
expect refinement as more services adopt them. Production deployments
should pin specific versions.

---

## qx-auth

**Status:** V2 ready.

Provides:

- **JWT validation** against an OIDC-compliant issuer.
  - RS256 / ES256 supported by default; algorithm allowlist configurable.
  - JWKS keys fetched and cached with TTL.
  - Optional revocation hook for JTI deny-lists.
- **OIDC discovery client** with cached `.well-known/openid-configuration`.
- **RBAC primitives**: dotted permissions (`billing.invoice.refund`) with
  wildcard support (`billing.*`).
- **Policy evaluator**: composable `Allow` / `Deny` rules over
  `(Principal, Resource)`.
- **Rate limiting**: Lua-atomic Redis token-bucket per `(scope, key)`.

### Integration story

The JWT validator runs as an HTTP middleware (planned for V2.1; currently
callers wire it as a FastAPI dependency). On successful validation it
attaches the resolved `Principal` to the `RequestContext` (`actor_id`,
`tenant_id`, `roles`, `permissions`). Downstream handlers read from context
rather than parsing headers themselves.

A future pipeline behavior `AuthorizationBehavior` will accept a list of
required permissions (or a `Policy`) on each command/query and reject
before the handler runs.

### Known gaps

- No first-class authorization-code flow client. Browser flows go through
  the hosted-UI frontend (separate repo); backends only validate the
  resulting tokens. If a backend needs to initiate flows (admin tooling,
  console BFFs), that's V3 work.
- No built-in mTLS support. Add via service-mesh sidecar (Istio, Linkerd).
- No SCIM provisioning. Out of scope; provision via API.

---

## qx-grpc

**Status:** V2 ready, opinionated minimal surface.

What's there:

- `create_grpc_server(container, *, metrics, ...)` factory that returns a
  configured `grpc.aio.Server` with framework interceptors installed.
- **RequestContextInterceptor**: synthesizes/extracts correlation ids from
  metadata, opens a `RequestContext`.
- **MetricsInterceptor**: increments `qx_http_request_*` counters
  (reusing the HTTP labels with `method=GRPC` for unified dashboards).
- **ExceptionInterceptor**: translates `Error` instances to
  `google.rpc.Status` with an `ErrorInfo` detail.
- `status_from_error(err)` for callers that need to emit framework-shaped
  errors from handler code directly.

### Service-implementation pattern

Generate stubs from `.proto` files with `grpcio-tools`. The service class is
a normal Python class that subclasses the generated `*Servicer`. Inject
dependencies via the constructor; resolve through DI when adding the
servicer to the server.

The framework deliberately does **not** ship a code generator from
`Command` types to `.proto` messages — that's seductive but couples the
domain to a wire format. Keep `.proto` files hand-written and translate at
the boundary.

### Known gaps

- No client-side helpers (interceptor for outbound correlation-id
  propagation). V3 will ship a `GrpcClient` wrapper similar to the
  outbound HTTP wrapper.
- No reflection registration helper. Easy to add manually with
  `grpc_reflection`.

---

## qx-search

**Status:** V2 scaffold; backed by OpenSearch async client (pinned to
`opensearch-py[async]>=2.7,<3` because 3.x dropped sync/async parity).

What's there:

- `SearchSettings` with `QX_SEARCH__*` env support.
- `create_search_client(settings)` returning a pooled `AsyncOpenSearch`.
- `SearchRepository[TDoc]` abstract base mirroring `qx.db.Repository`
  shape: `index`, `delete`, `search`.
- `SearchQuery` / `SearchHit` value types.
- `ensure_index`, `drop_index` lifecycle helpers (dev-only — production
  schemas managed declaratively).

### Integration story

Services that need search are CQRS-natural:
- Commands write to the authoritative store (Postgres) + emit integration
  events.
- A `projection` worker consumes those events and updates the search
  index.
- Query handlers backed by `SearchRepository` read from the index.

This separation keeps the write path simple (no dual writes to two stores
inside the same transaction) and the search index is eventually consistent
with the source of truth — acceptable for almost every search use case.

### Known gaps

- No concrete `OpenSearchRepository` implementation yet. Services that
  need search write their own subclass. V2.1 will ship a generic
  implementation.
- No reindex helpers. Use OpenSearch's reindex API directly via the raw
  client.
- No suggester / aggregation helpers.

---

## Things we considered and deferred

### Saga orchestration

Multi-step workflows across aggregates (and services) need an orchestrator.
We considered a built-in saga state machine but deferred for two reasons:

1. The use cases divide into two camps: pure compensation
   ("if step 3 fails, undo step 1 and 2") and process-style ("wait for
   external event, then continue"). One library does both poorly.
2. The integration with the outbox + worker + idempotency stack is
   architecturally fine but operationally heavy (state stores, versioning,
   replay).

V3 will pick one of:
- A lightweight pattern guide ("how to implement a saga with the existing
  primitives") + an example service.
- A first-class `qx-saga` package with a process-manager DSL.

### Event sourcing

We considered making aggregates rebuildable from their event stream. This
is a real architectural option but it's a major commitment — once an
aggregate is event-sourced, you can't trivially go back. We default to
state-storage (Postgres rows) with the outbox for outbound events; this
covers the 90% case and stays simple.

V3 will introduce an optional `qx-eventstore` for services that
genuinely benefit (audit-heavy, time-traveling read models, regulatory
replay).

### Multi-region

V2 services run in a single region. Multi-region needs:
- Tenanted database routing (which region owns this tenant's data?)
- Cross-region event replication
- Eventual consistency for reads with region-local replicas

The pieces exist (Postgres logical replication, NATS leaf nodes, JetStream
mirrors) but the framework doesn't wrap them yet. V3.
