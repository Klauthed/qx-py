# Backend Engineering: Junior to Senior

A practical handbook for engineers building production backend services.
Written against the Qx framework conventions, but most of the
material is transferable — the patterns predate Qx and outlive any
specific framework choice.

This is opinionated. Where there are trade-offs, the recommended choice is
stated explicitly and the alternative is named. You're free to disagree;
disagreement is healthy when it's informed.

---

## Table of contents

1. The Python runtime and how it actually runs
2. Async/await: what it is and what it isn't
3. Types as a design tool
4. Domain modeling: entities, value objects, aggregates
5. Application architecture: layers and the dependency rule
6. CQRS without ceremony
7. The transactional outbox: the one pattern to learn well
8. Errors as values
9. Logging, metrics, traces
10. HTTP design
11. Database concerns: pools, transactions, migrations
12. Caching: what to cache, what not to
13. Messaging: NATS, JetStream, at-least-once semantics
14. Idempotency
15. Authentication, authorization, multi-tenancy
16. Testing strategy
17. Performance and the tools to measure it
18. Operating services
19. Senior-level trade-offs

The volume is intentional. Read in order if you're early-career; skim by
section heading if you already know the basics and want the framework
opinions.

---

## 1. The Python runtime and how it actually runs

Python is interpreted, dynamically typed, reference-counted with a cyclic
GC, and has a Global Interpreter Lock. Each of these matters when you're
debugging production issues, even if you never think about them when writing
business logic.

**Interpreted.** Bytecode runs in a stack-based VM (CPython). The startup
overhead is real: a fresh `python -c "pass"` is ~30ms on a fast machine. For
long-running processes (services), startup cost is amortized; for CLI tools
that get invoked frequently, it's noticeable. Don't reach for Python when
something needs to start in 5ms.

**Dynamic typing with hints.** Type hints (`x: int`) are runtime-introspectable
metadata, not runtime checks. Mypy / pyright run statically at build time. At
runtime your code happily passes a `str` where an `int` is hinted; you discover
the bug when something downstream chokes. Type hints earn their keep through
the tooling around them (mypy, IDE refactors, autocomplete, pydantic) — not
through the runtime semantics.

**Reference counting + cyclic GC.** Most objects die immediately when their
last reference drops. The cyclic GC mops up reference cycles periodically.
Two implications:

- File handles, sockets, database connections close *deterministically* in
  most Python code when their owning object goes out of scope. This is why
  `with` blocks are idiomatic — they pin the scope.
- Cycles between objects with `__del__` methods used to be problematic
  (uncollectable cycles). Modern CPython handles this fine; don't avoid
  `__del__`, but understand `weakref` if your design has natural cycles.

