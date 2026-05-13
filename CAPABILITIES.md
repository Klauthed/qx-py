# Qx Framework — Capabilities Reference

> Keep this file up to date whenever a package gains or changes a capability.
> Each section maps to one `qx-*` package.

## Installation

```bash
# Everything at once
pip install qx-py

# Or cherry-pick
pip install qx-core qx-cqrs qx-db qx-http qx-di qx-observability
```

---

## qx-core — Foundation Types

The shared vocabulary every other package builds on.

### Result<T>
Railway-oriented error handling — no exceptions in domain logic.

```python
from qx.core import Result, NotFoundError

def find_user(id: UUID) -> Result[User]:
    if not found:
        return Result.failure(NotFoundError(code="user.not_found", message=str(id)))
    return Result.success(user)

result = find_user(some_id)
if result.is_success:
    print(result.value)
else:
    print(result.error.message)
```

**Error types:** `NotFoundError`, `ConflictError`, `ValidationError`,
`UnauthorizedError`, `ForbiddenError`, `InfrastructureError`.
All inherit from `Error` (a frozen dataclass, **not** a Python exception).
To raise one: `raise error.as_exception()` → raises `ErrorException`.

### Entity & Identifier

```python
from qx.core import Entity, Identifier

class UserId(Identifier): pass

class User(Entity[UserId]):
    email: str
    name: str
    is_active: bool = True
```

`Entity` ships with `id`, `version` (optimistic concurrency), and
`domain_events` (collected, then drained by the UoW).

### DomainEvent

```python
from qx.core import DomainEvent

class UserRegistered(DomainEvent):
    email: str
    name: str
```

Domain events are Pydantic models. Raise them inside an aggregate with
`self.record_event(UserRegistered(...))`.

### RequestContext & Settings

```python
from qx.core import RequestContext, current_context, QxSettings

ctx = current_context()      # always safe; returns empty default outside scope
ctx.tenant_id                # UUID | None
ctx.user_id                  # UUID | None
ctx.correlation_id           # str
```

`QxSettings` reads from `QX_*` env vars via Pydantic Settings.

### Pagination

```python
from qx.core import OffsetPage, OffsetPagination

page = OffsetPage(items=[...], page=1, page_size=20, total=57)
```

---

## qx-cqrs — Commands, Queries & Mediator

### Defining messages

```python
from qx.cqrs import Command, Query, command_handler, query_handler
from qx.core import Result

class CreateUserCommand(Command[User]):
    email: str
    name: str

class GetUserQuery(Query[User]):
    user_id: UUID
```

### Implementing handlers

```python
@command_handler(CreateUserCommand)
class CreateUserHandler:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def handle(self, cmd: CreateUserCommand) -> Result[User]:
        async with self._uow:
            ...
            return Result.success(user)


@query_handler(GetUserQuery)
class GetUserHandler:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def handle(self, query: GetUserQuery) -> Result[User]:
        ...
```

### Wiring the Mediator

```python
from qx.cqrs import Mediator, LoggingBehavior, ExceptionTranslationBehavior

mediator = Mediator(
    container,
    command_behaviors=(LoggingBehavior(), ExceptionTranslationBehavior()),
    query_behaviors=(LoggingBehavior(), ExceptionTranslationBehavior()),
)
mediator.register_decorated(CreateUserHandler)
mediator.register_decorated(GetUserHandler)

# Or bulk-register all @command_handler/@query_handler decorated classes:
register_handlers(mediator, container)  # your own helper calling mediator.register_decorated
```

### Sending messages

```python
result = await mediator.send(CreateUserCommand(email="x@y.com", name="X"))
```

### Pipeline behaviors

| Behavior | Effect |
|---|---|
| `LoggingBehavior` | Structured log on entry/exit with timing |
| `ExceptionTranslationBehavior` | Converts unhandled exceptions to `InfrastructureError` |

---

## qx-db — Database & Persistence

### Repository

Generic SQLAlchemy-backed repository. Extend it per aggregate:

