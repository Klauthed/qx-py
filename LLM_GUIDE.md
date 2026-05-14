# Qx Framework — LLM Guide

**Paste this file into your AI tool's context** when building a service with Qx.
It gives the model the patterns, rules, and recipes it needs to generate correct code
on the first try.

---

## What Qx Is

Qx is an opinionated Python backend framework for building production-grade
microservices. It provides:

- **CQRS + Mediator** — commands and queries routed through a pipeline
- **Domain-driven design primitives** — `Entity`, `Identifier`, `DomainEvent`, `Result`
- **SQLAlchemy repository + UnitOfWork** — optimistic concurrency, outbox pattern
- **FastAPI integration** — DI injection, standardised JSON envelope, probes
- **Feature flags** — OpenFeature-based `FlagClient`
- **Multi-region routing** — 307 redirect writes to the tenant's home region
- **Worker runtime** — NATS JetStream consumer
- **Testing doubles** — `RepositoryStub`, `MediatorStub`, `FlagClientStub`

Install everything: `pip install qx-py`  
Individual packages: `pip install qx-core qx-cqrs qx-db qx-http qx-di qx-observability`

---

## Project Structure

Qx supports two layout strategies. Choose one per service; don't mix them.

### Layered (default)

Best for microservices focused on a single domain aggregate.
Scaffold: `qx new service <name>`

```
src/<service_name>/
  application/
    commands/         # one file per command + handler
    queries/          # one file per query + handler
    __init__.py       # register_handlers(mediator, container)
  domain/
    aggregates/       # Entity subclasses + domain logic
    events/           # DomainEvent subclasses
    __init__.py       # register_events(registry)
  infrastructure/
    persistence/
      <aggregate>/
        mapping.py    # SQLAlchemy Table + metadata
        repository.py # Repository[Entity] subclass
  presentation/
    routes/           # FastAPI APIRouter per aggregate
    __init__.py       # register_routes(app)
  main.py             # composition root — build_app()
```

`main.py` calls `register_handlers(mediator, container)` which walks
`application/` to discover all `@command_handler`/`@query_handler` classes.

### Vertical Slice (--slices)

Best for monolith-style services with multiple feature domains (user, house, rent…).
Scaffold: `qx new service <name> --slices --domain <starter_slice>`

```
src/<service_name>/
  shared/               # single MetaData instance + outbox table
  application/
    <slice_a>/          # e.g. user/
      commands/         # one file per command + handler
      queries/          # one file per query + handler
    <slice_b>/          # e.g. house/
      commands/
      queries/
  domain/               # all aggregates, value objects, domain events (flat)
  infrastructure/       # all SQLAlchemy tables + repositories (flat)
  presentation/         # one FastAPI APIRouter file per slice (flat)
  main.py               # composition root
```

`main.py` calls `mediator.register_decorated(<package>)` which walks the
entire service package, discovering all decorated handlers automatically.
Routes are mounted explicitly per slice: `app.include_router(user_router, prefix="/v1")`.

**Adding a slice:**
```bash
qx generate slice payment
qx generate command payment/CapturePayment
qx generate query payment/GetPaymentStatus
# Then mount in main.py:
#   from <svc>.presentation.payment import router as payment_router
#   app.include_router(payment_router, prefix="/v1")
```

See `examples/rental-service/` for a complete VS example with `user`, `house`, `rent` slices.

---

## Critical Rules

These are the things that trip up models unfamiliar with Qx:

1. **`NotFoundError` is NOT a Python exception** — it is a frozen dataclass.
   - `return Result.failure(NotFoundError(code="...", message="..."))` ✅
   - `raise NotFoundError(...)` ❌ — will crash with `TypeError`
   - To raise: `raise error.as_exception()` → raises `ErrorException`
   - In tests: `pytest.raises(ErrorException)` not `pytest.raises(NotFoundError)`