**The GIL.** Only one Python thread executes Python bytecode at a time. C
extensions can release the GIL (numpy, asyncpg's underlying I/O paths do).
**Implication**: pure-Python CPU-bound code does not scale with threads.
For CPU-bound work use `concurrent.futures.ProcessPoolExecutor` or rewrite
the hot path in something that releases the GIL (numpy / Cython / Rust
extensions via PyO3).

For I/O-bound work — almost all services — async/await is what you want.
A single Python thread happily handles thousands of concurrent async
requests because the GIL is only contended when the thread is actually
executing Python bytecode; while it's waiting on a socket, other coroutines
on the same thread run.

**Python 3.13** introduced experimental no-GIL builds. By Python 3.15
they'll likely stabilize. Don't switch yet for production services — the
ecosystem hasn't caught up — but know the constraint will eventually go
away.

### Practical things to know

- `python -X dev` enables development mode: more warnings, fewer
  optimizations, useful in CI.
- `sys.intern()` and small-integer caching mean some `is` comparisons
  work that mathematically shouldn't. Don't rely on them; `==` for
  equality, `is` for identity (None, True, False, sentinel singletons).
- `asyncio.run()` creates a new event loop each call. Don't call it
  twice in the same process; reuse the loop or use `asyncio.run` once at
  the entrypoint.
- Memory layout: classes have a per-instance `__dict__` by default.
  Aggregates with many instances benefit from `__slots__` for both speed
  and memory. The framework's `@dataclass(slots=True)` does this.

---

## 2. Async/await: what it is and what it isn't

Async/await is **cooperative concurrency** within a single thread. Each
`await` point is an explicit "I might give up control here." When you write
`await foo()`, control returns to the event loop, which picks the next
ready coroutine and runs it until *its* next `await`.

This is fundamentally different from threads:

- No preemption. If a coroutine never `await`s, it monopolizes the loop.
- No shared-state races *within a single coroutine* — execution is linear
  between `await` points. Between two coroutines, however, races can
  happen at `await` boundaries.
- I/O is the main use case. CPU-bound work blocks the loop and starves
  every other coroutine.

### When async pays off

Async pays off when your service is dominated by I/O wait — database
calls, HTTP calls, message broker calls. A synchronous service spends
most of its wall time blocked on I/O; an async service interleaves
hundreds of in-flight I/O operations on one thread.

A typical HTTP handler in a CQRS service:

```python
async def create_user(cmd: CreateUserCommand, ...):
    async with uow:
        existing = await repo.find_by_email(cmd.email)  # await: DB roundtrip
        # ...
        await repo.add(user)                            # await: DB roundtrip
        await uow.commit()                              # await: DB roundtrip + outbox write
```

Each `await` is a real network call. While this coroutine waits, the loop
runs other coroutines that are also waiting on DB. The throughput of the
service is bounded by the database, not by the Python process.

### When async hurts

- CPU-bound code in the request path. A loop doing 100ms of pure Python
  blocks every other request for that whole 100ms. Move it to a
  `ThreadPoolExecutor` with `loop.run_in_executor` (for code that
  releases the GIL), or a `ProcessPoolExecutor` (for pure-Python CPU
  work).
- Mixing sync and async carelessly. Calling a sync function that blocks
  for 50ms inside an async handler blocks the loop for that whole 50ms.
  If a library only offers a sync API, either wrap it with
  `run_in_executor` or accept the blocking and document it.

### Common mistakes

**Forgetting to `await`.** `result = foo()` where `foo` is an `async def`
returns a coroutine, not the result. The coroutine never runs. Mypy and
ruff catch this with `RUF006` / mypy's `Coroutine` return type. Always
configure your linters strict enough to catch it.

**Using `asyncio.gather` without thinking about failure modes.**

```python
results = await asyncio.gather(fetch_a(), fetch_b(), fetch_c())
```

If any one fails, the others are *not* cancelled by default. The exception
propagates, but the remaining coroutines continue running until they
finish on their own. Use `return_exceptions=True` if you want the
exceptions as values, or `asyncio.TaskGroup` (3.11+) for the structured
"if one fails, cancel the rest" semantics.

```python
async with asyncio.TaskGroup() as tg:
    a = tg.create_task(fetch_a())
    b = tg.create_task(fetch_b())
# If either raises, both are cancelled, and the exception
# group surfaces on exit from the `async with`.
```

The framework uses TaskGroup-style aggregation in the mediator's event
dispatch — failures aggregate into a `BaseExceptionGroup` so partial
failures are visible.

**Blocking calls inside async code.** `time.sleep(1)` is a 1-second loop
freeze. `requests.get(...)` is a synchronous HTTP call that blocks the
loop while the socket waits. Use `asyncio.sleep` and an async HTTP
client (`httpx.AsyncClient`).

**Long-running coroutines without yielding.** A coroutine doing CPU work
in a loop blocks even though it has `async def` in front. If you need
to do CPU work, periodically `await asyncio.sleep(0)` to give the loop
a chance to run other coroutines. Better: move the work off the loop.

### Cancellation

Cancellation is *cooperative*: `task.cancel()` schedules a
`CancelledError` to be raised at the task's next `await`. The task can
catch it and continue, though usually shouldn't.

```python
try:
    await something()
except asyncio.CancelledError:
    # cleanup
    raise
```

The `raise` matters. Suppressing `CancelledError` is almost always a
bug — it prevents the cancellation from propagating, which means the
parent task or the event loop can't shut down cleanly. Catch it only
to do cleanup, then re-raise.

The framework's `WorkerRuntime` handles this correctly: it propagates
`CancelledError` from `asyncio.gather` and uses a structured
`asyncio.Event` for shutdown so handlers can finish their current
message before the process exits.

---

## 3. Types as a design tool

Type hints in Python are *advisory* at runtime but *constraining* at
build time when you run mypy or pyright. Treat them as part of the
design, not as documentation tacked on after the fact.

### Three levels of type discipline

**Level 1: function signatures.** Every function parameter and return
value is annotated. Mypy strict mode enforces this. No `Any` leaks into
your code from third-party libraries without an explicit `# type: ignore`
comment. This level is non-negotiable; everything below assumes you've
already got it.

**Level 2: domain types.** Beyond primitives. A `UserId` is not a
`UUID` is not a `str`. The framework's `Identifier` type wraps a UUID,
and you subclass it (`UserId(Identifier)`) for each aggregate. Functions
that take a `UserId` *cannot* accidentally receive an `OrderId`.

```python
@dataclass(frozen=True)
class UserId(Identifier):
    pass

@dataclass(frozen=True)
class OrderId(Identifier):
    pass

def grant_admin(user: UserId) -> None: ...

grant_admin(some_order_id)  # mypy: error: incompatible type
```

This catches whole categories of bugs at lint time. Cost: a few extra
classes. Worth it.

**Level 3: parse, don't validate.** Make illegal states unrepresentable
in the type system. A `NonEmptyString` is a different type from `str`.
A `ValidatedEmail` is constructed only by a function that's checked it.
After construction, the type guarantees the constraint — the rest of the
code doesn't need to re-check.

```python
class ValidatedEmail:
    def __init__(self, raw: str) -> None:
        if "@" not in raw:
            raise ValueError(f"invalid email: {raw!r}")
        self._value = raw.lower().strip()

    @property
    def value(self) -> str:
        return self._value
```

Pydantic `BaseModel` does much of this for you when validating commands
and queries: by the time a `CreateUserCommand` exists, every field has
already been validated.

### Generics

When the framework defines `Repository[TEntity]`, the type parameter
gives you typed get/save/list without per-aggregate boilerplate. Your
repository code is correct or it doesn't compile.

`TypeVar`, `Generic`, `Protocol`, `runtime_checkable` — learn them. They're
how you get the rest of static-typed-language ergonomics in Python.

`ParamSpec` (3.10+) is the missing piece for typed decorators. It lets a
decorator preserve the wrapped function's signature in the type checker's
view. Use it when you write decorators; otherwise the wrapped function
appears as `Callable[..., Any]` everywhere it's called.

```python
P = ParamSpec("P")
R = TypeVar("R")

def with_retry(f: Callable[P, R]) -> Callable[P, R]:
    @functools.wraps(f)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        ...
    return wrapper
```

### Pydantic

Pydantic v2 (the rewrite in Rust under the hood) is the framework's
default for validation. Use it for:

- HTTP request / response bodies.
- Settings (environment-driven configuration).
- Commands and queries (the framework already does this).
- Anywhere you parse JSON from a network into a typed structure.

Use it *less* for:

- Domain aggregates. They're long-lived objects with mutable state;
  Pydantic adds overhead and forces some unnatural patterns. The
  framework uses `@dataclass` for aggregates and `BaseModel` for DTOs
  / commands / queries — that's the right split.

### When type hints lie

There's a long tail of cases where the type system can't express what
you need: dynamic class creation, generic decorators with side-channel
inputs, third-party libraries with poor stubs. Two responses:

1. Fix the types if you can. Submit type-stub PRs upstream; use
   `Protocol` to abstract over an interface you can't import.
2. If you can't, escape with a *narrow* `# type: ignore[specific-code]`
   comment. Never blanket-ignore. The specific code documents exactly
   what's being suppressed; if the upstream gets fixed, the suppression
   becomes a dead comment that ruff catches.

---

## 4. Domain modeling: entities, value objects, aggregates

The terms come from Domain-Driven Design (DDD). You don't need DDD's
full apparatus to write good services, but the vocabulary is useful.

### Entity vs value object

An **entity** has identity. Two entities are equal if their ids match,
even if every other attribute differs. Users, orders, invoices — anything
the business refers to by a stable reference.

A **value object** has no identity. Two value objects are equal if their
attributes match. Addresses, money amounts, date ranges — things that
describe but aren't themselves identifiable.

```python
@aggregate
class User(AggregateRoot[Identifier]):
    email: str = ""
    address: Address  # value object

# Two Address instances with the same fields are interchangeable:
class Address(BaseModel):
    model_config = ConfigDict(frozen=True)
    street: str
    city: str
    country_code: str
```

Value objects should be immutable (`frozen=True`). They're cheap to copy;
changing an address means assigning a *new* one to the user. This avoids
a class of bugs where mutating a shared address from one user surprises
another.

### Aggregates

An **aggregate** is a cluster of entities and value objects with a single
root entity. The root is the only thing the outside world references; the
inside is private. Invariants hold across the entire aggregate.

Why aggregates matter: they're the *consistency boundary*. Inside one
aggregate, you can write multi-step business rules ("if I add a line item,
the order total must update; if the total exceeds the customer's credit
limit, the order is rejected") and trust that they hold at any read
because writes are atomic.

Across aggregates, consistency is *eventual*. Don't try to enforce
cross-aggregate invariants synchronously. Use integration events and
the outbox to propagate; accept that the system will be out of sync for
milliseconds.

Rules of thumb:

- Aggregates should be small. If you're tempted to load 1000 line items
  to update one, the aggregate boundary is wrong; line items might be
  their own aggregate.
- Reference other aggregates by id, not by Python reference. A `User`
  knows its `OrganizationId`, not an `Organization` instance.
- Aggregates emit events but don't publish them. The unit-of-work
  routes events on commit.

### Anemic domain models

The classic anti-pattern: aggregates with public setters and no methods,
all logic living in services that mutate them from outside. Symptoms:

```python
user.email = new_email
user.updated_at = now()
user.audit_log.append(...)
await repo.save(user)
```

Every caller has to remember to update `audit_log` and `updated_at`.
Some forget. Bugs ensue.

The fix: methods on the aggregate.

```python
def change_email(self, new_email: str) -> Result[None]:
    self.email = new_email.lower().strip()
    self.record_event(UserEmailChanged(...))
    return Result.success(None)
```

Now the caller writes `user.change_email(...)` and can't forget anything.
The aggregate enforces its own invariants.

### Factories vs constructors

The constructor (`__init__`) reconstitutes existing aggregates from the
database. Factories (`User.register(...)`, `User.invited_by(...)`)
create new ones with their initial events recorded.

```python
@classmethod
def register(cls, email: str, name: str) -> Result["User"]:
    if "@" not in email:
        return Result.failure(DomainError(code="invalid_email", ...))
    user = cls(id=Identifier(value=uuid4()), email=email, name=name)
    user.record_event(UserRegistered(...))
    return Result.success(user)
```

The factory is the only entry point for new aggregates; it validates
input, sets sensible defaults, records the creation event. Calling the
constructor directly bypasses all of that.

---

## 5. Application architecture: layers and the dependency rule

The framework uses a four-layer architecture:

- **Domain.** Aggregates, value objects, events. No dependencies on
  anything else in the codebase. No I/O, no framework, no SQLAlchemy.
- **Application.** Use cases — command handlers, query handlers,
  application services. Orchestrates domain objects + infrastructure
  via interfaces.
- **Infrastructure.** Adapters — repositories, HTTP clients, NATS
  publishers. Implements interfaces declared by the layers above.
- **Presentation.** FastAPI routes, gRPC servicers, CLI commands.
  Translates wire formats to/from application requests.

**The dependency rule:** dependencies point inward. Domain depends on
nothing. Application depends on domain. Infrastructure implements
interfaces defined by domain or application. Presentation depends on
application.

You enforce this with imports. The domain layer's `__init__.py` doesn't
import from `qx.db` or `qx.http` — if you find yourself
wanting to, you've found a leak. The pattern is: define an interface in
domain/application; implement in infrastructure; inject at the composition
root.

### Why this matters

The dependency rule is what lets you:

- **Test domain logic without infrastructure.** A `User.register()` test
  doesn't need a database, doesn't need a running NATS, doesn't even
  need a mock — it's a pure Python function.
- **Swap infrastructure.** Move from Postgres to Spanner; from NATS to
  Kafka. The domain doesn't know.
- **Reason about business rules in isolation.** When the domain layer
  is small and focused, you can read it end-to-end and convince yourself
  it's correct.

The cost is some up-front discipline. The payoff is years of services
that are still easy to change.

### Layers do *not* mean directories

You can structure a project with `domain/`, `application/`, etc., or by
feature with `users/`, `orders/`, etc. Both work; the framework's CLI
scaffolds the layered version because the boundaries are explicit in
the file layout.

Feature-folder layouts often end up with the same boundary internally
("`users/domain.py`, `users/application.py`, `users/infrastructure.py`"),
which is fine.

### The composition root

There's exactly one place in your service where everything is wired
together: `main.py`. The container is constructed, settings are loaded,
the mediator is created, handlers are registered, the HTTP app is built.

Nothing else should know about the container or settings. Inject the
specific dependencies a class needs; let the composition root assemble
the graph.

This rule has a name (Composition Root pattern) and a discipline. Once
it slips — a handler reaches up to `container.resolve(...)` from inside
its body, or reads `settings.foo` directly — you start to lose the
benefits of DI. Resist.

---

## 6. CQRS without ceremony

Command-Query Responsibility Segregation sounds like a lot of architecture
for "have two methods." In practice it is.

A **command** mutates state and returns a small acknowledgement (id of
what was created, nothing at all). A **query** reads state and returns a
DTO. They go through separate handler registries because they have
different cross-cutting concerns:

- Commands get a transaction. Queries don't (they're read-only).
- Commands get idempotency. Queries get caching.
- Commands write to the authoritative store. Queries might read from
  replicas, search indices, or materialized views.

The framework's mediator gives you the dispatch hub. The discipline is
yours: keep commands focused on a single use case ("register a user", not
"do all the user stuff"), keep queries side-effect-free, don't share
DTOs between command responses and query responses.

### Why not just call handlers directly?

Two reasons.

1. **Pipeline behaviors.** Every command goes through logging,
   metrics, authorization, idempotency, transaction-opening, retry —
   without each handler having to opt in. Pipeline behaviors are the
   cleanest place to put cross-cutting concerns.

2. **Decoupling senders from receivers.** An HTTP route doesn't know
   which class will handle the command. The class can be replaced
   (different impl for different tenants, A/B test variants, feature
   flags) without changing the caller.

The cost is one indirection. The benefits compound.

### Don't over-CQRS

Some teams take CQRS to mean *separate write and read databases*. That's
one possible architecture (the "+Event Sourcing" extreme), but it's not
required and usually overkill. CQRS as we use it just means "different
types for different intentions, different handlers, different pipelines."
Same database is fine.

---

## 7. The transactional outbox: the one pattern to learn well

If you take one pattern from this handbook into your career, take this
one. It's the difference between distributed systems that lose data
under failure and ones that don't.

### The setup

Your service does two things in one logical operation:

1. Writes to the database.
2. Publishes a message to a broker (NATS, Kafka, RabbitMQ).

Naively, you do them in sequence. Either order is broken under failure:

- **Write first, publish second:** if the publish fails, the DB has the
  change but downstream consumers never know. *Lost events.*
- **Publish first, write second:** if the write fails, consumers got
  notified about a change that never happened. *Phantom events.*

There's no version of these two operations that's atomic on its own.
The broker doesn't participate in the database's transaction; the
database doesn't participate in the broker's.

### The pattern

Add a third table:

```sql
CREATE TABLE outbox_events (
  id UUID PRIMARY KEY,
  event_name VARCHAR NOT NULL,
  payload JSONB NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  published_at TIMESTAMPTZ,
  attempts INT DEFAULT 0
);
```

The command writes:

1. The aggregate change.
2. A row in `outbox_events`.

**In the same SQL transaction.** Either both commit or both roll back. ACID
guarantees atomicity.

A separate process — the **outbox relay** — polls the table, publishes
unsent rows to the broker, marks them as sent. If the relay crashes
mid-batch, no problem: on restart it re-publishes whatever's still
marked unsent. Duplicate delivery is OK because consumers must handle
at-least-once anyway.

### Properties you get for free

- **No lost events.** If the transaction committed, the event is in the
  outbox; the relay will eventually publish it.
- **No phantom events.** If the transaction rolled back, the outbox row
  rolled back too; nothing gets published.
- **Replay.** Want to re-process events? `UPDATE outbox_events SET
  published_at = NULL` and the relay re-sends them. (Be careful with
  consumers that aren't idempotent.)
- **Audit trail.** The outbox is a record of every cross-service event
  this service ever generated.

### Operational concerns

The outbox table grows monotonically unless you trim it. The relay can
both publish and (separately) delete rows older than N days; a cron job
works too.

The relay should be a singleton per service, or use distributed locking
to elect a leader. The framework's `OutboxRelay` supports both: solo
mode for development, leader-election mode (Redis) for production. The
underlying `SELECT ... FOR UPDATE SKIP LOCKED` would let multiple relays
coexist on the same table, but it's simpler to run one.

### Why not just use Kafka transactions?

Some teams reach for Kafka's exactly-once semantics (EOS) instead of the
outbox. EOS is great when your *only* state lives in Kafka. As soon as
you have a database alongside, you're back to the two-systems-not-atomic
problem and the outbox is the right tool. The outbox is broker-agnostic;
EOS is Kafka-specific. Pick the more flexible tool.

---

(continued in the next section)

## 8. Errors as values

Python's default error story: raise exceptions. The framework's: return
`Result[T, Error]` from handlers, raise only for genuinely exceptional
infrastructure failures (and even then, the pipeline translates them
back to `Result.failure` at the boundary).

### Why both

Exceptions are unmatched at jumping out of deep stacks. When a network
read fails 8 frames deep in a serialization helper, you don't want
every level to thread `Result` through manually.

Results are unmatched at making *expected* failure paths visible in the
type system. When `find_user(email)` can return "user not found", you
want callers to handle that case explicitly. With exceptions, "not found"
is a runtime surprise. With `Result[User, NotFoundError]`, the type
system forces the caller to deal with it.

The split: domain failures are Results. Infrastructure failures are
exceptions, caught at the pipeline boundary and translated to Results
with `InfrastructureError`.

### Anti-pattern: control flow with exceptions

```python
try:
    user = await repo.get(user_id)
except NotFoundError:
    return None
```

`NotFoundError` here is part of the normal flow — sometimes users don't
exist, that's fine. Using exceptions for it costs you:

- A stack trace gets constructed every call. (Cheap, but not free —
  in hot paths, measurable.)
- The control flow is invisible at the call site. Reading the caller,
  it looks like `await repo.get(...)` either returns a `User` or
  doesn't return. Surprise!
- Linters can't tell you you forgot to handle the case.

The Result form makes both visible and free:

```python
result = await repo.get(user_id)
if result.is_failure:
    return None
user = result.value
```

### Error codes vs error messages

Every error has a *code* (string, stable, machine-readable) and a
*message* (human-readable, can change). Examples:

- code: `user.not_found`, message: "No user with id 12345"
- code: `billing.payment_required`, message: "Invoice 6789 is unpaid"

Clients pattern-match on codes. Humans read messages. Don't conflate
them. Don't `if "not found" in error.message`; that's how you ship
bugs when someone tweaks the wording.

The framework's `Error.code` is the contract; `Error.message` is for
operators and logs.

### Hierarchies

`Error` has subclasses (`ValidationError`, `NotFoundError`,
`ConflictError`, ...) that carry standard HTTP status / gRPC code on
their classes. Your domain errors should subclass these so the
framework knows how to map them to wire formats:

```python
class EmailAlreadyInUseError(ConflictError):
    """Specific to user registration."""
```

Now `raise EmailAlreadyInUseError(...)` gets a 409 response automatically.

---

## 9. Logging, metrics, traces

The three pillars of observability. Each answers a different question.

- **Logs** answer "what happened" — discrete events with detail.
- **Metrics** answer "how often, how fast, how bad" — aggregates.
- **Traces** answer "where did the time go" — causal flow across services.

You need all three. None substitutes for the others.

### Logs

Structured JSON in production. Plain-text human-readable in development.
The framework's structlog config flips automatically based on
`LOGGING__JSON_OUTPUT`.

Rules:

- **Log structured fields, not formatted strings.** `log.info("user
  registered", user_id=str(id))` beats `log.info(f"user {id}
  registered")`. The former queries cleanly in Loki / Cloudwatch / Splunk;
  the latter forces full-text search.
- **Don't log secrets.** Tokens, passwords, credit-card numbers — ever.
  Redact at the boundary.
- **Don't log PII unnecessarily.** Emails and names show up in audit
  logs; that's by design. Avoid them in routine request logs. Hash
  user ids when correlating across services.
- **Log levels matter.** `DEBUG` for detail you turn on when investigating;
  `INFO` for routine business events; `WARNING` for things that worked
  but suggest a problem (degraded mode, fallback used); `ERROR` for
  things that didn't work; `CRITICAL` only for "the service is down".
- **One log line per significant event, not ten.** A request that emits
  20 INFO lines drowns the signal. Log once at completion with the
  fields that matter.

### Metrics

Prometheus model: counters (monotonic), gauges (instantaneous),
histograms (distribution). The framework's `Metrics` class provides
standard counters for every service:

- `qx_command_total{name, outcome}`
- `qx_command_duration_seconds{name, outcome}`
- `qx_http_request_total{method, route, status}`
- `qx_http_request_duration_seconds{method, route}`
- `qx_outbox_pending` (gauge)

Service-specific business metrics go alongside:

```python
self.metrics.counter(
    "billing_invoices_paid_total",
    "Number of invoices paid",
    labelnames=("payment_method",),
)
```

Label cardinality discipline:

- Bounded labels: `payment_method` (5 values), `status` (10 values), `route`
  (~50 values). All fine.
- Unbounded labels: `user_id`, `request_id`, anything that grows with
  traffic. Never. Each unique label combo becomes a distinct time
  series; Prometheus melts.

Your service's metric cardinality should fit on one screen of unique
combinations. If it doesn't, you're using metrics for what should be
logs or traces.

### Traces

OpenTelemetry spans, exported via OTLP/gRPC to Tempo/Jaeger/Honeycomb.
The framework opens a top-level span per HTTP request, per worker
message; you can open sub-spans inside hot paths:

```python
with trace_span("invoice.calculate_total", attributes={"line_items": 12}):
    total = self.compute_total(items)
```

A good span:

- Has a meaningful name (`invoice.calculate_total`, not `function_1`).
- Tagged with attributes that distinguish it (which tenant? which
  document? how many items?).
- Lasts long enough to be worth recording (>1ms is a rough lower bound).

Traces are *sampled*. Don't expect every request to leave a trace. In
production, head-sampling at 1-10% is typical; tail-sampling (decide
to keep the trace after seeing the latency) is better but needs the
collector to support it.

### Correlation

The single most important observability feature: **one correlation id
flows from client → service → service → service**. Logs, metrics
labels (when low-cardinality), and trace ids all carry it.

The framework's `RequestContext` makes this automatic. HTTP middleware
extracts/synthesizes `X-Correlation-Id`; NATS publisher attaches it as
a header; consumer extracts it. Logs get enriched via the structlog
context processor. When something breaks, you grep one id and see the
whole story.

---

## 10. HTTP design

### REST is fine

You will hear about GraphQL, gRPC, JSON:API, OData, HAL, JSON-RPC,
Falcor. Most projects don't need them. Plain REST with consistent
conventions wins on operability: every developer, every tool, every
proxy understands HTTP semantics.

The framework's HTTP conventions:

- **POST** to a collection creates a member. Returns 201 + envelope.
- **GET** a member returns it. 404 if missing.
- **GET** a collection returns a paged list. 200 + envelope with
  pagination metadata.
- **PATCH** a member modifies it (partial). 204 on success, no body.
- **DELETE** a member soft-deletes it. 204.

Avoid:

- **PUT for create.** PUT is idempotent replace; if your handler is
  "create or do nothing if exists," POST + idempotency-key is clearer.
- **GET with side effects.** Caches, retries, prefetchers all assume
  GETs are safe.
- **Verbs in URLs.** `/users/123/activate` reads OK but it's a
  command, not a resource. Use `POST /users/123/activations` (an
  activation event is a resource) or `PATCH /users/123` with a body
  field. Both work; pick one and stick with it.

### The envelope

Every response has the shape:

```json
{
  "success": true,
  "data": {...} | [...] | null,
  "error": null | {"code": "...", "message": "...", "details": {...}},
  "metadata": {"correlation_id": "...", "request_id": "...", ...}
}
```

Why:

- The shape is *uniform* across success and failure. Clients have one
  parser, not two.
- The status code carries the intent (201, 404, 409) but the body has
  the definitive answer; some gateways (k8s ingress, CDN) mangle status
  codes, and you don't want to lose information.
- Metadata (correlation id, pagination cursors) rides separately from
  business data so it doesn't pollute the response shape.

Some teams hate envelopes because they add a level of indirection. The
trade-off is real; pick a convention and apply it everywhere. Mixing
envelope and non-envelope responses across endpoints is the worst of
both worlds.

### Versioning

Two reasonable strategies:

- **URL prefix:** `/v1/users`, `/v2/users`. Simple, obvious. Old
  clients keep working; new clients use new paths.
- **Accept header:** `Accept: application/vnd.qx.v2+json`.
  Cleaner URLs, but every client must set the header right.

The framework's CLI scaffolds URL-prefix versioning. Most teams find it
easier to operate.

When you add v2: don't deprecate v1 until v2 has been live for at least
one client release cycle. Don't *remove* v1 until you've measured zero
traffic for a month.

### Pagination

Two flavors, both supported by the framework:

- **Offset pagination** (`?page=2&page_size=20`). Simple. Breaks down
  for stable iteration when rows are inserted/deleted mid-walk.
- **Cursor pagination** (`?cursor=abc...&page_size=20`). Cursor is
  opaque base64 of the last seen sort key + id. Stable across writes.
  Strictly better for iterating large lists; slightly more complex.

Use cursor pagination by default for any list that might exceed a few
hundred items. Reserve offset pagination for cases where the client
genuinely needs random access (paginated UIs with "jump to page" buttons).

### Idempotency

`POST` is not idempotent by default. Network blips mean clients retry;
without idempotency, retries create duplicates. The framework supports
the `Idempotency-Key` header:

```
POST /users
Idempotency-Key: 7f9b3e2c-...
{...}
```

The framework caches the response under that key for 24h; a retry with
the same key returns the cached response without re-running the
handler. A retry with the same key but different body returns 412
("you reused the key with a different request, that's a bug").

Always make state-changing endpoints idempotent. The cost is one round
trip to Redis on first call; the benefit is correctness under retry.

---

## 11. Database concerns: pools, transactions, migrations

### Connection pools

A Postgres connection costs memory in the database (~10MB) and on the
client (file descriptor, async coroutine state). Each query needs one
connection for its duration.

A pool keeps a configurable number open and reuses them. Two settings
matter:

- `pool_size` — how many connections are kept warm.
- `max_overflow` — additional connections that can be opened under
  load, returned to the pool when no longer needed, eventually closed
  if idle.

Both have caps imposed by the DB (Postgres default `max_connections=100`,
shared across all clients). With 20 service replicas at `pool_size=10`,
you're at 200 already; you'll hit the cap. Either:

- Raise `max_connections` (cheap up to ~500; beyond that, PgBouncer).
- Lower `pool_size` and lean on overflow.
- Use a connection pooler (PgBouncer / pgcat) between service and DB.

For serverless runtimes (Lambda, Fly Machines that auto-scale to zero),
use `NullPool` — connections are opened per-request and closed at the
end. Pools fight serverless lifecycle.

The framework's `DatabaseSettings` exposes these. Pick once per service,
based on traffic profile and DB capacity.

### Transactions

Every command opens a transaction. The framework's `UnitOfWork` does this
via `async with`. You commit explicitly; on exit-without-commit (any
return path that didn't call `uow.commit()`), the UoW rolls back. This
guards against forgotten commits more than against deliberate ones, but
both bugs are common.

Inside a transaction:

- **Don't do I/O to other systems.** A 100ms HTTP call inside a
  transaction is a 100ms lock on the rows you touched. Move external
  calls outside the transaction or to a follow-up integration event.
- **Don't sleep.** Same reason.
- **Watch transaction duration.** Long transactions hold locks; locks
  block other writers; you spiral. A statement-level `SET LOCAL
  statement_timeout = '5s'` is a cheap safety net for catastrophic cases.

### Isolation levels

Postgres defaults to READ COMMITTED. Each statement sees a snapshot at
its start. The framework leaves this alone for most paths.

REPEATABLE READ helps for read-modify-write patterns where you don't
want phantom reads inside the transaction. Use sparingly; it can
serialize transactions and reduce throughput.

SERIALIZABLE gives you true serialization guarantees at the cost of
occasional `40001` errors that the application must retry. Worth it for
hot-spot writes where correctness matters more than throughput (account
balance debits, inventory decrements).

### Migrations

Alembic is the de-facto Python migration tool. The framework wires it
up by default. A few rules:

- **Each migration is a small unit.** Add a column, backfill, switch
  read paths to it, drop the old column — across multiple deploys.
  Don't do all of that in one migration.
- **Make migrations backward-compatible with the previous service
  version.** During a rolling deploy, both versions run simultaneously.
  If the migration breaks v1 while v2 is rolling out, v1 pods crash
  and you have an outage.
- **Don't put data backfills in DDL migrations.** Migration time
  matters; long-running data fills should be background jobs, not
  blocking the deploy.

### Read replicas

Once write load is high, route queries to replicas. The framework's
session factory can be configured with a writer + reader engine pair
(V2.1); for now, services that need it wire it themselves.

Trap: replication lag. Reads from a replica may not see writes that
just happened on the primary. Common in workflows like "user
registered → user redirected to profile page → profile page reads from
replica → not found." Mitigate with sticky-write-after-read (route
reads to primary for N seconds after a user's write) or accept the lag
and design UI around it.

---

## 12. Caching: what to cache, what not to

Caching is one of the easiest places to introduce bugs that no one
notices until they cost real money.

### Three reasons to cache

1. **Repeated computation is expensive.** Materializing a complex
   query, hitting a slow upstream API.
2. **The data changes rarely.** Configuration, feature flags, reference
   data.
3. **Traffic is unevenly distributed.** Hot rows that every request
   touches.

If none of the three apply, don't cache.

### What can go wrong

- **Stale reads.** Cache returns yesterday's data; user sees stale UI.
- **Thundering herd.** Cache miss triggers 1000 requests to recompute
  the same thing simultaneously. Use single-flight (one in-flight
  computation per key) or pre-warm during deploy.
- **Cache stampede on TTL expiry.** Every request after the TTL hits the
  origin. Use randomized TTL jitter (`ttl + random.uniform(-30, 30)`)
  to spread the load.
- **Inconsistency between cache and source of truth.** Write to DB but
  forget to invalidate the cache. The user gets old data on the next
  read.

### Patterns

- **Read-through.** Get from cache; if miss, get from source, write
  to cache, return. Easy to implement, easy to reason about.
- **Write-through.** Update cache and source on every write. Simple
  but doubles write latency.
- **Write-around.** Write to source only; let the next read populate
  cache. Good for write-heavy data.
- **Write-back.** Write to cache, lazy-flush to source. Risky —
  cache outages lose data.

The Qx framework's `Cache` is intentionally barebones (`get_json`,
`set_json`, `delete`). Pick the pattern that fits the data; don't
push pattern complexity into the cache abstraction.

### Cache invalidation

Two hard problems in computer science: cache invalidation, naming
things, and off-by-one errors.

Strategies:

- **TTL.** Set every cache entry to expire after N seconds. Coarse but
  simple. Always combine with another strategy for correctness-critical
  data.
- **Event-driven.** Subscribe to integration events; invalidate
  affected keys. Couples the cache to the broker; works well in CQRS
  systems where integration events already exist.
- **Versioning.** Include a version in the cache key. Bump the version
  when the data changes globally (config reload). All old entries
  become unreachable; they expire naturally.

### Things not to cache

- Authorization decisions. Use a policy evaluator; rely on its built-in
  caching strategy if any.
- Sequential ids. Caching the "next id" forces every node to fight for
  the cache lock; just use UUIDs or a sequence.
- Data that's already fast. Caching a 1ms query for "performance" adds
  more complexity than it saves.

---

## 13. Messaging: NATS, JetStream, at-least-once semantics

The framework uses NATS with JetStream. The choice was deliberate:

- Lighter operationally than Kafka (no Zookeeper, no per-partition
  consumer-group rebalancing complexity).
- First-class support for request/reply, pub/sub, and stream patterns.
- JetStream gives you durable streams with ack/nack semantics, retention
  policies, and consumer-side persistence.

If your team already runs Kafka well, Kafka is a reasonable substitute;
the framework's `qx-events` abstraction would need an alternate
publisher/consumer pair. Don't switch for no reason.

### At-least-once

Every message broker that gives you durability gives you at-least-once,
not exactly-once. The consumer must handle duplicates.

Three flavors of consumer handling:

1. **Naturally idempotent.** Setting a user's email to a specific value
   is idempotent — applying it twice is the same as once.
2. **Idempotency-key.** Each message has an id; the consumer records
   "id X processed" and short-circuits subsequent deliveries.
3. **Eventually consistent.** The consumer projects into a view that's
   designed to converge; duplicate updates don't matter.

The framework supports all three; idempotency-key is the most general
and works for everything.

### Streams and consumers

A JetStream **stream** holds messages persistently. Streams have
retention policies (size-based, time-based, count-based) — old messages
get evicted eventually.

A **durable consumer** has a name and tracks its own progress through the
stream. Multiple consumer instances with the same durable name *share*
the work (JetStream distributes messages between them). Different
durable names get independent copies of the same messages.

For each `(service, event)` pair, define a durable consumer. Worker
replicas share the consumer name; another service that consumes the
same event uses a different durable name and gets its own copy.

### Subjects and routing

NATS uses subject hierarchies: `identity.user.registered`,
`billing.invoice.paid`. Streams filter by subject patterns
(`identity.>`, `billing.invoice.*`). Consumers further filter their
durable.

Convention: `{service}.{aggregate}.{verb-past-tense}`. Verb past tense
is the marker that it's an event, not a command. (Commands use
`{service}.{verb}` like `identity.create_user` for service-to-service
RPC-style messaging, if you use that pattern at all.)

### When *not* to use a broker

- For synchronous request/reply where the caller needs an answer to
  proceed. Use HTTP or gRPC. NATS *can* do request/reply (`nc.request`),
  and it works, but the failure modes are surprising; explicit RPC is
  clearer.
- For very high throughput, very latency-sensitive paths (>100k msg/s
  per topic). NATS handles this fine, but at that scale, Kafka's
  partition story is more proven.

---

## 14. Idempotency

The single property that makes distributed systems survivable: the
ability to safely retry.

Network calls fail. Brokers redeliver. Clients retry. If your handlers
aren't idempotent, you'll create duplicate users, double-charge cards,
send the same email twice. The user reports it; you scramble.

### Levels of idempotency

- **Trivially idempotent.** `SET balance = 100` (vs `INCREMENT
  balance BY 100`). Replaying it has no effect.
- **Idempotent with a key.** "Charge this card $X with idempotency key
  Y." First call succeeds; later calls with the same key return the
  cached result.
- **Conditional.** "Set version 5 if currently version 4." Optimistic
  concurrency — only one of N retries succeeds; the rest see a
  conflict and report it.

All three are valid; pick per use case.

### Implementing idempotency-key

Client generates a unique id (UUID), sends it with every retry of the
same logical operation:

```
POST /charges
Idempotency-Key: 7f9b3e2c-4d1a-...
{"amount": 100, "card": "..."}
```

Server:

1. Look up the key in Redis. If present and `completed`, return
   the cached response.
2. If present and `in_progress`, wait briefly or return 409 (your
   choice).
3. If absent, claim the key (atomic Redis SETNX), do the work, store
   the response, return it.

The framework's `IdempotencyStore` does this with a Lua script for
atomicity. Wire it into a pipeline behavior so commands get it for
free.

### Domain-level idempotency

Some commands have natural idempotency keys built in. "Send welcome
email to user X" has key = user_id; sending it twice is a duplicate
email regardless of how the retry happened.

For these, the handler can short-circuit before any side effect:

```python
if await sent_emails_repo.exists(SentEmail(user_id=cmd.user_id, kind="welcome")):
    return Result.success(None)  # already sent
```

This is more robust than idempotency-key alone — it survives client
bugs that fail to supply the key.

---

## 15. Authentication, authorization, multi-tenancy

### Authentication: who is this?

Almost always, JWT or OIDC tokens. The framework's `qx-auth`
validates them.

The bearer token in `Authorization: Bearer ...` is signed by the
issuer. Validation verifies the signature against the issuer's public
keys (JWKS), checks expiry, audience, issuer. After validation, you
have a `Principal` value object: subject, tenant, roles, permissions.

Don't:

- Roll your own crypto. The library handles signatures; trust it.
- Validate tokens in every handler. Validate once in middleware;
  attach to `RequestContext`.
- Send the token to the database. The DB doesn't care who you are;
  it cares what user-id and tenant-id to use, which are derived from
  the validated principal.

### Authorization: what can they do?

Two main approaches:

- **Role-Based Access Control (RBAC).** Users have roles; roles have
  permissions; permissions are required to do things. The framework's
  default approach.
- **Attribute-Based Access Control (ABAC).** Decisions are based on
  request attributes, resource attributes, environment. More flexible;
  more complex.

Start with RBAC. Most authorization fits ("can this user delete
invoices?"). Escalate to ABAC where you need it ("can this user delete
invoices for organizations they're a billing-admin of, in tenants that
have feature flag X enabled, on weekdays only?").

The framework's `PolicyEvaluator` supports both. Permissions are
literal strings; policies can branch on the principal and the resource.

### Multi-tenancy

A tenant is the unit of isolation: a customer's data, a team's data, an
organization's data. Almost every production service is multi-tenant.

Three isolation levels, weakest to strongest:

1. **Logical isolation.** Single database, `tenant_id` column on every
   row, queries filter by it. Cheapest. Risk: a missed filter leaks
   one tenant's data to another. Mitigate with framework-level
   defaults (the repository base class filters tenant_id automatically
   unless explicitly opted out).
2. **Schema isolation.** One Postgres schema per tenant. Stronger
   isolation (a query against the wrong schema returns nothing rather
   than another tenant's data). More expensive (migrations run per
   schema). Use for compliance-driven cases.
3. **Database isolation.** One database per tenant. Strongest;
   highest cost. Used by some B2B SaaS for very large tenants.

The framework defaults to logical isolation with column filters.
Schema/database isolation is V3 work.

### Tenant in context

The tenant id flows through `RequestContext`. HTTP middleware extracts
it from `X-Tenant-Id` or from the validated principal. Repositories,
event handlers, integration consumers all read it from context — no
explicit parameter passing.

This means **every handler implicitly operates on one tenant**. If you
need cross-tenant operations (admin tooling), open a special "system"
context with no tenant filter. Mark such endpoints clearly in your
codebase; they're privileged operations.

---

## 16. Testing strategy

Tests serve three purposes:

1. **Catch regressions.** When you change something, do the
   already-passing tests still pass?
2. **Document behavior.** A reader of the test should learn what the
   code does.
3. **Drive design.** Hard-to-test code is usually hard-to-use code.

The pyramid:

- **Unit tests.** Pure functions, single classes, no I/O. Fast (ms).
- **Integration tests.** Multiple components, often with a real database
  (testcontainers). Slower (hundreds of ms each).
- **End-to-end tests.** Service running, HTTP requests, real broker.
  Slow (seconds). Sparse; one or two per critical path.

The framework supports all three:

- `qx-testing` provides `MediatorStub`, `RepositoryStub`,
  `OutboxAssert` for unit tests.
- `testcontainers` helpers for `postgres_container`, `redis_container`,
  `nats_container`.
- `FastAPI.testclient` for HTTP-level tests.

### What to test

- **Domain logic.** Every aggregate method. These tests are pure
  Python — no fixtures, no setup.
- **Command handlers.** With stubbed repository and mediator. Verify
  the command produces the right Result, records the right events,
  saves the right state.
- **Query handlers.** With pre-seeded fixtures. Verify the query
  returns the expected DTO.
- **HTTP routes.** With the test client. Verify status codes, envelope
  shape, validation behavior. Don't re-test the handler here.
- **Integration paths.** With real Postgres + real NATS. Verify the
  outbox writes are atomic, the relay publishes, the worker consumes.

### What not to test

- Framework code. The framework has its own tests.
- Trivial getters / property accessors. Useless if they pass; useless
  if they fail.
- Implementation details. A test that mocks `Repository.save` and
  asserts it was called once is fragile and unhelpful. Assert on
  effects, not on the calls that produce them.

### Fixtures and isolation

Each test should start from a known state and clean up after itself.
For DB tests, use a transaction-per-test pattern: each test runs in a
transaction that gets rolled back at the end. Faster than DELETEing
between tests, cleanly isolated.

For broker tests, use a unique subject prefix per test so consumers
don't see each other's messages.

### Property-based testing

`hypothesis` generates random inputs and shrinks failing cases. Great
for domain logic with non-trivial invariants:

```python
@given(emails=lists(emails(), unique=True, min_size=1, max_size=10))
def test_unique_emails_constraint(emails):
    for e in emails:
        User.register(e, "X").unwrap_or_raise()
    # ...
```

Use it for things where you have a property you can express ("the
total never goes negative", "the order is preserved"). Don't use it
for things where you can't.


## 17. Performance and the tools to measure it

Performance is engineered, not wished for. The single biggest lever is
**measure first, then optimize**. The second biggest is to know which
metric matters: p50 latency for happy-path UX, p99 for the worst-case
users feel, throughput for capacity planning, memory for cost.

### The shape of a service's performance

Most services follow a familiar shape:

- **Median request: dominated by 1–3 database round-trips.** A handler
  that loads an aggregate, mutates it, and saves it does three DB
  round-trips (one read, one write, one outbox insert all in the same
  transaction). At 1ms each network round-trip in a healthy cluster,
  you're at 3ms before the handler does anything else.
- **Tail latency: dominated by something contended.** A locked row, a
  pool exhaustion, a GC pause, a network blip. The p99 is rarely 10×
  the p50; it's usually one of these specific things.
- **Throughput: bounded by the slowest downstream.** Usually the
  database. CPU on the service is rarely the bottleneck for I/O-bound
  work; it's the database that runs out.

### Where to look first

When a service is slow:

1. **Check the database.** `pg_stat_statements`, slow query log. If
   one query dominates, that's your target.
2. **Check connection pool utilization.** Saturated pool means
   requests queue. Logs show `QueuePool.timeout` errors.
3. **Check upstream latency.** External HTTP calls have their own
   latency distribution; one slow upstream contaminates everything.
4. **Check tracing.** Spans show you where the time went per request.
5. **Only then check CPU profiles.** Most Python services do not bottleneck
   on CPU. When they do, it's a specific hot loop you'll find with
   `py-spy` or `austin`.

### Tools

- **`asyncio` debugging mode** (`PYTHONASYNCIODEBUG=1` or `asyncio.run(..., debug=True)`)
  warns about slow callbacks. Useful for catching accidental blocking.
- **`py-spy top` against a running process** gives a top-style view of
  where Python is spending time. Doesn't need code changes; works on
  production processes.
- **`asyncio.get_event_loop().slow_callback_duration = 0.1`** logs any
  callback that takes more than 100ms. Catches blocking calls inside
  coroutines.
- **EXPLAIN ANALYZE** for any query you suspect. Read the plan: are you
  scanning a 10M-row table that lacks an index? You'll know.
- **`pgbadger`** processes Postgres logs into a digestible report:
  slowest queries, most-frequent queries, lock waits.

### Patterns that pay off

- **Reduce round trips.** Fetch related rows in one query, not N. The
  N+1 query problem is the most expensive pattern in software, and
  SQLAlchemy gives you ways to avoid it (`selectinload`, `joinedload`).
- **Batch.** A worker processing 100 messages should write 100 results
  in one transaction (where the semantics allow), not 100 separate
  transactions.
- **Stream.** Long results should not be materialized into memory.
  SQLAlchemy's `yield_per` lets you iterate result sets in chunks.
- **Pre-compute.** If the same expensive view is requested often,
  build a materialized view (postgres) or a denormalized projection
  (a separate read store updated by integration events).

### Patterns that *look* like they pay off, but rarely do

- **Caching everything.** Caching is one of the easiest ways to
  introduce subtle bugs. Cache only what you've measured to be slow.
- **Switching to faster libraries.** "Switch from JSON to msgpack",
  "switch from requests to httpx for speed." The savings are usually
  in single-digit microseconds when the request itself takes
  milliseconds. Optimize the bottleneck, not the periphery.
- **Aggressive parallelism.** Spawning more tasks than the underlying
  resource can handle just shifts the wait. If your DB can handle 50
  concurrent queries, launching 200 puts 150 in a queue.

### Benchmarking discipline

Microbenchmarks lie. The same function in isolation runs differently
than in a real service: different cache states, different GC pressure,
different concurrent contention. Always benchmark against a workload
that approximates production.

Tools:

- **`locust`** for HTTP load testing. Python-native, easy to script.
- **`k6`** for HTTP load testing if you'd prefer JS / want better
  reporting. Both work.
- **`pgbench`** for raw Postgres load testing.

Don't benchmark "is this 100ns faster than that". Benchmark "can this
service handle 1000 req/s at p99 < 200ms".

---

## 18. Operating services

The discipline of running services is its own subject. The frontend
handbook had to mention this in passing; the backend version goes deeper
because backend services have more operational surface.

### Configuration

Every difference between environments (local, staging, production) is
configuration, not code. If you find yourself with `if env == "prod":`
in code, that's a smell — extract the difference into a setting.

The framework uses Pydantic Settings with the `QX_` prefix and
nested keys (`QX_DB__URL`). Layering:

1. Defaults declared on the settings classes.
2. `.env` files (development convenience; never in production).
3. Environment variables (production source of truth).
4. Command-line overrides (rare).

Secrets — DB URLs with passwords, API keys, JWT signing keys — should
*never* live in env vars baked into the image. They come from a secret
manager (AWS Secrets Manager, Vault, GCP Secret Manager) via an
operator like External Secrets Operator that syncs them into Kubernetes
secrets. The pod mounts the secret as env vars at runtime.

### Deployments

The framework's deployment story is K8s + Helm. Other reasonable choices
exist (Nomad, AWS ECS, Fly Machines, plain VMs). Pick one and standardize.

Within K8s:

- **Rolling deployments for stateless services.** New version comes up
  alongside old, traffic shifts gradually.
- **Recreate for the outbox relay.** Singleton, so there's nothing to
  roll. Brief gap is acceptable (events accumulate in the outbox
  during the gap; the new instance drains them).
- **Pre-deploy migrations.** Alembic runs as a Job *before* new pods
  come up. If migration fails, the deploy is aborted.

### Health checks

Two endpoints, both required:

- **`/healthz` (liveness).** "Am I still able to function?" Should
  return 200 unless the process is genuinely wedged. Should *not*
  depend on downstream systems; a downstream outage shouldn't kill
  this service.
- **`/readyz` (readiness).** "Can I serve traffic right now?" Should
  return 200 only when downstream dependencies are reachable. K8s
  removes the pod from the load-balancer rotation when readyz returns
  503; the pod stays in the cluster until it's healthy again.

Common mistake: making liveness too strict. If liveness calls the
database, a 5-minute database outage triggers K8s to restart every pod
— but the new pods can't reach the database either, so they restart
again, and now you have a thundering herd hitting the DB the moment it
comes back. Keep liveness simple ("the process responds at all"). Use
readiness for downstream dependency checks.

### Logs in production

JSON output is mandatory in production. Aggregator (Loki, Cloudwatch
Logs, Datadog, Splunk) picks them up. Searchable by field
(`correlation_id="..."`, `tenant_id="..."`, `level="error"`).

Don't:

- Log secrets, PII, raw request bodies.
- Log at DEBUG level in production. Bump to DEBUG temporarily for
  investigation; revert.
- Rotate logs in-process. Let the aggregator deal with retention.

### Alerts

The fewer alerts that page someone awake, the better. Each one must be:

- **Actionable.** The on-call person can do something. "Disk is full"
  is actionable; "error rate increased 5%" might not be.
- **Symptom-based, not cause-based.** Alert on "user-facing latency
  >1s" rather than "individual service CPU >80%." Sometimes the
  service is at 80% CPU and serving fine.
- **Tested.** Routinely fire a synthetic version. If you've never seen
  the alert fire, you don't know if it works.

A good set of starting alerts for any Qx service:

- `qx_http_request_duration_seconds:p99` over 1s for 5 minutes.
- Error rate (5xx / total) over 1% for 5 minutes.
- Outbox depth (`qx_outbox_pending`) over 1000 for 10 minutes.
- Worker lag (consumer pending) over some threshold for 10 minutes.
- Database connection pool exhaustion (timeout errors > 0).

### Runbooks

Every alert should have a runbook: "Here's what to do when this fires."
Concrete steps. "Check the upstream API status page. If it's down,
the alert is informational; if it's up, run the following query:
`SELECT ...`."

Without runbooks, every on-call rotation starts from scratch
investigating the same alert. Runbooks compound: each incident
improves the next one.

### Incident response

When something breaks in production:

1. **Stabilize first.** Rollback, scale up, divert traffic — whatever
   stops the bleed. Don't try to find the root cause while users are
   hitting errors.
2. **Communicate.** A status page entry; a Slack channel update; an
   internal heads-up to other teams. Silence makes everyone panic.
3. **Investigate.** Logs, metrics, traces. Find what changed.
4. **Mitigate.** A targeted fix; a feature flag flip; a config change.
5. **Post-mortem.** Within a few days, write up what happened, why,
   and what changes prevent it. Blameless: focus on systems, not
   individuals.

Failure is information. Treat it as such.

---

## 19. Senior-level trade-offs

The job at this level is less about knowing things and more about
having opinions backed by reasons. A few trade-offs every senior
engineer encounters:

### Speed vs correctness

Almost always: correctness. The user does not appreciate the
99-millisecond response that's wrong. The 200ms response that's right
is fine.

Exceptions: real-time systems (trading, game servers), where wrong is
acceptable because the next data point will overwrite it anyway.

For backend services: never optimize before measuring. Never trade
correctness for speed without an explicit, documented decision.

### Consistency vs availability

The CAP theorem says you can have two of three: consistency,
availability, partition-tolerance. Partitions happen (network is not
reliable). So you choose between C and A.

In practice, most user-facing services choose A: the user wants to be
able to do things, even if some data is slightly stale. Backend
services that handle money are different: a banking ledger chooses C
— if the system can't be sure of the balance, it refuses the
transaction.

The CQRS + outbox + eventually-consistent-projections pattern is
"choose A with eventual C". Most things work this way; the user sees a
slightly stale dashboard, the system eventually catches up.

Pick deliberately. Document the choice.

### Coupling vs cohesion

Tight coupling: changes to one part require changes to another.
Cohesion: things that change together are near each other.

You want high cohesion, low coupling. The framework's CQRS + outbox
gives you this between services: services communicate through
integration events (loose coupling), each service has its own
internally cohesive domain (high cohesion).

Trap: turning everything into a service for "decoupling". The result
is a distributed monolith: changes require updating six services in
coordination, deployment becomes a nightmare. The decoupling was
*organizational* (each service owned by a team), not *functional*
(the change really should have been local).

The fix: bigger services. A service should be small enough to fit in
one team's head, big enough that most changes are internal.

### Build vs buy vs adopt

For every framework decision: build (write your own), buy (pay for a
hosted offering), adopt (use open source).

Adopt by default when a healthy open-source option exists. The
maintenance cost of "your own" rarely pays off.

Buy when:

- The thing is operational expertise you don't have (Auth0 vs running
  your own OIDC IdP).
- The thing is core but undifferentiated (Stripe vs your own payment
  processing).

Build when:

- The thing is your *actual* business differentiator.
- Off-the-shelf doesn't fit your operational model or scale.

A real example: this framework. It's a build, not an adopt. The
reason: existing Python frameworks were either too thin (FastAPI alone
doesn't give you transactions, outbox, mediator, observability) or too
heavy (Django doesn't fit a CQRS service shape well). Building it
made sense because the alternative was duplicating the same wiring
across every service. The cost is real; the payoff is the next 10
services come together faster.

### Mocking vs integration

In tests: mocks are tempting because they're fast. Integration tests
are slow but they actually catch the bugs that matter.

A rule of thumb: mock the boundary of the unit under test (the
collaborators it talks to). Don't mock things deeper. Mocking a
repository in a handler test is fine; mocking the database engine
inside the repository is not.

For the framework specifically: prefer `RepositoryStub` for handler
tests; use real Postgres (via testcontainers) for repository tests.

### Documentation vs code clarity

If you have to write a comment explaining what code does, can you
rewrite the code so it doesn't need the comment? Usually yes.
Sometimes no — for non-obvious *why*, comments are essential.

```python
# Why: we use ORDER BY ... LIMIT 1 instead of MAX() because Postgres
# can't use the index for MAX() without a sequential scan when the
# table has soft-deleted rows.
```

That comment is useful. "Increment the counter" before `count += 1`
is not.

API documentation is different. Auto-generated from OpenAPI / docstrings.
Don't skimp; the API doc is the contract.

### Pragmatism vs purity

Some of the framework's choices are deliberately impure:

- Repositories return domain entities, but the entity is constructed
  from a row inside the repository — so there's a serialization
  boundary the domain doesn't see.
- The unit-of-work tracks aggregates; the application has to call
  `uow.track(...)` explicitly. We could do magic to auto-track
  everything passed to `repo.add(...)` and `repo.save(...)`; we chose
  explicitness.
- The aggregate's events get enriched with context (correlation_id,
  tenant_id) by the unit-of-work, not by the aggregate itself.
  Aggregates shouldn't know about correlation ids.

Each of these is a deliberate trade-off between clean architecture and
practical use. Purity is a tool, not a goal. The goal is software that
works.

### Final principle

You will, in your career, work on systems where the original decisions
were wrong. Sometimes obviously wrong, sometimes subtly. Your job is
not to declare them wrong and rewrite from scratch — that's the
junior's move. Your job is to:

1. Understand *why* the decisions were made (what trade-off was being
   resolved, even if you'd resolve it differently today).
2. Decide which parts to keep, which to migrate, which to leave alone.
3. Migrate incrementally, leaving the system better at every step than
   it was before.

The frameworks you'll work on are someone else's frameworks. The
services you'll inherit are someone else's services. The discipline of
making them better without breaking them is the discipline that takes
you from senior to staff-and-above.

---

## Closing

This handbook is one team's view, written in 2026 with current tools and
current pressures. Some of it will age. The patterns — aggregates,
events, outbox, idempotency, dependency rule — won't.

The right way to use this document is as a starting point. Disagree
where you have reasons. Re-read it when your team grows beyond what
implicit convention can carry. Update it when you find a better
pattern.

Build well. Operate carefully. Ship things that work.

---

## Appendix A: Database deep-dive

A few topics that deserve more attention than the section above gave them.

### Indexes and how Postgres uses them

An index is a data structure (B-tree, hash, GIN, BRIN) that lets the
query planner find rows without scanning every row. Indexes cost write
performance (each insert/update touches the table *and* every relevant
index) and disk space.

Default for most columns: B-tree. Effective for:

- Equality (`WHERE col = ?`).
- Inequality and range (`WHERE col > ?`, `BETWEEN`, `LIKE 'foo%'` —
  but not `LIKE '%foo'`).
- ORDER BY (the index already has the rows in order).

GIN indexes for JSONB / array / full-text. BRIN for very large
append-only tables where rows correlate with insertion order (time-series
data).

**Composite indexes** (`(col_a, col_b, col_c)`) are useful for queries
that filter on the prefix. `WHERE col_a = ? AND col_b > ?` uses the
index; `WHERE col_b > ?` does not. The column order matters.

**Partial indexes** filter the index itself: `CREATE INDEX ... WHERE
deleted_at IS NULL`. Useful when you mostly query active rows; the
index is smaller and faster to maintain.

**Index-only scans** happen when every column the query needs is in
the index, so Postgres doesn't need to touch the heap. Add INCLUDE
columns to an index to enable this: `CREATE INDEX ... (a, b) INCLUDE (c)`.

The framework's `Repository` doesn't auto-add indexes. Migration time:
look at the queries the repository emits and add indexes covering each
filter and sort path.

### EXPLAIN ANALYZE in practice

`EXPLAIN ANALYZE SELECT ...` runs the query and shows the plan with
actual row counts and timings. Read it bottom-up: the innermost node
runs first.

Patterns to recognize:

- **Seq Scan on big table** — almost always wrong if the WHERE has
  selective predicates. Missing index, or the planner thinks the
  scan is cheaper (statistics out of date — run `ANALYZE`).
- **Nested Loop with high row count** — a join that should have been
  a Hash Join or Merge Join. Sometimes a planner mistake; sometimes
  truly the right plan for small inputs.
- **Sort with high cost** — could be avoided with an index that
  pre-sorts.
- **Filter after the index** — the index brought back rows that get
  filtered out. The index is wrong; add the filter column to it.

### Connection lifecycle

Each connection has costs beyond memory: prepared statement caches,
transaction state, role / search_path settings. The framework's
`session_factory` returns sessions backed by connections from the pool;
sessions don't outlive a request.

Subtle: opening a transaction *checks out* a connection from the pool
and holds it until commit/rollback. Long transactions hold connections
out of circulation. If `pool_size=10` and you have 11 simultaneous
long transactions, the 11th request waits for one to finish.

Symptoms of pool exhaustion:

- `QueuePool limit overflow` errors.
- p99 latency spikes that correlate with high transaction durations.

The fix is usually to shorten transactions, not to increase the pool.

### Locks

Postgres locks come in many flavors:

- **Row-level**: `SELECT ... FOR UPDATE` locks rows for the duration of
  the transaction. The next reader (also `FOR UPDATE`) blocks until
  this transaction ends.
- **Advisory**: `pg_advisory_lock(123)` — application-level locks that
  Postgres doesn't interpret, just gatekeeps. Useful for distributed
  leader election when you already have Postgres.
- **Table-level**: `LOCK TABLE foo IN ACCESS EXCLUSIVE MODE` — almost
  always wrong in application code. Migrations use them; runtime
  shouldn't.

Common deadlock pattern: two transactions update the same two rows in
different orders. Postgres detects the cycle and aborts one with error
`40P01`. Application must retry. Or: always update in a canonical
order (sort by id before updating).

### Vacuum, bloat, analyze

Postgres' MVCC means UPDATE/DELETE doesn't immediately free space; it
creates a new row version and marks the old one dead. Autovacuum
reclaims space periodically.

On heavily-updated tables, autovacuum can fall behind. Symptoms:

- Tables much larger than the live row count would suggest (`pg_stat_user_tables.n_dead_tup`).
- Query plans degrade (stale statistics).

Tune autovacuum per table: `ALTER TABLE ... SET (autovacuum_vacuum_scale_factor = 0.05);`
makes vacuum kick in at 5% dead tuples instead of the default 20%.

### Schema design

A few things that pay off:

- **UUIDs for primary keys.** The framework defaults to this. Reasons:
  no central id assignment (clients can mint ids before contacting the
  service), no leakage of count via sequential ids, easier merging
  across regions in V3.
- **Tenant id on every multi-tenant table.** As a column, indexed. Even
  if you start with logical isolation, the column is the path to
  schema isolation later.
- **Soft-delete (`deleted_at`) on user-facing tables.** Lets you undo
  accidental deletes; supports GDPR "hard delete after 30 days" cleanup.
  Audit/log tables don't need it.
- **`created_at` / `updated_at` everywhere.** Standard audit columns;
  the framework's `standard_audit_columns()` helper adds them
  consistently.

---

## Appendix B: Background jobs

The framework's worker runtime handles integration-event consumption.
That's one kind of background work; there are others.

### Job types

- **Triggered by event.** Worker consumes an integration event, does
  something. The default pattern; the framework handles it.
- **Periodic.** "Every minute, do X." A K8s CronJob is the standard
  answer. Don't put cron logic in the service process — separate
  scaling, separate failures, separate operational story.
- **Long-running batch.** "Reindex every user." A K8s Job or Argo
  Workflow. Process in chunks; restartable; idempotent.
- **Delayed.** "Run X in 30 seconds." Awkward. The cleanest answer
  is a scheduled message: put the work on a JetStream subject with a
  delivery delay, consumer picks it up at the right time. Alternative:
  a `scheduled_tasks` table polled by a worker.

### Patterns that scale

- **Chunked processing.** A batch job processing 10M rows should
  commit every N rows, not at the end. If it crashes, the next run
  picks up where it left off.
- **Checkpointing.** Persist progress so resuming is cheap. The
  framework's outbox table is the canonical example: each row has a
  `published_at`; the relay knows exactly what's done.
- **Backoff on retries.** Failing every 100ms means hammering the
  failing dependency. Exponential backoff with jitter is the
  textbook answer.
- **Dead-letter queues.** A message that fails N times goes to a DLQ
  for human inspection. The framework's worker supports max delivery
  via the JetStream consumer config; messages exceeding it land in
  the stream's max-deliver dead-letter pool.

### Failure semantics

- **At-most-once:** "Try; if it fails, give up." Bad default for most
  business work; you lose updates silently.
- **At-least-once:** "Retry until success." The default for the
  framework's worker. Handlers must be idempotent.
- **Exactly-once:** Not real over a network. The closest you can get
  is at-least-once + idempotent handlers, which produces an
  exactly-once *effect*. That's good enough for everything.

---

## Appendix C: A reading list

Books and papers worth the time:

- *Designing Data-Intensive Applications* by Martin Kleppmann. The
  best single book on the operational realities of distributed
  systems.
- *Domain-Driven Design* by Eric Evans. The original DDD book. Long;
  not all of it has aged well; the foundational concepts hold.
- *Implementing Domain-Driven Design* by Vaughn Vernon. More
  pragmatic than Evans. Good complement.
- *Site Reliability Engineering* by Google. Free online. The chapter
  on alerting alone is worth reading every year.
- *Release It!* by Michael Nygard. The patterns of building software
  that survives production. Bulkheads, circuit breakers, etc.
- *The Pragmatic Programmer* by Hunt and Thomas. Old (1999) but the
  practices remain.
- "How Complex Systems Fail" by Richard Cook (12-page paper). The
  best 15 minutes of reading on incident response.

Papers:

- "Life beyond Distributed Transactions" (Pat Helland). Why the
  outbox exists.
- "Hints for Computer System Design" (Butler Lampson). One of the
  great systems papers. Half of its hints are violated by every
  modern system; they're still hints.

The frameworks come and go. The thinking lasts.

---

## Appendix D: Observability deep-dive

### Designing log fields

The most common log-design failure is too many INFO lines saying too little.
A useful INFO record carries the *who*, *what*, *when*, and *why* in one line.

A good handler log:

```python
log.info(
    "command.completed",
    command="CreateUserCommand",
    outcome="success",
    duration_ms=12.3,
    user_id=str(new_user.id.value),
    tenant_id=str(ctx.tenant_id),
    correlation_id=str(ctx.correlation_id),
)
```

Field-level discipline:

- **`event` (the log message)** is a short, stable, machine-grep-able
  string. Not `"User 12345 created successfully in 12.3ms"`; that's
  three fields concatenated into one.
- **Snake_case keys.** Most aggregators index field names; consistency
  matters more than aesthetics.
- **Stringified ids.** UUIDs stringified at log time, not serialized
  as opaque bytes.
- **No nested objects in routine logs.** Flatten where reasonable. A
  log with `{user: {id: 'x', email: 'y'}}` is harder to query than
  one with `user_id='x', user_email='y'`.

### Trace span design

A trace is a tree of spans. The root span is the request; child spans
are the operations that happen during that request. Two questions to ask
for any span:

1. **Is it worth its own span?** Sub-millisecond operations clutter the
   trace; 10ms+ operations are useful.
2. **What attributes will I need when reading this trace in 6 months?**
   The aggregate id, the tenant id, the operation result, the rows
   affected. Future-you grep through traces from current-you's
   incident.

The framework's `trace_span` context manager attaches `RequestContext`
attributes automatically — correlation_id, tenant_id, user_id show up
on every span without any caller doing anything. Custom attributes you
add are merged on top.

### SLOs and burn rate

A Service Level Objective is a target — "99.9% of requests served in
under 500ms over a 30-day window." It's the language of trust between
your team and others (operations, product, customers).

Two important corollaries:

- **Error budget.** 0.1% of 30 days is ~43 minutes of "allowed" failure
  per month. You can choose to spend it on deliberate risk-taking
  (deploys, experiments, migrations) or to keep it in reserve.
- **Burn rate.** A short-window burn faster than the SLO allows means
  you're using the budget faster than you can replenish. Alert on this,
  not on absolute error counts — a 1% error rate at 3 AM is fine if
  your SLO is 99.5%; alerting on it wakes someone for no reason.

Tools to compute burn rate exist in Prometheus / Grafana SLO operators
and in services like Nobl9 / Cortex / Sloth. Pick one early; bolting
SLO discipline on later is harder than starting with it.

### Cost-aware observability

Logs, metrics, and traces cost money. A 100-instance fleet emitting
detailed logs at INFO can produce 10s of GB/day to your aggregator.

Strategies:

- **Sample.** Trace sampling at 1–10% is normal. Tail-sampling (decide
  to keep the trace after seeing if it had errors / high latency) is
  better; needs collector support.
- **Tier metrics.** High-cardinality, application-specific metrics live
  in a cheaper backend (Cortex, Mimir, Influx). High-value cluster
  metrics stay in the central Prometheus.
- **Compress old logs.** Most aggregators offer cold-tier storage:
  cheap, slower-query, for compliance retention.
- **Eliminate noisy fields.** A library that emits a debug log on every
  call is expensive at scale. Suppress at the source.

Observe your observability bill the same way you observe your DB bill.

---

## Appendix E: Messaging deep-dive

### Headers, payload, and envelope versioning

Every message has three layers:

1. **Transport headers** (NATS-level): subject, reply-to, JetStream
   stream/consumer metadata. The framework attaches `qx.event_name`,
   `qx.correlation_id`, `qx.tenant_id`.
2. **Envelope** (application-level wrapping): event_id, occurred_at,
   event_name, event_version, causation_id, payload.
3. **Payload**: the business data.

The envelope lets you evolve schemas without breaking consumers. A new
field on the envelope adds to it; consumers that don't care ignore it.
Payload changes are versioned with `event_version`, and consumers can
support multiple versions in parallel.

### Schema evolution rules

- **Additive changes are always safe.** Adding a field that consumers
  don't have to read.
- **Removing a required field is a breaking change.** Coordinate with
  every consumer; bump the version.
- **Renaming is a removal + addition.** Same constraint.
- **Changing field types is a breaking change.** UUID-to-string, int-to-string,
  etc.
- **Reordering fields is fine in JSON** but a breaking change in
  positional formats (Protobuf, Avro by number).

The framework uses JSON envelopes by default. Switch to Protobuf if you
need tighter wire size + schema enforcement at the message bus.

### When NOT to use a broker

- **Synchronous user-facing flows.** The user posts a form; the response
  must reflect the result. A message published to NATS is *eventually*
  consistent; the response can't wait for the projection to catch up.
  Do the work in-line; publish a follow-up event for downstream
  consumers.
- **Replacing function calls.** Two services that need each other
  *right now* should talk via HTTP or gRPC. Messaging adds latency
  and complexity that's only worth it when consumers are decoupled
  from the producer's lifecycle.
- **High-volume metrics streams.** OpenTelemetry / Prometheus already
  exist; don't reinvent.

### Patterns to know

- **Fanout.** One event, N consumers. Each consumer has its own
  durable; they don't share work.
- **Work distribution.** N consumer replicas with the *same* durable
  share the work. NATS distributes messages between them.
- **Request/reply.** A consumer publishes a response on the `reply-to`
  subject of the request. Works but is synchronous-flavored; prefer
  HTTP for true request/reply.
- **Dead-letter handling.** A message that fails N times needs human
  attention. Configure JetStream's `max_deliver`; oversized failures
  land in a separate stream you scrape for incidents.

### NATS-specific operational notes

- **JetStream's "deliver new" vs "deliver all."** A fresh durable
  defaults to `DeliverAll`: it gets the entire stream from the start.
  For a service joining an established system, this can be a lot.
  Choose `DeliverNew` for "only events from this point forward."
- **Subject filters.** Durables can filter the stream. `identity.>`
  catches everything under `identity.`; `identity.user.*` catches only
  user events. Use filters to make consumers consume only what they
  need.
- **Stream retention.** Default is `LimitsPolicy` — keep up to the
  size/time/count limit. `WorkQueuePolicy` deletes messages as soon
  as they're consumed (good for task queues). `InterestPolicy` keeps
  messages as long as any consumer hasn't read them yet.

---

## Appendix F: Idempotency deep-dive

### A taxonomy of "the same operation"

Three increasingly strong notions of "same":

1. **Same payload.** "POST /users {email: a@b}" — if I send the same
   body twice, that's the same operation. Idempotency-key isn't
   needed; the duplicate-email constraint catches it.
2. **Same intent, possibly different payload.** "POST /charges
   $100" — if I retry with a fresh body (regenerated timestamps,
   etc.) it's the same intent. Idempotency-key required.
3. **Cross-session same operation.** "User Alice meant to charge $100
   to card X" — different request from a different device. Should
   collapse via app-level deduplication.

The framework's idempotency store handles (2). (1) is handled by domain
uniqueness constraints. (3) needs app-level deduplication that the
framework can't generalize.

### Failure cases

- **Client retries with a new key.** Each call is treated as a new
  operation; you create duplicates. Train clients to *reuse* keys
  across retries of the same logical operation. Use SDK helpers if you
  publish one.
- **Different payload, same key.** The framework rejects with 412.
  Better than silently returning a wrong response. Treat 412 from
  this source as a bug to investigate.
- **Key expires before retry.** Default TTL is 24h. A client retrying
  after 25h gets re-processed. Tune TTL to the longest retry window
  you support.

### Distributed idempotency

For cross-service operations: the framework's `IdempotencyStore` is
service-local. If service A calls service B with idempotency key K,
service A is responsible for retry; service B sees each call with the
same K as the same operation.

When service A retries via NATS (publish an event), idempotency moves
to *consumers*: each consumer must dedupe via a key (often
`event_id`). The framework's worker doesn't auto-dedupe (it would need
to know your consumer's storage); applications do it inside handlers.

---

## Appendix G: Reading code

The single most undertaught skill in software is reading other
people's code. New engineers spend their time writing; senior engineers
spend a lot more time reading.

A short field guide:

- **Start from the entry point.** `main.py` / `app.py` / the routes
  file. Don't open random files in alphabetical order.
- **Follow one request through.** Pick a typical operation; trace it
  from HTTP handler to DB write. You'll have a working model of the
  system within an hour.
- **Read tests for the behavior, not the code.** A well-written test
  describes what the code *should* do; a poorly-written test describes
  what the code currently does. Both are informative.
- **Don't refactor first.** The temptation to clean up code you don't
  understand is strong; resist. Understand it first, *then* decide
  what changes.
- **Notice silences.** The places where the code *doesn't* do something
  are often where the bugs hide. "Why don't we validate this input
  here?" might be a missing check; might be a deliberate choice
  documented elsewhere.

The Qx framework is itself a body of code to read. The best way
to learn it is to clone the example service, run the tests, then trace
one request from HTTP route to outbox write. The third time you do this
the patterns will be obvious; on the tenth they'll be second nature.

---

## Appendix H: Patterns you'll encounter in the wild

### The God Service

A service that owns half of the domain because no one wanted to split
it. Symptoms: 50+ HTTP routes; the test suite takes 20 minutes; every
team is afraid to deploy on Friday. Cure: incrementally extract
bounded contexts into their own services, joined by integration events.

### The Distributed Monolith

The opposite mistake: services that can't deploy independently because
they all share a database, or all change together for every feature.
Cure: split the database, eliminate cross-service joins, communicate
via events.

### The Synchronous Spaghetti

Service A calls B which calls C which calls A. Symptoms: cascading
timeouts; deadlocks; "we only have problems when production is busy."
Cure: replace some of the synchronous calls with async events; if
that's not possible, add circuit breakers and timeouts.

### The Event Cascade

One event triggers handlers in five services; each of those publishes
its own events; soon you have an unbounded fan-out. Symptoms: cost
spikes; can't trace any operation end to end. Cure: design events
intentionally — *what should hear about this?* — and prefer "X
happened" events to "do X" command events. (Commands command;
events inform.)

### The Distributed Saga Anti-Pattern

A long workflow implemented by ad-hoc event listeners across services,
with no central record of "what step are we on?" The system works
when things go well; it falls over when one step fails halfway.
Cure: explicit saga state, stored somewhere, with compensation
steps for each step.

### The Hot Table

One table that every service writes to. The framework's outbox can
become this if multiple services share a database. Cure: per-service
databases; communicate via events.

---

## Closing notes (extended)

Two final habits worth cultivating:

**Write up what you learn.** Every incident, every postmortem, every
"huh, that was unexpected" — turn it into a paragraph in your team's
docs. The first time you encounter a problem, you investigate. The
next time, your notes save the next engineer the investigation.

**Argue with your past self.** Six months from now, you'll have new
context that makes today's code look naive. That's growth, not
failure. The job isn't to write code you'll always be proud of; it's
to write code that's *good enough now* and *easy to improve later*.

The frameworks change. The principles last.

---

## Appendix I: A guided tour of the identity-service example

The framework ships with `examples/identity-service` — a complete service
implementing user registration. It's small enough to read in one sitting,
real enough to deploy. This appendix walks through it as a teaching aid.

### Domain

Open `src/identity_service/domain/aggregates/user/__init__.py`. About 90
lines. The `User` aggregate:

- Has three fields: email, name, is_active.
- Has a class method `register(email, name)` that validates input and
  emits two events (`UserRegistered` for in-process handlers,
  `UserRegisteredIntegration` for the outbox).
- Has an instance method `change_email` that records
  `UserEmailChanged` when the email actually changes.
- Has `deactivate()` that flips `is_active` to False.

Notice what's not there:

- No SQLAlchemy. The aggregate doesn't know how it's persisted.
- No FastAPI. The aggregate doesn't know how the user requested the
  operation.
- No NATS. The aggregate records events; something else routes them.
- No `User.save()`. Persistence is an outside-the-aggregate concern.

This is what the dependency rule looks like in practice. The aggregate
is testable as pure Python; the tests in `tests/test_user_unit.py` run
in milliseconds without any external dependencies.

### Application

Three submodules: commands, queries, integration_handlers.

`commands/create_user.py`:

```python
@command_handler(CreateUserCommand)
class CreateUserHandler:
    def __init__(self, uow: UnitOfWork, sessions: SessionFactory) -> None:
        self._uow = uow
        ...

    async def handle(self, command: CreateUserCommand) -> Result[CreateUserDto]:
        async with self._uow:
            repo = UserRepository(self._uow.session)

            existing = await repo.find_by_email(command.email)
            if existing.is_success:
                return Result.failure(
                    ConflictError(code="user.duplicate_email", ...)
                )

            user_result = User.register(command.email, command.name)
            if user_result.is_failure:
                return ...

            user = user_result.value
            self._uow.track(user)
            await repo.add(user)
            await self._uow.commit()

        return Result.success(CreateUserDto(...))
```

Read it line by line:

1. `async with self._uow:` opens a SQL transaction.
2. `find_by_email` checks the uniqueness constraint at the domain
   level (the DB unique constraint is the ultimate guarantor; this
   check just makes the error message friendlier).
3. `User.register` is the factory — domain validation lives there.
4. `self._uow.track(user)` registers the aggregate so its events
   get drained on commit. The framework needs this because aggregates
   loaded via `repo.get(...)` are tracked automatically; aggregates
   created fresh need explicit tracking.
5. `repo.add(user)` inserts the row.
6. `uow.commit()` does several things in order:
   - Drains events from tracked aggregates.
   - Routes `UserRegistered` (a DomainEvent) to in-process handlers
     via the mediator.
   - Routes `UserRegisteredIntegration` (an IntegrationEvent) to the
     outbox table.
   - Commits the SQL transaction (everything atomic).

Notice that the handler doesn't catch exceptions. The pipeline's
`ExceptionTranslationBehavior` does that, mapping bare exceptions to
`InfrastructureError` Results. If we caught and translated here, every
handler would need the same boilerplate.

### Infrastructure

`infrastructure/persistence/user/mapping.py` defines the SQLAlchemy
table:

```python
users_table = Table(
    "users",
    metadata,
    uuid_column("id", primary_key=True),
    Column("email", String(255), nullable=False),
    Column("name", String(255), nullable=False),
    Column("is_active", Boolean, nullable=False, default=True),
    *standard_audit_columns(),
    UniqueConstraint("email", "tenant_id", name="uq_users_email_tenant"),
)

registry.map_imperatively(User, users_table)
```

`map_imperatively` is the SQLAlchemy 2 way to attach mapper metadata to
a class without making the class extend `DeclarativeBase`. The User
class stays pure Python; the mapper knows how to load and save it.

`infrastructure/persistence/user/repository.py` extends the framework's
generic `Repository[User]` with a domain-flavored finder:

```python
class UserRepository(Repository[User]):
    entity_cls = User
    table = users_table
    filterable_fields = {"email", "name", "is_active"}

    async def find_by_email(self, email: str) -> Result[User]:
        ...
```

The `_row_to_entity` and `_entity_to_dict` overrides handle the
`Identifier` wrapping (the table stores a UUID; the entity holds an
`Identifier(value=UUID)`). The default behavior assumes the table
columns exactly match the entity field names; this gives us the option
to depart from that.

### Presentation

`presentation/routes/users.py` has four endpoints. Each is small:

```python
@router.post("", status_code=status.HTTP_201_CREATED)
async def create_user(
    cmd: CreateUserCommand,
    mediator: Mediator = Inject(Mediator),
) -> dict:
    result = await mediator.send(cmd)
    return envelope_success(unwrap(result))
```

Three lines of logic:

1. FastAPI's `Pydantic` integration validates the body and constructs
   the command.
2. The mediator dispatches; the handler runs through the pipeline.
3. `unwrap` raises on failure (the framework's exception handlers
   convert it to the right HTTP status); `envelope_success` wraps the
   value.

The route is *thin*. All business logic lives in the handler. This is
deliberate: an HTTP endpoint should be a translation layer, not a place
for branching logic.

### Composition root

`main.py` is the wiring. About 70 lines:

```python
def build_app() -> FastAPI:
    settings = QxSettings(...)
    metrics, health = setup_observability(settings)
    container = Container()

    engine = create_engine(DatabaseSettings())
    session_factory = make_session_factory(engine)
    container.register_instance(SessionFactory, session_factory)

    mediator = Mediator(container, ...)
    container.register_instance(Mediator, mediator)

    registry = EventRegistry()
    register_events(registry)
    container.register_instance(EventRegistry, registry)

    container.register_scoped(UnitOfWork, _uow_factory)
    register_handlers(mediator, container)

    app = setup_qx_app(container, settings, ...)
    register_routes(app)
    return app
```

Read top to bottom. Settings → observability → DI container → engine →
mediator → event registry → unit-of-work → handlers → app → routes.
The order matters: each step depends only on what came before.

This is the *only* file in the codebase that knows about all of these
pieces together. Everything else is a pure function of its
dependencies.

### Worker

`worker.py` is a separate process. About 80 lines. It:

1. Builds the same DI graph as the HTTP service (minus the FastAPI
   app).
2. Connects to NATS.
3. Constructs a `WorkerRuntime` with a durable consumer.
4. Listens for SIGINT/SIGTERM for graceful shutdown.
5. Loops: pull batch → dispatch → ack/nack → repeat.

The worker shares the application package with the HTTP service —
same handlers, same DI wiring, same observability. The two deployments
differ only in their entrypoint. This is on purpose: keeping the two
in lockstep means handlers can move from "fired by HTTP" to "fired by
event" without rewriting.

### What's NOT in the example

To keep the example focused, we left out:

- A read model. The example reads from the same Postgres tables it
  writes to. A real service might project into a separate read store
  optimized for queries.
- Authentication. The example endpoints are open. A production service
  would wire `JwtValidator` into a middleware.
- Rate limiting. Same.
- A real audit logger consuming `UserRegistered`. The integration
  handler in the example just logs.
- Tenancy enforcement. The framework supports it; the example doesn't
  exercise the multi-tenant path.

Each is a follow-on exercise. Add one at a time; commit; deploy; learn.

---

## Appendix J: Anti-cheat sheet

A short list of things to refuse, no matter how convenient they seem in
the moment.

1. **Don't put business logic in HTTP routes.** Routes translate; they
   don't decide. Move it into a handler.
2. **Don't bypass the mediator for "internal" calls.** If handler A
   needs handler B's logic, either move the logic into a shared
   domain service, or dispatch B as a separate command.
3. **Don't bypass the outbox for "small" events.** "It's just a metric,
   I'll publish directly to NATS" — and then NATS is down for a minute
   and you lose data. The outbox is cheap; use it.
4. **Don't swallow exceptions.** `try: ... except Exception: pass` is
   how production becomes opaque. Either handle the specific exception
   meaningfully, or let it propagate.
5. **Don't share types between commands and queries.** They diverge.
6. **Don't put I/O in domain code.** Aggregate methods are pure
   functions of their inputs.
7. **Don't make integration events large.** Big payloads are a
   contract that's hard to evolve. Send ids; consumers fetch what
   they need.
8. **Don't lock tables in application code.** Migrations get to;
   handlers don't.
9. **Don't log secrets, even at DEBUG.** A debug log can leak as
   easily as an error log.
10. **Don't have a "utils" module that ends up holding everything.**
    Organize by domain concept, not by structural category.

The framework can't prevent you from doing any of these. It can only
make doing the right thing the easy thing. The discipline is yours.


---

## Appendix K: Security considerations

A framework can make secure-by-default easier, but cannot make a service
secure on its own. The list below covers what every backend engineer
should internalize.

### Input validation

Trust nothing from the network. Validate every field of every request.
The framework's commands and queries are Pydantic models — validation
runs automatically. Use the strict types:

- `EmailStr` instead of `str` for emails.
- `HttpUrl` for URLs.
- `conint(ge=0, le=100)` for bounded integers.
- `constr(min_length=1, max_length=255, pattern=r"^[\w\-]+$")` for
  pattern-constrained strings.

Strict validation isn't paranoia; it's the first wall of defense.
SQL injection, command injection, ReDoS, and a long tail of bugs are
caught here.

### SQL injection

If you're using SQLAlchemy's parameterized queries, you're safe by
default. The framework's `Repository` and `select()` API never
interpolate user input into SQL text.

The danger zone: raw text queries with f-strings.

```python
await session.execute(text(f"SELECT * FROM users WHERE email = '{email}'"))  # BAD
```

Never. Always:

```python
await session.execute(
    text("SELECT * FROM users WHERE email = :email"),
    {"email": email},
)
```

SQLAlchemy's `bindparams` handles the escaping correctly.

### Authentication failures

Three patterns to get right:

1. **Verify signatures before parsing claims.** The framework's
   `JwtValidator` does this in the correct order: signature → expiry →
   audience → issuer → claims. Reordering can leak information.
2. **Fail closed on signature errors.** A 500 to the client is fine;
   leaking which key failed is not.
3. **Constant-time comparison for secrets.** Use `secrets.compare_digest`
   rather than `==` when comparing tokens or API keys. Timing attacks
   are real.

### Authorization failures

Two pitfalls:

1. **Forget to check.** A handler that loads an aggregate by id without
   verifying the requester is authorized to access it is a
   horizontal-privilege-escalation bug. The framework's tenant scoping
   helps; the policy evaluator helps more; ultimately the handler
   author must remember.
2. **Check on the wrong thing.** "User can view this invoice if they
   own the organization" — but what if they used to and the
   organization was transferred? Check current state, not historical.

A pipeline behavior is the safest place to put authorization. The
handler can assume it ran.

### Secrets management

- Never in code. Never in config files committed to git.
- Loaded from environment at runtime; environment populated from a
  secret manager.
- Rotated periodically. The framework's JWT validator caches keys
  with TTL; rotation is automatic on the validator side. Issuer-side
  rotation is operational discipline.

### Dependency vulnerabilities

`pip-audit` (or `safety`) on every PR. CI gate that blocks merging
PRs with critical vulnerabilities. Pin top-level dependencies to
specific versions; use Dependabot (or Renovate) to track updates.

Don't be the team that finds out about a CVE from the news.

### Cross-service trust

In a microservices system, do services trust each other implicitly?
Two answers:

1. **Yes, with mTLS** at the network boundary (service mesh enforces
   it; certificates rotate automatically). Trust is at the wire level;
   any service inside the mesh is trusted.
2. **No, propagate user identity.** Every request carries the
   user's JWT; downstream services validate it. More secure; more
   overhead.

Most teams pick #1 for internal RPCs and #2 for sensitive operations.
The framework supports both: `RequestContextMiddleware` extracts user
identity from incoming headers; outbound calls can attach it.

### Data privacy

GDPR / CCPA / regional rules mean:

- **Right to access.** Users can ask for all their data; the service
  must produce it. Build this as an actual export endpoint; don't
  scramble during legal requests.
- **Right to deletion.** Hard-delete on request; soft-delete is
  insufficient. The outbox table is a particular consideration —
  events for deleted users contain their data.
- **Data minimization.** Store only what you need. Aggressively prune
  fields you've stopped using.

The framework doesn't enforce any of this. The discipline is yours.

---

## Appendix L: Migrations to keep around

The framework's CLI scaffolds a fresh service. In a long-running team,
you'll have services that started before you discovered patterns you
now love. Migrating them is a normal part of the work.

A few patterns for safe migrations:

### Adding a new column

1. Migration adds the column, nullable, with a default.
2. New code writes the column.
3. Backfill job populates historical rows.
4. Future migration sets NOT NULL constraint (only after backfill).
5. Eventually remove the default (older code that didn't set the
   column is gone).

Never combine "add column" with "set NOT NULL with backfill" in one
migration. The backfill blocks the migration; the migration blocks the
deploy.

### Renaming a column

1. Migration adds the new column.
2. Code writes both columns.
3. Backfill populates the new column from the old.
4. Code reads from the new column.
5. Code stops writing the old column.
6. Migration drops the old column.

Five separate deploys. Tedious. The alternative — drop and add — risks
data loss during a rolling deploy.

### Splitting a service

The hardest migration. The textbook approach:

1. Identify the bounded context.
2. Extract its tables, code, tests into a new package.
3. Run the new package as a separate service alongside the old.
4. Migrate clients to call the new service.
5. Migrate writes: dual-write for a period.
6. Cut over reads to the new service.
7. Remove the old code paths.

This is months of work for a real service. Plan accordingly. Don't
split until you have a clear bounded context; don't split for
"future-proofing."

### Joining two services

Rare but happens: two services discover they're really one. The
mechanics are the reverse of splitting; the political work is harder
because two teams have to agree.

If the join is right, the merged service is smaller and simpler than
the sum of the parts (no integration events, no shared data
duplication, no cross-service transactions). That's the test for
whether the join was the right call.

---

## Appendix M: Useful disciplines you'll never regret

A short list of practices that compound over a career:

### Always write the failing test first

When you find a bug, the first thing you do is write a test that
reproduces it. Then fix the bug. Then commit both.

If you fix first and write the test later, you'll write a test that
passes against the fix — but you don't know if it would have caught
the bug, because it never failed. Always start from red.

### Always read the code that called your code

Before changing a function, search for every callsite. Sometimes the
"obvious refactor" breaks a caller you didn't anticipate. The 30
seconds of grepping saves hours of post-deploy panic.

### Always commit small

A commit that changes 50 lines is reviewable. A commit that changes
5000 lines isn't, no matter how well-organized. Reviewers approve
without reading; subtle bugs slip through; nobody understands the
history later. Train yourself to break work into small commits, each
green, each meaningful.

### Always write the commit message for the future reader

Six months from now, someone will `git blame` a line and find your
commit. They will read your commit message looking for "why".
"Fix bug" is not why. "Reject empty email in User.register; the
domain validator was missing this case after the email field became
optional" is why.

### Always look at the data before you optimize

A query that's slow on your laptop might be fast on production
(different data shape, different hardware) or vice versa. Run the
query in production (read-only) before you optimize it; you might
discover you're optimizing the wrong thing.

### Always read the docs you're using

Library documentation is undervalued. The 20 minutes you spend
reading SQLAlchemy's session lifecycle documentation will save you
hours of debugging connection leaks. The 30 minutes on FastAPI's
dependency resolution will save you weeks of mis-wired services.

### Always sleep on the architecture decision

If you're about to make a decision that's hard to reverse — a
framework choice, a database choice, a major refactor — sleep on it.
Tomorrow's mind is wiser than tonight's mind. The 24 hours of delay
will not break you; the decision might.

### Always answer "how would I know this is broken in production"

For every feature, define how you'd detect that it's broken. A metric,
a log query, a synthetic check. If you can't define how you'd know,
you won't know. Build the detector when you build the feature.

