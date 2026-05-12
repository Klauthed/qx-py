# V3 Design

V3 expands the framework into the harder territory: long-running workflows,
event-sourced aggregates, multi-region operation, and richer tenancy
controls. None of these are in the current build; this document describes
the intended shape so V1/V2 services don't paint themselves into corners.

If you're building today, you don't need V3 — V1 + V2 cover ~95% of
service requirements. V3 is for the cases where they don't.

---

## qx-saga: process managers and sagas

**Problem.** A use case spans aggregates — possibly across services.
Example: "register user" needs to (a) create the user, (b) provision a
workspace, (c) send a welcome email, (d) bill an onboarding charge. If any
step fails partway, the previous steps must be compensated. The transactional
outbox handles the "publish event reliably" half; the saga handles "react to
events and orchestrate next steps, with rollback discipline."

### Two shapes, picking one

**Choreography** — each service reacts to events without a central
controller. Pros: no single point of failure, services stay autonomous.
Cons: the workflow is implicit, scattered across handlers; debugging
end-to-end requires tracing across services.

**Orchestration** — a single saga manager directs the sequence. Pros:
explicit workflow, single place to read what should happen. Cons: the
manager is a new service, with its own state and failure modes.

V3 will ship orchestration support. Choreography is already possible with
V1 (just emit and react to integration events).

### Likely API

```python
class RegisterUserSaga(Saga):
    """When UserRegistered: provision workspace, send email, bill onboarding.

    On any failure: compensate the steps completed so far.
    """

    state: SagaState = field(default_factory=SagaState)

    @on(UserRegistered)
    async def start(self, ev: UserRegistered) -> None:
        await self.dispatch(ProvisionWorkspaceCommand(user_id=ev.user_id))
        self.state.user_id = ev.user_id

    @on(WorkspaceProvisioned)
    async def workspace_done(self, ev: WorkspaceProvisioned) -> None:
        self.state.workspace_id = ev.workspace_id
        await self.dispatch(SendWelcomeEmailCommand(...))

    @on(EmailSent)
    async def email_done(self, _ev: EmailSent) -> None:
        await self.dispatch(BillOnboardingCommand(...))

    @on(OnboardingBilled)
    async def complete(self, _ev: OnboardingBilled) -> None:
        self.complete()

    # Compensation: if any step fails, the saga manager invokes this.
    async def compensate(self) -> None:
        if self.state.workspace_id:
            await self.dispatch(DeprovisionWorkspaceCommand(...))
```

State lives in a `qx_sagas` table (one row per saga instance, JSONB
state column). The saga manager polls (or subscribes to) events, loads
the matching saga instance, runs the handler, persists the new state.

Timeouts are first-class: `@on_timeout(after=timedelta(hours=24))` fires
if no event has arrived in the timeout window — useful for "send a reminder
if the user hasn't verified email in 24 hours."

### Open questions

- Versioning: when the saga code changes, in-flight instances should
  finish on the *old* code. Standard pattern: include a `version` field in
  the state and dispatch to versioned handler implementations.
- Determinism: should saga handlers be deterministic functions of state +
  event (like Temporal workflows)? Tempting, but it constrains what
  handler code can do.

---

## qx-eventstore: event-sourced aggregates

**Problem.** Some aggregates need full history: regulatory auditing,
time-traveling reads, "show me the state at end of Q3", projections that
need to be re-derivable from scratch.

V3 will ship an optional event-store backend:

- Aggregates are persisted as their *events* (in `aggregate_events`), not
  their current state.
- Reading the aggregate replays its events from disk, applying each via
  `aggregate.apply(event)`.
- A snapshot table holds periodic snapshots so replay doesn't start from
  the beginning every time.
- The outbox is still in play — integration events still flow through it.

### Storage choice

Two options under consideration:

- **Postgres with `aggregate_events` table** — simple, ACID, integrates
  with the existing outbox transaction. Replay performance depends on
  indexing + snapshots; sufficient up to ~1M events per aggregate.
- **EventStoreDB / Kurrent** — purpose-built event log, better replay
  throughput, harder to operate. V3 will support pluggable backends; one
  table-based default and a contract for swapping.