2. **`FlagClient` uses class-level configuration** — no constructor args.
   ```python
   FlagClient.configure(InMemoryProvider({...}))  # once at startup
   container.register_instance(FlagClient, FlagClient())  # then inject
   ```
   `InMemoryProvider` requires `InMemoryFlag` objects, not plain values:
   ```python
   InMemoryProvider({
       "my-flag": InMemoryFlag("off", {"on": True, "off": False}),
   })
   ```

3. **`Repository.get(id)` not `find_by_id`** — the base class method is `get`.

4. **Handlers declare deps in `__init__`, not `handle`** — the DI container
   resolves constructor arguments. Never resolve from the container manually.

5. **`async with self._uow:` wraps the whole handler body** — commits on clean
   exit, rolls back on exception.

6. **`unwrap(result)` raises `ErrorException`** on failure — FastAPI's exception
   handlers translate it to the correct HTTP status + envelope.

7. **Always pass `scope=scope` to `mediator.send`** inside route handlers so the
   UoW and scoped deps are bound to the request scope.

---

## Recipes

### 1 — Command + Handler

```python
# application/commands/create_order.py
from __future__ import annotations
from uuid import UUID
from qx.core import ConflictError, Result
from qx.cqrs import Command, command_handler
from qx.db import UnitOfWork

from orders.domain.aggregates.order import Order, OrderId
from orders.infrastructure.persistence.order.repository import OrderRepository


class CreateOrderCommand(Command[Order]):
    customer_id: UUID
    amount_cents: int


@command_handler(CreateOrderCommand)
class CreateOrderHandler:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def handle(self, cmd: CreateOrderCommand) -> Result[Order]:
        async with self._uow:
            repo = OrderRepository(self._uow.session)
            order = Order.create(customer_id=cmd.customer_id, amount=cmd.amount_cents)
            result = await repo.add(order)
            if result.is_failure:
                return result
            return Result.success(order)
```

### 2 — Query + Handler

```python
# application/queries/get_order.py
from __future__ import annotations
from uuid import UUID
from qx.core import NotFoundError, Result
from qx.cqrs import Query, query_handler
from qx.db import UnitOfWork

from orders.infrastructure.persistence.order.repository import OrderRepository
from orders.domain.aggregates.order import Order


class GetOrderQuery(Query[Order]):
    order_id: UUID


@query_handler(GetOrderQuery)
class GetOrderHandler:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def handle(self, query: GetOrderQuery) -> Result[Order]:
        async with self._uow:
            repo = OrderRepository(self._uow.session)
            return await repo.get(query.order_id)
```

### 3 — Entity + Domain Event

```python
# domain/aggregates/order.py
from __future__ import annotations
from uuid import UUID, uuid4
from qx.core import Entity, Identifier, DomainEvent, ValidationError, Result


class OrderId(Identifier): pass

class OrderPlaced(DomainEvent):
    order_id: str
    amount_cents: int


class Order(Entity[OrderId]):
    customer_id: UUID
    amount_cents: int
    status: str = "pending"

    @classmethod
    def create(cls, *, customer_id: UUID, amount: int) -> "Order":
        if amount <= 0:
            raise ValidationError(
                code="order.invalid_amount", message="amount must be positive"
            ).as_exception()
        order = cls(id=OrderId(value=uuid4()), customer_id=customer_id, amount_cents=amount)
        order.record_event(OrderPlaced(order_id=str(order.id.value), amount_cents=amount))
        return order
```

### 4 — Repository

```python
# infrastructure/persistence/order/mapping.py
from sqlalchemy import Column, MetaData, Table, Integer, String
from uuid import UUID as PyUUID
from sqlalchemy.dialects.postgresql import UUID

metadata = MetaData()

orders_table = Table(
    "orders", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("customer_id", UUID(as_uuid=True), nullable=False),
    Column("amount_cents", Integer, nullable=False),
    Column("status", String(32), nullable=False, default="pending"),
    Column("version", Integer, nullable=False, default=1),
)


# infrastructure/persistence/order/repository.py
from qx.db import Repository
from orders.domain.aggregates.order import Order
from orders.infrastructure.persistence.order.mapping import orders_table


class OrderRepository(Repository[Order]):
    entity_cls = Order
    table = orders_table
```