```python
from qx.db import Repository
from qx.core import Result, NotFoundError

class UserRepository(Repository[User]):
    entity_cls = User
    table = users_table
    filterable_fields = {"email", "is_active"}

    async def find_by_email(self, email: str) -> Result[User]:
        # use self._session (AsyncSession) for custom queries
        ...
```

Built-in methods: `get(id)`, `exists(id)`, `list(pagination)`,
`add(entity)`, `save(entity)` (optimistic lock), `soft_delete(id)`,
`hard_delete(id)`.

### UnitOfWork

```python
from qx.db import UnitOfWork

class MyHandler:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def handle(self, cmd) -> Result[...]:
        async with self._uow:           # opens session, commits on exit
            repo = UserRepository(self._uow.session)
            user = ...
            await repo.add(user)
            return Result.success(user)
```

On commit the UoW dispatches domain events and writes outbox records.

### Migrations (Alembic helpers)

```python
# env.py — single-schema
from qx.db import run_async_migrations
run_async_migrations(target_metadata)

# env.py — schema-per-tenant multi-tenancy
from qx.db import run_async_migrations_all_schemas
run_async_migrations_all_schemas(target_metadata, schema_prefix="tenant_")
```

### Multi-tenancy modes

| Mode | Package | How |
|---|---|---|
| Row-level security | `qx-db` | `RlsMiddleware`; tenant_id column + Postgres policy |
| Schema-per-tenant | `qx-db` | `run_async_migrations_all_schemas` fan-out |
| Database-per-tenant | `qx-db` | `DatabasePerTenantRouter` |

### Outbox

`DefaultOutboxRecorder` writes integration events to `qx_outbox_events`
table inside the same transaction. The outbox relay (in `qx-events`) reads
and publishes them to NATS JetStream.

---

## qx-di — Dependency Injection

```python
from qx.di import Container

container = Container()

# Singletons (one instance for the process lifetime)
container.register_instance(Mediator, mediator)

# Scoped (one per request; factory receives resolved deps)
container.register_scoped(UnitOfWork, lambda sf: UnitOfWork(sf))

# Transient (new instance every time)
container.register_transient(MyService, MyService)

# Resolve
uow = container.resolve(UnitOfWork, scope=current_scope)
```

---

## qx-http — FastAPI Integration

### Bootstrap

```python
from qx.http import setup_qx_app

app = setup_qx_app(
    container,
    settings,
    metrics=metrics,
    health=health,
    extra_lifespan=lifespan,           # optional async context manager
    region_router=region_router,       # optional; enables region redirect
)
```

`setup_qx_app` wires: `RequestContextMiddleware` (correlation ID, tenant
from JWT), `MetricsMiddleware` (Prometheus per-route counters),
`RegionRedirectMiddleware` (optional), standardised error handlers, and
`/healthz`, `/readyz`, `/metrics` probes.

### Routes — DI injection

```python
from qx.http import Inject, scope_dep, envelope_success, unwrap
from qx.di import Scope
from fastapi import Depends

@router.post("/users", status_code=201)
async def create_user(
    cmd: CreateUserCommand,
    mediator: Mediator = Inject(Mediator),
    scope: Scope = Depends(scope_dep),
) -> dict:
    result = await mediator.send(cmd, scope=scope)
    return envelope_success(unwrap(result))
```

### Response envelope

Every response is wrapped automatically:

```json
{
  "success": true,
  "data": { ... },
  "error": null,
  "metadata": { "correlation_id": "...", "request_id": "..." }
}
```

On failure the HTTP status comes from the `Error` subclass (e.g. 404 for
`NotFoundError`, 409 for `ConflictError`).

### Pagination envelope

```python
return envelope_success(
    [u.model_dump() for u in page.items],
    page=page.page,
    page_size=page.page_size,
    total=page.total,
)
```

---

## qx-observability — Metrics, Health & Tracing

### Bootstrap

```python
from qx.observability import setup_observability

metrics, health = setup_observability(settings)
```

Configures: structlog JSON logging, OpenTelemetry tracing (OTLP exporter),
Prometheus metrics registry, and a `HealthRegistry`.

### Custom metrics

```python
counter = metrics.counter("orders_total", "Orders placed", labelnames=("status",))
counter.labels(status="paid").inc()
```

### Health checks