### Likely API

```python
@event_sourced
class Account(EventSourcedAggregate[Identifier]):
    balance: int = 0

    def apply_money_deposited(self, ev: MoneyDeposited) -> None:
        self.balance += ev.amount

    def apply_money_withdrawn(self, ev: MoneyWithdrawn) -> None:
        self.balance -= ev.amount

    def deposit(self, amount: int) -> None:
        self.record_event(MoneyDeposited(amount=amount))

    def withdraw(self, amount: int) -> Result[None]:
        if self.balance < amount:
            return Result.failure(DomainError(code="insufficient_funds", ...))
        self.record_event(MoneyWithdrawn(amount=amount))
        return Result.success(None)
```

Methods record events but don't directly mutate state — the `apply_*`
methods are the only state mutators. This is the standard event-sourcing
pattern and makes replay trivial: load events, call `apply_*` for each,
done.

### Coexistence with state-storage

Services pick per-aggregate. The framework doesn't force a choice. Most
aggregates stay state-stored; a few critical ones (audit, billing ledger)
go event-sourced. The repository abstraction hides the difference from
callers.

---

## Multi-region

V1/V2 assumes a single region. V3 will support active/active multi-region
with:

- **Tenanted routing.** A `RegionResolver` resolves a tenant_id to its
  authoritative region. HTTP middleware proxies non-local writes to the
  owning region.
- **Cross-region event replication.** NATS JetStream stream mirrors carry
  integration events to other regions. Consumers in each region see all
  events; idempotency keeps them safe.
- **Read replicas per region.** Local read traffic served by local
  Postgres replicas; writes still go to the home region.

The 80% case: each tenant lives in one region; reads can be served locally
everywhere; writes hop to the home region with ~80–200ms added latency.
Acceptable for most workloads.

The 20% case: globally-shared tenants need conflict resolution. CRDTs
help, but they're domain-specific; the framework will support adding them
as custom aggregate types, not provide them generically.

### Failure modes to think about now

- Network partition between regions. Each region must keep serving its
  tenants. Cross-region replication catches up when partition heals.
- Region failover. Promote a replica to authoritative; tenant routing
  table updated; in-flight events replayed.
- Region-local outbox vs global outbox. Each region writes locally; the
  mirror handles propagation. Don't try to make the outbox itself
  multi-region.

---

## Advanced multi-tenancy

V1 stores `tenant_id` as a column. V3 will support physical isolation
options:

### Schema-per-tenant

For compliance-driven workloads (HIPAA, EU data residency by tenant) where
a column-discriminator isn't enough. Each tenant gets its own Postgres
schema; the framework's session factory switches `SET search_path` based
on the active `RequestContext.tenant_id`.

Trade-offs:
- Pros: physical isolation, simpler audit, easier per-tenant exports.
- Cons: alembic migrations need to run per schema (the framework will
  ship a migration runner that fans out across all schemas).

### Database-per-tenant

Same idea, taken further: one database per tenant. Used by some B2B SaaS
companies where tenants are large and few. The connection pool needs to
be tenant-aware.

V3 will provide both as opt-in. Most services stay on shared schema.

---

## Other V3 plumbing

- **Outbox-relay HA.** Today's single-leader pattern works for hundreds of
  events/sec. V3 will support sharded relays (partition by event_name or
  tenant_id) for higher throughput.
- **Cold-start projections.** Tooling to rebuild a read model from the
  event log (or from a Postgres snapshot + the event stream from the last
  snapshot point).
- **Feature flags as a first-class concept.** Currently expected to be
  external; V3 may ship a thin wrapper over Unleash / OpenFeature so
  pipeline behaviors can read flags cleanly.

---

## What V3 will NOT do

- It will not add a built-in GraphQL adapter. Use Strawberry or Hasura on
  top.
- It will not add a workflow engine that replaces Temporal. The saga
  package is for the cases that don't need full Temporal-level guarantees.
- It will not add a CMS-style admin UI. Build a separate admin BFF.
- It will not become a "do everything" platform. The framework's job is
  to make distributed systems boring; everything else is application
  code.