### 5 — HTTP Route

```python
# presentation/routes/orders.py
from typing import Any
from uuid import UUID
from fastapi import APIRouter, Depends, status
from qx.cqrs import Mediator
from qx.di import Scope
from qx.http import Inject, envelope_success, scope_dep, unwrap

from orders.application.commands.create_order import CreateOrderCommand
from orders.application.queries.get_order import GetOrderQuery

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_order(
    cmd: CreateOrderCommand,
    mediator: Mediator = Inject(Mediator),
    scope: Scope = Depends(scope_dep),
) -> dict[str, Any]:
    return envelope_success(unwrap(await mediator.send(cmd, scope=scope)))


@router.get("/{order_id}")
async def get_order(
    order_id: UUID,
    mediator: Mediator = Inject(Mediator),
    scope: Scope = Depends(scope_dep),
) -> dict[str, Any]:
    return envelope_success(unwrap(await mediator.send(GetOrderQuery(order_id=order_id), scope=scope)))
```

### 6 — main.py (composition root)

```python
from __future__ import annotations
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from qx.core import QxSettings
from qx.cqrs import ExceptionTranslationBehavior, LoggingBehavior, Mediator
from qx.db import DatabaseSettings, SessionFactory, UnitOfWork, create_engine, make_session_factory
from qx.db.outbox import DefaultOutboxRecorder
from qx.di import Container
from qx.events import EventRegistry, MediatorEventDispatcher
from qx.http import setup_qx_app
from qx.observability import setup_observability

from orders.application import register_handlers
from orders.domain import register_events
from orders.presentation import register_routes

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from fastapi import FastAPI


def build_app() -> FastAPI:
    settings = QxSettings(app={"name": "orders-service"})
    metrics, health = setup_observability(settings)
    container = Container()

    db_settings = DatabaseSettings()
    engine = create_engine(db_settings)
    session_factory = make_session_factory(engine)
    container.register_instance(SessionFactory, session_factory)

    mediator = Mediator(
        container,
        command_behaviors=(LoggingBehavior(), ExceptionTranslationBehavior()),
        query_behaviors=(LoggingBehavior(), ExceptionTranslationBehavior()),
    )
    container.register_instance(Mediator, mediator)

    registry = EventRegistry()
    register_events(registry)
    container.register_instance(EventRegistry, registry)

    dispatcher = MediatorEventDispatcher(mediator)
    outbox = DefaultOutboxRecorder()
    container.register_scoped(UnitOfWork, lambda sf: UnitOfWork(session_factory=sf, dispatcher=dispatcher, outbox=outbox))

    register_handlers(mediator, container)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await engine.dispose()

    app = setup_qx_app(container, settings, metrics=metrics, health=health, extra_lifespan=lifespan)
    register_routes(app)
    return app


app = build_app()
```

### 7 — Unit test with stubs

```python
# tests/test_create_order_unit.py
import pytest
from uuid import uuid4
from qx.testing import RepositoryStub, MediatorStub

from orders.application.commands.create_order import CreateOrderCommand, CreateOrderHandler
from orders.domain.aggregates.order import Order


@pytest.fixture
def repo():
    return RepositoryStub[Order]()


async def test_create_order_succeeds(repo: RepositoryStub) -> None:
    # Arrange — no real DB needed
    # (inject repo directly; for full handler isolation use a UoW stub)
    cmd = CreateOrderCommand(customer_id=uuid4(), amount_cents=1000)

    # Act — instantiate handler directly with the stub
    # handler = CreateOrderHandler(uow_stub)
    # result = await handler.handle(cmd)

    # Assert
    # assert result.is_success
    # assert isinstance(result.value, Order)
    pass  # replace with your UoW stub pattern
```