```python
health.add_liveness("db", check_db)
health.add_readiness("nats", check_nats)

# /healthz → liveness checks
# /readyz  → readiness checks
```

### Structured logging

```python
from qx.observability import get_logger

log = get_logger(__name__)
log.info("user.registered", user_id=str(user_id), email=email)
```

### Trace spans

```python
from qx.observability import trace_span

async with trace_span("my-operation", attributes={"key": "value"}):
    ...
```

---

## qx-flags — Feature Flags (OpenFeature)

Configure once at startup, evaluate anywhere.

```python
from qx.flags import FlagClient, InMemoryProvider, InMemoryFlag

# Startup — choose a provider
FlagClient.configure(
    InMemoryProvider({
        "payments.new-checkout": InMemoryFlag("off", {"on": True, "off": False}),
    })
)

# In a handler
class CheckoutHandler:
    def __init__(self, flags: FlagClient) -> None:
        self._flags = flags

    async def handle(self, cmd) -> Result[...]:
        use_new = await self._flags.bool("payments.new-checkout", default=False)
        ...
```

**Value types:** `bool()`, `string()`, `int()`, `float()`.
**Targeting:** the client automatically forwards `tenant_id`/`user_id` from
the current `RequestContext` as OpenFeature evaluation context.
**Production:** swap `InMemoryProvider` for FlagD or Unleash at startup.

---

## qx-regions — Multi-Region Routing

```python
from qx.regions import RegionConfig, RegionRouter, StaticRegionResolver

config = RegionConfig()   # QX_REGION__NAME, QX_REGION__URLS
resolver = StaticRegionResolver(
    {"tenant-abc": "eu-west-1"},
    default=config.name,
)
router = RegionRouter(resolver, config)
```

Pass `region_router=router` to `setup_qx_app`.  
Write requests (POST/PUT/PATCH/DELETE) for a tenant whose home region
differs from the current region are transparently 307-redirected.

**Production:** replace `StaticRegionResolver` with `DbRegionResolver`
(reads tenant→region mapping from Postgres).

---

## qx-auth — JWT, RBAC & Policies

### JWT validation

```python
from qx.auth import JwtValidator, JwtSettings, Principal

settings = JwtSettings()  # QX_JWT__* env vars
validator = JwtValidator(settings)

result: Result[Principal] = await validator.validate(token)
principal = result.value   # .subject, .roles, .permissions, .tenant_id
```

### RBAC & Policies

```python
from qx.auth import require_permission, PolicyEvaluator, Role, Permission

evaluator = PolicyEvaluator()
decision = evaluator.evaluate(principal, require_permission("orders:write"))
```

### Rate limiting

```python
from qx.auth import TokenBucket

bucket = TokenBucket(capacity=100, refill_rate=10)   # 10 tokens/s
result = await bucket.consume(principal.subject, tokens=1)
if not result.allowed:
    raise ForbiddenError(...)
```

---

## qx-cache — Redis Cache

```python
from qx.cache import CacheSettings, create_client, Cache, DistributedLock, IdempotencyStore

settings = CacheSettings()   # QX_CACHE__URL env var
redis = create_client(settings)
cache = Cache(redis)

# Typical ops
value = await cache.get_json("key")
await cache.set_json("key", {"data": ...}, ttl=300)

# Distributed lock
async with DistributedLock(redis, "lock:order-42", ttl=30):
    ...

# Idempotency (e.g. for payment handlers)
store = IdempotencyStore(redis)
await store.guard(request_id, ttl=86400)
```

---

## qx-worker — NATS JetStream Consumer

```python
from qx.worker import WorkerRuntime

runtime = WorkerRuntime(
    nats_url="nats://localhost:4222",
    stream="qx_events",
    consumer="identity-service",
    mediator=mediator,
    container=container,
    event_registry=registry,
    health=worker_health,
)

# Entry point (e.g. worker.py)
async def main() -> None:
    await runtime.run()
```

- Pulls batches from a durable JetStream consumer.
- Decodes integration events via `EventRegistry`.
- Opens a request scope and `RequestContext` per message.
- Acks on success or `PermanentWorkerError`; naks (with delay) on transient errors.
- Multiple worker replicas are safe — NATS load-balances.

