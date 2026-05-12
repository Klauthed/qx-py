# Getting Started

Build your first qx service in under ten minutes using the CLI scaffold.

## Prerequisites

- Python 3.14+
- [`uv`](https://docs.astral.sh/uv/) — package and project manager
- Docker — for the local infrastructure stack (Postgres, Redis, NATS, Grafana)

Install `uv` if you don't have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Verify your environment:

```bash
uv run qx doctor
```

## 1. Install the CLI

```bash
uv tool install qx-cli
```

Or, inside a workspace that already declares `qx-cli` as a dependency:

```bash
uv sync
uv run qx version
```

## 2. Scaffold a service

```bash
qx new service hello-qx
cd hello-qx
uv sync
```

This generates a complete, runnable service skeleton:

```
hello-qx/
├── src/hello_qx/
│   ├── main.py                  # FastAPI app factory + DI wiring
│   ├── application/             # Commands and queries go here
│   ├── domain/                  # Aggregates and domain events
│   ├── infrastructure/          # Repositories and persistence mappings
│   └── presentation/routes/     # HTTP endpoints
├── alembic/                     # Database migrations
├── tests/
├── Dockerfile
└── pyproject.toml
```

## 3. Start the local stack

```bash
qx dev up
```

This starts Postgres, Redis, NATS JetStream, Prometheus, Grafana, and Tempo via Docker Compose. Wait for the health checks to pass (usually ~5 s), then run the service:

```bash
uv run uvicorn hello_qx.main:app --reload
```

The service starts on `http://localhost:8000`. Three endpoints are already live:

| Endpoint | Purpose |
|---|---|
| `GET /healthz` | Liveness probe |
| `GET /readyz` | Readiness probe |
| `GET /metrics` | Prometheus metrics |

## 4. Add a command

```bash
qx generate command CreateGreeting
```

This creates `src/hello_qx/application/commands/create_greeting.py` with a `CreateGreetingCommand` class and a `CreateGreetingHandler` stub. Fill in the handler:

```python
async def handle(self, command: CreateGreetingCommand) -> Result[CreateGreetingDto]:
    if not command.name.strip():
        return Result.failure(
            ValidationError(code="name.empty", message="name is required")
        )
    return Result.success(CreateGreetingDto(message=f"hello, {command.name}"))
```

## 5. Add an endpoint

```bash
qx generate endpoint /greet --handler CreateGreeting
```

This generates `src/hello_qx/presentation/routes/create_greeting.py` — a FastAPI router that deserializes the request body, dispatches through the `Mediator`, and wraps the result in the standard envelope.

Register the router in `main.py` (the scaffold wires this automatically if you regenerate; for existing services, add one line):

```python
from hello_qx.presentation.routes import create_greeting
app.include_router(create_greeting.router)
```

## 6. Try it

```bash
curl -X POST localhost:8000/greet \
  -H 'Content-Type: application/json' \
  -d '{"name": "ada"}'
```

```json
{
  "success": true,
  "data": {"message": "hello, ada"},
  "error": null,
  "metadata": {
    "correlation_id": "...",
    "request_id": "...",
    "trace_id": null
  }
}
```

Every response — success or failure — uses this envelope shape. Clients pattern-match on `error.code`, never on HTTP status text.

Error path:

```bash
curl -X POST localhost:8000/greet \
  -H 'Content-Type: application/json' \
  -d '{"name": ""}'
```

```json
{
  "success": false,
  "data": null,
  "error": {"code": "name.empty", "message": "name is required", "details": {}},
  "metadata": {"correlation_id": "..."}
}
```

## 7. Add a domain aggregate (with database)

```bash
qx generate aggregate Greeting
```

This generates:
- `src/hello_qx/domain/aggregates/greeting/` — aggregate root + domain events
- `src/hello_qx/infrastructure/persistence/greeting/` — SQLAlchemy table mapping + repository
- `alembic/versions/create_greeting.py` — migration stub (fill in columns, then `alembic upgrade head`)

## What you get for free

| Concern | How qx handles it |
|---|---|
| Request tracing | `correlation_id` injected at the FastAPI middleware layer, flows through logs and OTel spans |
| Structured logging | `structlog` with JSON output in production (`LOG__JSON_OUTPUT=true`) |
| Metrics | Prometheus counter + histogram per command/query, already in the pipeline |
| Transactional outbox | `UnitOfWork.track(aggregate)` routes domain events to `qx_outbox_events` in the same DB transaction |
| Idempotency | `IdempotencyBehavior` in the mediator pipeline; plug in `qx-cache` for the store |
| Health probes | `/healthz` and `/readyz` wired by `setup_qx_app()` |

## Next steps

- **Full vertical slice** — see [`examples/identity-service/`](../examples/identity-service/) for a complete user-registration service: HTTP → Command → Aggregate → Repository → UnitOfWork → Outbox → Worker → Integration Event.
- **Architecture deep-dive** — [`docs/architecture.md`](architecture.md) explains the layered design and every major decision.
- **CQRS patterns** — [`docs/cqrs-guide.md`](cqrs-guide.md) covers commands vs. queries vs. events, pipeline ordering, and anti-patterns.
- **Deployment** — [`docs/deployment.md`](deployment.md) covers local → Docker → Kubernetes → Helm.
