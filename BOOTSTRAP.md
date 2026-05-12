# BOOTSTRAP.md — Bootstrapping a New Service with qx

This document is the complete guide to building a production-grade backend service using the **qx** framework. It covers local setup, core framework patterns with code examples, deployment, and the conventions that govern every qx service.

---

## Table of Contents

1. [What qx is](#1-what-qx-is)
2. [Prerequisites](#2-prerequisites)
3. [Creating your first service](#3-creating-your-first-service)
4. [Project structure](#4-project-structure)
5. [Core framework patterns](#5-core-framework-patterns)
   - [Result\<T\> — errors as values](#51-resultt--errors-as-values)
   - [Entity & AggregateRoot](#52-entity--aggregateroot)
   - [CQRS and the Mediator](#53-cqrs-and-the-mediator)
   - [DI container](#54-di-container)
   - [Database, repositories, and UnitOfWork](#55-database-repositories-and-unitofwork)
   - [Events and the transactional outbox](#56-events-and-the-transactional-outbox)
   - [HTTP layer](#57-http-layer)
   - [Worker runtime](#58-worker-runtime)
   - [Observability](#59-observability)
6. [Wiring the composition root](#6-wiring-the-composition-root)
7. [Running locally](#7-running-locally)
8. [Design guidelines and conventions](#8-design-guidelines-and-conventions)
9. [Production deployment](#9-production-deployment)
   - [Docker](#91-docker)
   - [Kubernetes](#92-kubernetes)
   - [Helm](#93-helm)
   - [Database migrations](#94-database-migrations)

---

## 1. What qx is

qx is an opinionated Python backend framework modeled after Spring Boot. It gives a fleet of services a consistent operational story — observability, transactions, idempotency, retries, and auth — without teams having to solve those problems independently.

The framework is a monorepo of 15 focused packages. Services pick what they need; everything composes through a shared DI container.

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

---

## 2. Prerequisites

- Python **3.12+**
- [`uv`](https://docs.astral.sh/uv/) — the workspace and package manager
- Docker — for the local infrastructure stack

Install uv:
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Install the qx CLI (once the framework is published to PyPI):
```bash
uv tool install qx-cli
```

Or from source within this repo:
```bash
cd /path/to/qx-python
uv sync
# qx CLI is now available as: uv run qx
```

---

## 3. Creating your first service

The CLI scaffolds a fully wired service in one command:

```bash
uv run qx new service my-service
cd my-service
```

This generates the full layer structure, a Dockerfile, Alembic config, `.env.example`, and a passing smoke test. Nothing else to set up manually.

To scaffold individual artifacts inside an existing service:

```bash
uv run qx generate aggregate  User
uv run qx generate command    RegisterUser
uv run qx generate query      GetUserById
uv run qx generate event      UserRegistered
```

---

## 4. Project structure

Every qx service follows this layout. The boundaries are enforced by convention — dependencies flow inward only.

```
my-service/
├── src/
│   └── my_service/
│       ├── main.py                    ← composition root (HTTP)
│       ├── worker.py                  ← composition root (NATS consumer)
│       ├── domain/
│       │   ├── aggregates/            ← AggregateRoot subclasses
│       │   │   └── user/
│       │   │       └── __init__.py
│       │   └── events/                ← DomainEvent / IntegrationEvent definitions
│       ├── application/
│       │   ├── commands/              ← Command + CommandHandler pairs
│       │   └── queries/               ← Query + QueryHandler pairs
│       ├── infrastructure/
│       │   └── persistence/           ← SQLAlchemy mappings + Repository impls
│       └── presentation/
│           └── routes/                ← FastAPI route handlers
├── alembic/                           ← migration scripts
├── tests/
├── Dockerfile
├── pyproject.toml
└── .env.example
```

**Layer rules:**
- `domain/` imports nothing from the project except other domain types. No SQLAlchemy, no HTTP, no qx infrastructure packages.
- `application/` imports domain and qx framework packages. Never imports `infrastructure/` or `presentation/`.
- `infrastructure/` implements ports defined in domain/application. It imports SQLAlchemy, Redis, etc.
- `presentation/` translates HTTP/gRPC requests into Commands/Queries and sends them through the Mediator.

---

## 5. Core framework patterns

### 5.1 Result\<T\> — errors as values

Handlers return `Result[T]` instead of raising exceptions for business failures. Exceptions are reserved for genuine infrastructure faults.

```python
from qx.core import Result
from qx.core.errors import NotFoundError, ValidationError

# Success
return Result.success(user_dto)

# Failure — caller gets a structured error, not a traceback
return Result.failure(NotFoundError(code="user.not_found", message="User does not exist"))

# At the handler boundary — combine with Result.from_optional
user = await repo.find_by_id(user_id)
return Result.from_optional(user, on_none=NotFoundError(code="user.not_found", message="..."))
```

`Result.__bool__` is disabled — use `.is_success` / `.is_failure` explicitly. Never do `if result:`.

Combinators for chaining:

```python
result = await mediator.send(cmd)
# map: transform the success value
user_view = result.map(lambda u: UserView.from_domain(u))
# bind: chain Result-returning functions
final = result.bind(lambda u: validate_plan(u))
```

The HTTP layer calls `unwrap(result)` which converts `Result.failure` to the appropriate HTTP status code using the error's `code` prefix.

### 5.2 Entity & AggregateRoot

Domain objects are plain Python dataclasses annotated with framework decorators.

```python
from qx.core import AggregateRoot, DomainEvent, Result
from qx.core.entities import aggregate
from qx.core.errors import ValidationError

@aggregate
class User(AggregateRoot[UUID]):
    email: str
    name: str
    is_active: bool = True

    @classmethod
    def register(cls, email: str, name: str) -> Result["User"]:
        if not email.strip():
            return Result.failure(ValidationError(code="user.email_empty", message="Email required"))
        user = cls(id=uuid4(), email=email, name=name)
        user.record_event(UserRegistered(user_id=user.id, email=email))
        return Result.success(user)

    def deactivate(self) -> None:
        self.is_active = False
        self.record_event(UserDeactivated(user_id=self.id))
```

Key rules:
- Use `@aggregate` / `@entity` instead of bare `@dataclass`. These apply `eq=False, kw_only=True` and preserve identity-based equality.
- Call `record_event()` inside aggregate methods when state changes that other parts of the system should react to. Never publish events directly.
- The UnitOfWork drains buffered events via `pull_events()` at save time.
- Soft-delete with `entity.mark_deleted(by=actor_id)` — the persistence layer translates this to the storage mechanism.
- `ValueObject` is for immutable, equality-by-value primitives (Email, Money, Address). Extend `qx.core.entities.ValueObject` (backed by Pydantic with `frozen=True, extra="forbid"`).

### 5.3 CQRS and the Mediator

The Mediator is the central dispatch hub. All application logic is invoked through it.

**Commands** — intent to change state, return `Result[T]`:

```python
from qx.core import Result
from qx.cqrs import Command, command_handler

class RegisterUserCommand(Command[UserDto]):
    email: str
    name: str

@command_handler(RegisterUserCommand)
class RegisterUserHandler:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def handle(self, cmd: RegisterUserCommand) -> Result[UserDto]:
        async with self._uow:
            result = User.register(cmd.email, cmd.name)
            if result.is_failure:
                return result
            await self._uow.users.save(result.value)
            return result.map(UserDto.from_domain)
```

**Queries** — read-only, return `Result[T]`:

```python
from qx.cqrs import Query, query_handler

class GetUserQuery(Query[UserDto]):
    user_id: UUID

@query_handler(GetUserQuery)
class GetUserHandler:
    def __init__(self, users: UserReadRepository) -> None:
        self._users = users

    async def handle(self, q: GetUserQuery) -> Result[UserDto]:
        user = await self._users.find_by_id(q.user_id)
        return Result.from_optional(user, on_none=NotFoundError(...))
```

**Registering handlers** — three modes, all coexist:

```python
# Mode 1: Decorator + module scan (preferred for feature code)
mediator.register_decorated(commands_module)

# Mode 2: Explicit (useful for tests or conditional wiring)
mediator.register_command(RegisterUserCommand, RegisterUserHandler)

# Mode 3: Type-based (when the handler already declares its generics)
mediator.register_typed(RegisterUserHandler)
```

**Pipeline behaviors** wrap every handler invocation. Pass them at `Mediator()` construction time:

```python
from qx.cqrs import LoggingBehavior, ExceptionTranslationBehavior, TransactionBehavior

mediator = Mediator(
    container,
    command_behaviors=(
        LoggingBehavior(),
        ExceptionTranslationBehavior(),
        # Add TransactionBehavior() here if you want auto-transaction for every command
    ),
    query_behaviors=(LoggingBehavior(), ExceptionTranslationBehavior()),
)
```

Custom behavior:

```python
from qx.cqrs.pipeline import Behavior, Next

class IdempotencyBehavior(Behavior):
    def __init__(self, store: IdempotencyStore) -> None:
        self._store = store

    async def handle(self, message: Any, next: Next) -> Result[Any]:
        key = getattr(message, "idempotency_key", None)
        if key and await self._store.exists(key):
            return await self._store.get_result(key)
        result = await next(message)
        if key and result.is_success:
            await self._store.store(key, result)
        return result
```

### 5.4 DI container

`qx-di` provides a custom async DI container with three lifetimes:

| Lifetime | When to use |
|---|---|
| `SINGLETON` | Shared across the entire app (Mediator, EventRegistry, Engine) |
| `SCOPED` | One instance per HTTP request (UnitOfWork, DB session) |
| `TRANSIENT` | New instance each time (handlers, usually registered automatically) |

```python
from qx.di import Container

container = Container()

# Singleton — register an already-constructed instance
container.register_instance(Mediator, mediator)

# Scoped — factory receives resolved dependencies
container.register_scoped(UnitOfWork, lambda sf: UnitOfWork(session_factory=sf))

# Transient — resolved fresh each time
container.register_transient(UserRepository, UserRepositoryImpl)

# Resolve (usually done by the Mediator at dispatch time)
repo = await container.resolve(UserRepository)
```

Child containers and scope overrides are available for advanced use cases (multi-tenant request isolation, test overrides).

### 5.5 Database, repositories, and UnitOfWork

`qx-db` wraps SQLAlchemy 2 async with imperative mapping and a generic `Repository[TEntity]`.

**Mapping** — kept in `infrastructure/persistence/`, never in the domain:

```python
from sqlalchemy import Table, Column, UUID, String, Boolean
from qx.db import mapper_registry

users_table = Table(
    "users",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column("email", String, nullable=False, unique=True),
    Column("name", String, nullable=False),
    Column("is_active", Boolean, nullable=False, default=True),
    Column("version", Integer, nullable=False, default=1),
)

mapper_registry.map_imperatively(User, users_table)
```

**Repository** — extend the abstract base:

```python
from qx.db import Repository

class UserRepository(Repository[User]):
    async def find_by_email(self, email: str) -> User | None:
        result = await self._session.execute(
            select(User).where(User.email == email)
        )
        return result.scalar_one_or_none()
```

**UnitOfWork** — wraps a session + transaction, drains aggregate events on commit:

```python
async with uow:
    user = await uow.users.find_by_id(user_id)
    user.deactivate()
    await uow.users.save(user)
    # On exit: COMMIT, events drained, outbox entries written
```

Cursor-based pagination is built in via `CursorPage[T]` and `paginate()` on the repository.

### 5.6 Events and the transactional outbox

**DomainEvent** — in-process, dispatched within the same transaction:

```python
from qx.core import DomainEvent
from dataclasses import dataclass
from uuid import UUID

@dataclass(frozen=True)
class UserRegistered(DomainEvent):
    user_id: UUID
    email: str
```

The UnitOfWork dispatches domain events via the Mediator's `publish()` before committing. Domain event handlers run in the same DB transaction as the write.

**IntegrationEvent** — crosses service boundaries via the outbox + NATS JetStream:

```python
from qx.core import IntegrationEvent

@dataclass(frozen=True)
class UserRegisteredIntegration(IntegrationEvent):
    subject: str = "user.registered"   # NATS subject
    user_id: UUID
    email: str
    tenant_id: UUID | None = None
```

The aggregate records the integration event via `record_event()`. The UnitOfWork writes it to the `outbox` table in the same transaction as the aggregate write. The `OutboxRelay` process polls and publishes to NATS JetStream. This guarantees at-least-once delivery without distributed transactions.

**Handling integration events in a worker:**

```python
from qx.cqrs import integration_handler

@integration_handler(UserRegisteredIntegration)
class SendWelcomeEmailHandler:
    async def handle(self, event: UserRegisteredIntegration) -> None:
        await self._mailer.send_welcome(event.email)
```

### 5.7 HTTP layer

`qx-http` wraps FastAPI with a standard response envelope and DI integration.

```python
from fastapi import FastAPI
from qx.http import setup_qx_app, Inject, envelope_success, unwrap

app = setup_qx_app(container, settings, metrics=metrics, health=health)

@app.post("/users", status_code=201)
async def register_user(
    cmd: RegisterUserCommand,
    mediator: Mediator = Inject(Mediator),
) -> dict:
    result = await mediator.send(cmd)
    return envelope_success(unwrap(result))
```

`unwrap(result)` maps `Result.failure` to the right HTTP status using the error code prefix:

| Error code prefix | HTTP status |
|---|---|
| `*.not_found` | 404 |
| `*.validation_*` | 422 |
| `*.unauthorized` | 401 |
| `*.forbidden` | 403 |
| `*.conflict` | 409 |
| anything else | 500 |

Every response — success or failure — shares the same envelope shape:

```json
{
  "success": true,
  "data": { ... },
  "error": null,
  "metadata": { "correlation_id": "...", "request_id": "...", "trace_id": "..." }
}
```

Health and readiness probes are auto-mounted at `/healthz` and `/readyz`. Prometheus metrics at `/metrics`.

### 5.8 Worker runtime

`qx-worker` runs NATS JetStream consumers with ack/nak/drop semantics.

```python
# worker.py — second composition root alongside main.py
from qx.worker import WorkerRuntime

async def main() -> None:
    runtime = WorkerRuntime(
        container=container,
        mediator=mediator,
        nats_url=settings.nats.url,
    )
    runtime.subscribe(UserRegisteredIntegration)
    await runtime.run()  # blocks; handles SIGTERM/SIGINT gracefully
```

The worker runtime handles retries (nak with backoff), dead-letter routing (drop to DLQ subject after max attempts), and graceful shutdown.

### 5.9 Observability

`qx-observability` wires structlog, OpenTelemetry, and Prometheus in one call:

```python
from qx.observability import setup_observability

metrics, health = setup_observability(settings)
# metrics: Prometheus counter/histogram factory
# health: HealthRegistry for /healthz and /readyz checks
```

Structured logs are JSON when `LOGGING__JSON_OUTPUT=true`. The correlation ID from `RequestContext` flows automatically through every log line and trace span.

Register health checks:

```python
health.add_check("database", lambda: db_engine.connect())
health.add_check("nats", lambda: nats_client.ping())
```

---

## 6. Wiring the composition root

`main.py` is the only place where infrastructure is constructed and wired. Everything else receives dependencies through the DI container. A complete minimal composition root:

```python
from fastapi import FastAPI
from qx.core import QxSettings
from qx.cqrs import Mediator, LoggingBehavior, ExceptionTranslationBehavior
from qx.db import DatabaseSettings, SessionFactory, UnitOfWork, create_engine, make_session_factory
from qx.db.outbox import DefaultOutboxRecorder
from qx.di import Container
from qx.events import EventRegistry, MediatorEventDispatcher
from qx.http import setup_qx_app
from qx.observability import setup_observability

from my_service.application import register_handlers
from my_service.presentation import register_routes


def build_app() -> FastAPI:
    settings = QxSettings(app={"name": "my-service"})  # type: ignore[arg-type]
    metrics, health = setup_observability(settings)

    container = Container()

    # Infrastructure
    db_settings = DatabaseSettings()
    engine = create_engine(db_settings)
    session_factory = make_session_factory(engine)
    container.register_instance(SessionFactory, session_factory)

    # Mediator
    mediator = Mediator(
        container,
        command_behaviors=(LoggingBehavior(), ExceptionTranslationBehavior()),
        query_behaviors=(LoggingBehavior(), ExceptionTranslationBehavior()),
    )
    container.register_instance(Mediator, mediator)

    # Events
    registry = EventRegistry()
    container.register_instance(EventRegistry, registry)
    dispatcher = MediatorEventDispatcher(mediator)
    outbox = DefaultOutboxRecorder()
    container.register_scoped(UnitOfWork, lambda sf: UnitOfWork(session_factory=sf, dispatcher=dispatcher, outbox=outbox))

    # Handlers (must come after DI registrations above)
    register_handlers(mediator, container)

    app = setup_qx_app(container, settings, metrics=metrics, health=health)
    register_routes(app)
    return app


app = build_app()
```

> See `examples/identity-service/src/identity_service/main.py` for the full reference implementation.

---

## 7. Running locally

### Start infrastructure

```bash
docker compose -f deploy/docker-compose.yaml up -d
```

This starts: **Postgres**, **Redis**, **NATS** (with JetStream), **Prometheus**, **Tempo**, **Grafana**, **MailHog**, **MinIO**.

### Apply migrations

```bash
cd my-service
uv run alembic upgrade head
```

### Start the API

```bash
uv run uvicorn my_service.main:app --reload
```

### Start the worker (separate terminal)

```bash
uv run python -m my_service.worker
```

### Run tests

```bash
# Unit tests only (no external services needed)
uv run pytest packages/ examples/ -q

# Include integration tests (requires docker-compose stack)
uv run pytest -m integration
```

---

## 8. Design guidelines and conventions

### What qx services look like

| Concern | How qx handles it |
|---|---|
| Business failure | `Result.failure(...)` — never raise |
| Infrastructure fault | Raise normally — let the framework catch and log |
| State changes | Methods on `AggregateRoot` that call `record_event()` |
| Side effects | Integration events via outbox — never call external services from a handler directly |
| Cross-cutting concerns | Mediator pipeline behaviors |
| Shared state between layers | DI container — inject, don't `import` instances |

### Naming conventions

- Aggregates: `PascalCase` noun — `User`, `Order`, `Subscription`
- Commands: `VerbNounCommand` — `RegisterUserCommand`, `CancelOrderCommand`
- Queries: `GetNounBy*Query` — `GetUserByIdQuery`, `ListOrdersQuery`
- Domain events: past tense noun — `UserRegistered`, `OrderCancelled`
- Integration events: same as domain events, separate class — `UserRegisteredIntegration`
- Handlers: `NounVerbHandler` — `RegisterUserHandler`, `GetUserByIdHandler`
- Repositories: `NounRepository` — `UserRepository`, `OrderRepository`
- Error codes: `noun.verb_description` — `user.not_found`, `order.already_cancelled`

### What NOT to do

- **No business logic in routes.** Routes build a command/query and call `mediator.send()`. Nothing else.
- **No domain imports in infrastructure.** Infrastructure implements interfaces; it does not extend domain objects with persistence concerns.
- **No direct DB access in handlers.** Handlers use the UnitOfWork and repositories. They never hold a reference to a session or engine.
- **No `raise` for expected outcomes.** If "user not found" is an expected business condition, return `Result.failure(NotFoundError(...))`. Only raise for bugs and infrastructure failures.
- **No `datetime.now()` calls.** Use `from qx.core.entities import _utcnow` as the single source of truth. Monkeypatch it in tests.
- **No singleton state in handlers.** Handlers are TRANSIENT — constructed fresh per dispatch. Shared state belongs in SINGLETON-registered services.
- **No direct NATS publishing from handlers.** Always go through the outbox. Direct publishing bypasses the transactional guarantee.

### Error hierarchy

```
Error (base)
├── ValidationError        → 422
├── NotFoundError          → 404
├── ConflictError          → 409
├── UnauthorizedError      → 401
├── ForbiddenError         → 403
└── InfrastructureError    → 500
```

Define service-specific errors by subclassing the appropriate base. The code field (`user.not_found`) is the stable client-facing identifier — the message is human-readable and may change.

### Settings

Use `QxSettings` for configuration. Nest service-specific settings as typed Pydantic models:

```python
from qx.core import QxSettings
from pydantic import BaseModel

class SmtpSettings(BaseModel):
    host: str = "localhost"
    port: int = 587

class MyServiceSettings(QxSettings):
    smtp: SmtpSettings = SmtpSettings()
```

Environment variables use `__` as a nested separator: `SMTP__HOST=mail.example.com`.

---

## 9. Production deployment

### 9.1 Docker

Each service gets its own `Dockerfile` (scaffolded by the CLI):

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY src/ src/
CMD ["uv", "run", "uvicorn", "my_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

Build and push:

```bash
docker build -t my-org/my-service:latest .
docker push my-org/my-service:latest
```

### 9.2 Kubernetes

`deploy/k8s/` contains bare manifests for three process types every qx service runs:

| Manifest | Process | Description |
|---|---|---|
| `deployment-api.yaml` | API | FastAPI + uvicorn workers |
| `deployment-worker.yaml` | Worker | NATS consumer runtime |
| `deployment-outbox-relay.yaml` | OutboxRelay | Polls outbox table → publishes to NATS |

Key K8s patterns used:
- **HPA** on the API deployment (CPU/RPS-based).
- **Pre-deploy migration Job** — runs `alembic upgrade head` before the rollout.
- **ExternalSecret** — pulls DB credentials from the secrets store (Vault / AWS Secrets Manager / etc.).
- **ServiceMonitor** — Prometheus Operator scrape config for `/metrics`.

Apply:
```bash
kubectl apply -f deploy/k8s/
```

### 9.3 Helm

`deploy/helm/qx-service/` is a parametric Helm chart that works for any qx service. Configure it via `values.yaml`:

```yaml
image:
  repository: my-org/my-service
  tag: "1.2.3"

env:
  DATABASE__URL: "postgresql+asyncpg://..."
  NATS__URL: "nats://nats:4222"

resources:
  api:
    requests: { cpu: "100m", memory: "128Mi" }
    limits: { cpu: "500m", memory: "512Mi" }

autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 10
```

Deploy:

```bash
helm upgrade --install my-service deploy/helm/qx-service \
  --namespace my-service \
  --create-namespace \
  -f my-values.yaml
```

The chart includes:
- Rolling deploy strategy (zero-downtime).
- Security-hardened pod spec (`runAsNonRoot`, `readOnlyRootFilesystem`, dropped capabilities).
- External Secrets Operator integration for database credentials.
- Alembic migration as a `pre-install/pre-upgrade` Helm hook — migrations run before new pods start.
- Prometheus Operator `ServiceMonitor` auto-configured from `values.yaml`.

### 9.4 Database migrations

Migrations use Alembic, configured in `alembic.ini` at the service root.

```bash
# Create a new migration
uv run alembic revision --autogenerate -m "add users table"

# Apply
uv run alembic upgrade head

# Rollback one
uv run alembic downgrade -1
```

In production, the migration runs as a Kubernetes Job (or Helm pre-upgrade hook) before the new pod version starts. This enforces the rule: migrations must be backward-compatible with the previous version of the service (add columns with defaults; never drop columns in the same release as the code that removes the reference).

---

## Reference

| Resource | Path |
|---|---|
| Reference service | `examples/identity-service/` |
| Architecture deep-dive | `docs/architecture.md` |
| CQRS guide | `docs/cqrs-guide.md` |
| Deployment guide | `docs/deployment.md` |
| v2 design (auth, gRPC, search) | `docs/v2-design.md` |
| v3 design (sagas, event-sourcing) | `docs/v3-design.md` |
| Backend engineering handbook | `docs/Backend_Junior_To_Senior_Engineering.md` |