---

## qx-events — Event Registry & Outbox Relay

```python
from qx.events import EventRegistry, MediatorEventDispatcher

registry = EventRegistry()
registry.register("identity.user.registered", UserRegistered)

# Publish via dispatcher (called by UoW on commit)
dispatcher = MediatorEventDispatcher(mediator)
```

The outbox relay (`OutboxRelay`) polls `qx_outbox_events` and publishes
any unpublished rows to NATS JetStream, then marks them published.

---

## qx-search — OpenSearch Repository

```python
from qx.search import SearchQuery, SearchRepository, SearchHit, OpenSearchRepository

query = SearchQuery(
    text="red shoes",
    filters={"category": "footwear"},
    page=1,
    page_size=10,
    sort=[("price", "asc")],
)

result: Result[tuple[list[SearchHit[ProductDoc]], int]] = await repo.search(query)
hits, total = result.value
```

Implement `SearchRepository[TDoc]` for custom backends.
Use `InMemorySearchRepository` from `qx-testing` in tests.

---

## qx-eventstore — Event-Sourced Aggregates

```python
from qx.eventstore import EventSourcedAggregate, EventStore

class Account(EventSourcedAggregate[Identifier]):
    balance: int = 0

    def deposit(self, amount: int) -> None:
        self.record_event(MoneyDeposited(amount=amount))

    def apply_moneydeposited(self, ev: MoneyDeposited) -> None:
        self.balance += ev.amount
```

`EventStore` loads/saves aggregates from the `qx_aggregate_events` table.
Snapshots are stored in `qx_aggregate_snapshots` (configurable interval).
Include the tables: `include_eventstore_tables(metadata)`.

---

## qx-saga — Process Managers (Orchestration Sagas)

Durable multi-step workflows that react to integration events and dispatch
compensating commands on failure.

```python
from qx.saga import Saga, SagaState, SagaManager, on, on_timeout

class OrderState(SagaState):
    order_id: str = ""
    reservation_id: str = ""

class OrderFulfillmentSaga(Saga[OrderState]):
    state_type = OrderState

    @on(OrderPlaced)
    async def start(self, ev: OrderPlaced) -> None:
        self.state.order_id = ev.order_id
        await self.dispatch(ReserveInventoryCommand(order_id=ev.order_id))

    @on(InventoryReserved)
    async def inventory_done(self, ev: InventoryReserved) -> None:
        self.state.reservation_id = ev.reservation_id
        await self.dispatch(ChargePaymentCommand(order_id=self.state.order_id))

    @on_timeout(after=timedelta(hours=1))
    async def handle_timeout(self) -> None:
        await self.compensate()

    async def compensate(self) -> None:
        if self.state.reservation_id:
            await self.dispatch(ReleaseInventoryCommand(...))
```

State persisted in `qx_saga_instances`. Wire `SagaManager` as an
integration-event handler in the worker.
Include the table: `include_saga_table(metadata)`.

---

## qx-projections — Read-Model Projections

Build and incrementally update read models from the event log.

```python
from qx.projections import Projection, ProjectionRunner

class BalanceProjection(Projection):
    name = "account_balance"

    async def on_moneydeposited(self, ev: MoneyDeposited) -> None:
        await self._db.execute(
            "UPDATE balances SET balance = balance + :amt WHERE id = :id",
            {"amt": ev.amount, "id": ev.aggregate_id},
        )

    async def reset(self) -> None:
        await self._db.execute("TRUNCATE balances")
```

`ProjectionRunner` tracks the last processed sequence number per
projection in `qx_projection_checkpoints`.
Include the tables: `include_projection_tables(metadata)`.

---

## qx-grpc — gRPC Server

```python
from qx.grpc import create_grpc_server, RequestContextInterceptor, ExceptionInterceptor

server = create_grpc_server(
    interceptors=[
        RequestContextInterceptor(),
        ExceptionInterceptor(),
    ]
)
add_UserServiceServicer_to_server(MyServicer(), server)
server.add_insecure_port("[::]:50051")
await server.start()
```