### 8 — Feature flag in a handler

```python
from qx.flags import FlagClient

@command_handler(CheckoutCommand)
class CheckoutHandler:
    def __init__(self, uow: UnitOfWork, flags: FlagClient) -> None:
        self._uow = uow
        self._flags = flags

    async def handle(self, cmd: CheckoutCommand) -> Result[CheckoutResult]:
        use_new = await self._flags.bool("payments.new-checkout", default=False)
        async with self._uow:
            if use_new:
                return await self._new_checkout(cmd)
            return await self._legacy_checkout(cmd)
```

Wire at startup (once):
```python
from qx.flags import FlagClient, InMemoryProvider, InMemoryFlag

FlagClient.configure(InMemoryProvider({
    "payments.new-checkout": InMemoryFlag("off", {"on": True, "off": False}),
}))
container.register_instance(FlagClient, FlagClient())
```

In tests use `FlagClientStub`:
```python
from qx.testing import FlagClientStub
flags = FlagClientStub({"payments.new-checkout": True})
handler = CheckoutHandler(uow_stub, flags)
```

---

## Environment Variables

| Variable | Package | Default | Description |
|---|---|---|---|
| `QX_DB__URL` | qx-db | `postgresql+asyncpg://...` | Async DB URL |
| `QX_CACHE__URL` | qx-cache | `redis://localhost:6379/0` | Redis URL |
| `QX_REGION__NAME` | qx-regions | `"default"` | Current region name |
| `QX_REGION__URLS` | qx-regions | `{}` | JSON map region→base URL |
| `QX_JWT__ISSUER` | qx-auth | — | JWT issuer URL |
| `QX_JWT__AUDIENCE` | qx-auth | — | JWT audience |
| `QX_LOG__LEVEL` | qx-observability | `"info"` | Log level |
| `QX_OTEL__ENDPOINT` | qx-observability | — | OTLP endpoint |

---

## Common Mistakes & Fixes

| Mistake | Fix |
|---|---|
| `raise NotFoundError(...)` | `return Result.failure(NotFoundError(...))` or `raise NotFoundError(...).as_exception()` |
| `FlagClient(provider)` | `FlagClient.configure(provider); FlagClient()` |
| `InMemoryProvider({"flag": True})` | `InMemoryProvider({"flag": InMemoryFlag("off", {"on": True, "off": False})})` |
| `repo.find_by_id(id)` | `repo.get(id)` |
| Missing `scope=scope` in `mediator.send` | Add `scope: Scope = Depends(scope_dep)` param and pass `scope=scope` |
| `pytest.raises(NotFoundError)` | `pytest.raises(ErrorException)` |
| Importing inside `if TYPE_CHECKING` then using at runtime | Move import to module level |
| Calling `container.resolve(X)` inside a handler | Declare `X` as a constructor param instead |

---

## What to Reach For

| Goal | Use |
|---|---|
| Multi-step workflow across services | `qx-saga`: `Saga`, `SagaManager`, `@on` |
| Audit log / event replay | `qx-eventstore`: `EventSourcedAggregate`, `EventStore` |
| Denormalised read models | `qx-projections`: `Projection`, `ProjectionRunner` |
| Full-text / faceted search | `qx-search`: `SearchRepository`, `SearchQuery` |
| JWT-protected routes | `qx-auth`: `JwtValidator`, `Principal`, `require_permission` |
| Request deduplication | `qx-cache`: `IdempotencyStore` |
| Distributed lock | `qx-cache`: `DistributedLock` |
| Async message consumer | `qx-worker`: `WorkerRuntime` |
| gRPC API | `qx-grpc`: `create_grpc_server` |
| Scaffold a new service (layered) | `qx new service <name>` |
| Scaffold a new service (vertical slices) | `qx new service <name> --slices --domain <slice>` |
| Add a slice to existing VS service | `qx generate slice <name>` |
