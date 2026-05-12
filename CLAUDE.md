# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install / sync all workspace packages
uv sync

# Run all tests
uv run pytest packages/ examples/ -q

# Run a single package's tests
uv run pytest packages/qx-core/tests/ -q

# Run a single test file
uv run pytest packages/qx-cqrs/tests/test_mediator.py -q

# Lint
uv run ruff check .

# Format
uv run ruff format .

# Type check
uv run mypy packages/

# CLI
uv run qx version
uv run qx new service <name>
uv run qx generate aggregate <Name>
uv run qx generate command <Name>
uv run qx generate query <Name>
uv run qx generate event <Name>

# Local dev stack (Postgres, Redis, NATS, Prometheus, Grafana, etc.)
docker compose -f deploy/docker-compose.yaml up -d
```

## Architecture

This is a monorepo managed with `uv` workspaces. All 15 framework packages live under `packages/` and share the `qx.*` namespace (namespace packages — no `__init__.py` at the `qx` root level). Each package has its own `pyproject.toml` built with `hatchling`.

### Layer model (strict, inward-only dependencies)

```
Presentation  (qx-http, qx-grpc)
     ↓
Application   (qx-cqrs — Command/Query handlers, Mediator)
     ↓
Domain        (qx-core — Entity, AggregateRoot, Result, Error)
     ↑
Infrastructure (qx-db, qx-cache, qx-events, …)
```

Infrastructure implements domain-defined interfaces (repository ABCs, dispatcher protocols). Domain has no infrastructure imports.

### CQRS + Mediator

`qx-cqrs` provides the `Mediator` singleton. Handlers are registered three ways (all coexist):

1. **Decorator + scan** — `@command_handler(SomeCommand)` on the class, then `mediator.register_decorated(module)` to walk it.
2. **Explicit** — `mediator.register_command(SomeCommand, SomeHandler)`.
3. **Type-based** — `class H(CommandHandler[SomeCommand, SomeDto])` + `mediator.register_typed(H)`.

Pipeline behaviors wrap every handler invocation. The reference composition order is: `LoggingBehavior → ExceptionTranslationBehavior` (outermost first). Add `TransactionBehavior` when a handler needs a DB write. Behaviors are passed at `Mediator()` construction time.

### Result pattern

Handlers return `Result[T]`, never raise for business errors. Use `Result.success(value)` / `Result.failure(SomeError(...))`. The HTTP layer (`unwrap()`) maps failures to structured JSON 4xx/5xx responses. `Result.__bool__` is intentionally disabled — always check `.is_success` / `.is_failure`.

### Entity / AggregateRoot

Use the `@entity` and `@aggregate` decorators (not bare `@dataclass`). They apply `eq=False, kw_only=True` while preserving identity-based equality from the base classes. `AggregateRoot.record_event(event)` buffers domain events; the Unit of Work calls `pull_events()` at save time and dispatches them in the same transaction or via the outbox.

### DI container

`qx-di` provides `Container` with three lifetimes: `SINGLETON`, `SCOPED`, `TRANSIENT`. `UnitOfWork` is registered as `SCOPED` (one per HTTP request). `Mediator` resolves handler instances from the container at dispatch time, so handler scope is governed by registration, not by mediator internals.

### Outbox / events

Domain events are dispatched in-process (same transaction). Integration events cross service boundaries via NATS JetStream through the transactional outbox (`UnitOfWork` → `OutboxRelay` → NATS). `qx-worker` runs the NATS consumer with ack/nak/drop semantics.

### CLI scaffolding

Templates are Jinja2 files under `packages/qx-cli/src/qx/cli/scaffolds/`. Path tokens `__service_pkg__` and `__name_snake__` are replaced at render time. The `service` scaffold generates a fully wired service (layers, Dockerfile, Alembic config, smoke test). Per-artifact scaffolds (`aggregate`, `command`, `query`, `event`) append files into an existing service tree.

### Reference service

`examples/identity-service/` is the canonical end-to-end example. `src/identity_service/main.py` is the composition root — read it to understand how DI, Mediator, UnitOfWork, observability, and FastAPI are wired together. `main.py` boots the HTTP server; `worker.py` boots the NATS consumer.

### Test markers

`integration` — requires external services (Postgres, Redis, NATS). These are excluded from the default `pytest` run; use `-m integration` to include. `slow` — any test taking > 1s.

### Key conventions

- Migrations live in `alembic/versions/` per service; excluded from ruff (`**/migrations/**`).
- Generated protobuf / OpenAPI code goes in `_generated/`; also excluded from ruff.
- `qx.core.entities._utcnow` is the single source for "now" — monkeypatch it in tests that control time.
- `ValueObject` extends `pydantic.BaseModel` with `frozen=True, extra="forbid", strict=True`.
- `QxSettings` uses pydantic-settings with nested model support for typed config blocks.