Interceptors: `RequestContextInterceptor` (sets `RequestContext` per call),
`ExceptionInterceptor` (maps `ErrorException` → gRPC status codes),
`MetricsInterceptor` (Prometheus counters/histograms per RPC method).

---

## qx-testing — Test Utilities

### Test doubles (no infrastructure required)

```python
from qx.testing import RepositoryStub, MediatorStub, FlagClientStub, InMemorySearchRepository

# Repository stub (in-memory, enforces optimistic concurrency)
repo = RepositoryStub[User]()
repo.preload(some_user)

# Mediator stub (route to real handlers, capture published events)
mediator = MediatorStub()
mediator.register_command(CreateUserCommand, handler)
await mediator.send(cmd)
assert mediator.published_domain[0].email == "x@y.com"

# Flag client stub
flags = FlagClientStub({"payments.new-checkout": True})
flags.set("other-flag", "variant-b")

# Search repository stub (substring text matching, exact filter matching)
search = InMemorySearchRepository[ProductDoc]()
await search.index("p1", ProductDoc(name="Red shoes"))
hits, total = (await search.search(SearchQuery(text="shoes"))).value
```

### Outbox assertion

```python
from qx.testing import OutboxAssert

outbox = OutboxAssert(engine)
await outbox.assert_event_published(
    "identity.user.registered",
    where={"email": "ada@example.com"},
)
```

### Docker containers (pytest fixtures)

```python
from qx.testing import postgres_container, redis_container, nats_container

@pytest.fixture(scope="session")
def pg():
    with postgres_container() as c:
        yield c
```

---

## qx-cli — Service Scaffold

```bash
# Scaffold a new service
qx new service payments

# Output structure:
payments/
  src/payments/
    application/   # handlers, queries
    domain/        # aggregates, events
    infrastructure/  # repositories, mappings
    presentation/  # routes
    main.py
  tests/
  alembic/
  pyproject.toml
```

---

## qx-devtools — Shared Code-Quality Config

```python
from qx.devtools import write_configs
from pathlib import Path

write_configs(Path("."))   # writes ruff.toml, mypy.ini, .pre-commit-config.yaml, .editorconfig
```

---

## Capability Matrix

| Package | Stable | Key export |
|---|---|---|
| qx-core | ✅ | `Result`, `Entity`, `DomainEvent`, errors, `RequestContext` |
| qx-cqrs | ✅ | `Command`, `Query`, `Mediator`, `@command_handler`, `@query_handler` |
| qx-db | ✅ | `Repository`, `UnitOfWork`, `run_async_migrations` |
| qx-di | ✅ | `Container` |
| qx-http | ✅ | `setup_qx_app`, `Inject`, `envelope_success`, `unwrap` |
| qx-observability | ✅ | `setup_observability`, `Metrics`, `HealthRegistry` |
| qx-flags | ✅ | `FlagClient`, `InMemoryProvider`, `InMemoryFlag` |
| qx-regions | ✅ | `RegionRouter`, `StaticRegionResolver`, `DbRegionResolver` |
| qx-auth | ✅ | `JwtValidator`, `Principal`, `PolicyEvaluator`, `TokenBucket` |
| qx-cache | ✅ | `Cache`, `DistributedLock`, `IdempotencyStore` |
| qx-worker | ✅ | `WorkerRuntime` |
| qx-events | ✅ | `EventRegistry`, `MediatorEventDispatcher` |
| qx-search | ✅ | `SearchRepository`, `SearchQuery`, `OpenSearchRepository` |
| qx-eventstore | ✅ | `EventSourcedAggregate`, `EventStore` |
| qx-saga | ✅ | `Saga`, `SagaManager`, `@on`, `@on_timeout` |
| qx-projections | ✅ | `Projection`, `ProjectionRunner` |
| qx-grpc | ✅ | `create_grpc_server`, interceptors |
| qx-testing | ✅ | `RepositoryStub`, `MediatorStub`, `FlagClientStub`, `OutboxAssert` |
| qx-cli | ✅ | `qx new service <name>` |
| qx-devtools | ✅ | `write_configs` |
| qx-py | ✅ | Meta-package (installs all of the above) |
