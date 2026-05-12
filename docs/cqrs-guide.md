# CQRS Guide

The Command/Query/Event triplet is the backbone of every qx service.
This guide covers the patterns, when to use which, and the pitfalls.

## Commands

A `Command` mutates state. It's a request to change the world.

```python
class CreateUserCommand(Command[UserDto]):
    email: str
    name: str
```

The generic parameter is the success type (`Result[UserDto]`). Commands
return `Result[T]` from their handlers — never raise for business
failures; always return a typed failure.

### When to use a command

- "Create a new X" — yes.
- "Update X" — yes.
- "Delete X" — yes.
- "Send X an email" — yes (state changes: the email is sent, audited).
- "Retry a failed payment" — yes.

### When NOT to use a command

- Pure reads (use a Query).
- Internal helper operations that aren't external use cases — those are
  just methods on a domain service.

### Idempotency

Commands should be idempotent at the boundary. The framework supports this
via `IdempotencyStore`: clients send `Idempotency-Key` header, the
framework short-circuits replays. See `idempotency-guide.md` for the
full pattern.

Inside the handler, idempotency is a domain concern: the aggregate should
refuse to apply the same change twice ("user already exists" → conflict).

## Queries

A `Query` reads state without mutating it.

```python
class GetUserQuery(Query[UserDto]):
    user_id: UUID
```

Query handlers should be side-effect-free. The framework leans on this
property: query handlers can be safely retried, cached, or fanned out to
read replicas.

### When to use a query

- "Get X by id" — yes.
- "List X with filter" — yes.
- "Count X" — yes.
- Anything that produces a DTO without changing state.

### When NOT to use a query

- If you're tempted to write to a side store (logging analytics, etc.) —
  do it through a `Notification` event in a pipeline behavior, not in the
  query itself.

### Don't share types between commands and queries

A common newcomer mistake: defining a `UserDto` once and using it as both
the command response and the query response. Sounds DRY; it's a trap.

Command responses describe "what just happened" — usually the minimal
identity of the changed thing. Query responses describe "what you asked
for" — often denormalized, joined, projected.

When they diverge (they will), forcing one type to do both forces ugly
options-bag fields. Define them separately from the start.

## Events

Three flavors, distinguished by lifecycle:

### DomainEvent (in-process)

Handled inside the same transaction. Used for "when X happens in this
aggregate, Y must also happen in this service, atomically."

```python
class UserRegistered(DomainEvent):
    event_name: ClassVar[str] = "user.registered"
    user_id: UUID
    email: str
```

Recorded by aggregates:

```python
class User(AggregateRoot[Identifier]):
    @classmethod
    def register(cls, email: str, name: str) -> "User":
        user = cls(id=Identifier(), email=email, name=name)
        user.record_event(UserRegistered(user_id=user.id.value, email=email))
        return user
```

Drained by the unit-of-work on commit:

```python
async with uow:
    user = User.register(email="ada@example.com", name="Ada")
    await repo.add(user)
    await uow.commit()  # ← UserRegistered handlers run here, inside the txn
```

### IntegrationEvent (cross-process)

Handled out-of-process via the outbox + broker. Used for "when X happens,
the rest of the system should hear about it (eventually)."

```python
class UserRegisteredIntegrationEvent(IntegrationEvent):
    event_name: ClassVar[str] = "identity.user.registered"
    user_id: UUID
    email: str
```

Recorded by aggregates the same way as domain events. The unit-of-work
sees the `IntegrationEvent` subclass and writes it to the outbox table
instead of dispatching in-process. The relay worker publishes it to NATS
later.

**Naming convention:** `{service}.{aggregate}.{verb-past-tense}`.
E.g., `identity.user.registered`, `billing.invoice.paid`. The service
prefix makes routing rules straightforward.

### Notification (fire-and-forget)

Like a domain event, but failures are suppressed. Used for "side activities
that shouldn't fail the main operation": activity logging, recently-viewed
tracking, etc.

## Pipeline behaviors

Pipeline behaviors are middleware around handler invocations. The
framework ships a few; you write your own for service-specific concerns.

```python
class TimingBehavior:
    async def handle(self, msg, next_):
        start = time.perf_counter()
        result = await next_(msg)
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info("dispatch", type=type(msg).__name__, ms=elapsed_ms)
        return result

mediator = Mediator(
    container,
    command_behaviors=(LoggingBehavior(), TimingBehavior(), TransactionBehavior(...)),
    query_behaviors=(LoggingBehavior(), CachingBehavior(...)),
)
```

Order matters: outermost runs first on the way in and last on the way out.
Standard ordering (outer to inner):

1. **LoggingBehavior** — see everything.
2. **MetricsBehavior** — measure everything.
3. **AuthorizationBehavior** — fail fast if denied.
4. **ValidationBehavior** — fail fast if invalid.
5. **IdempotencyBehavior** — short-circuit replays.
6. **TransactionBehavior** — open a UoW.
7. **RetryBehavior** — retry transient infra errors inside the UoW.
8. (handler executes)

Some teams put retry *outside* transaction so each retry gets a fresh
transaction. Pick one convention and stick to it — mixed retry/transaction
nesting causes confusing bugs.

## Anti-patterns

### "God handler"

```python
@command_handler(DoThingCommand)
class DoThingHandler:
    async def handle(self, cmd):
        # 400 lines of orchestration ...
```

Split it. Either:

- Extract domain logic into the aggregate (preferred).
- Extract orchestration into application services that the handler calls.
- Split into multiple commands sequenced by a saga.

### Command that returns the whole aggregate

```python
async def handle(self, cmd: CreateUserCommand) -> Result[User]:  # ❌
```

`User` is an aggregate, not a wire type. Return a DTO:

```python
async def handle(self, cmd: CreateUserCommand) -> Result[UserDto]:  # ✓
    user = User.register(...)
    await repo.add(user)
    return Result.success(UserDto.from_aggregate(user))
```

### Direct broker publish from a handler

```python
async def handle(self, cmd):
    await nats_publisher.publish(SomeEvent(...))  # ❌ — bypasses the outbox
```

Use the aggregate's event recording so the unit-of-work routes it through
the outbox.

### Skipping the mediator for "internal" calls

If handler A needs handler B's logic, **don't** call B's class directly —
that couples them at the implementation level. Either:

- Send a command through the mediator (B is a separate use case).
- Extract the shared logic into a domain service that both call.

The former is appropriate when B is genuinely a public use case. The
latter is appropriate when B is just internal computation.
