# Getting Started

Build your first qx service in under five minutes. We'll create a tiny
service that exposes one HTTP endpoint, dispatches a command through the
mediator, and writes to the outbox.

## Prerequisites

- Python 3.12+
- Docker (for local Postgres + NATS via `deploy/docker-compose.yaml`)
- `uv` (recommended) or `pip`

## 1. Create your service directory

```bash
mkdir hello-qx && cd hello-qx
```

Initialize a `pyproject.toml`:

```toml
[project]
name = "hello-qx"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "qx-core",
    "qx-di",
    "qx-cqrs",
    "qx-http",
    "qx-observability",
    "uvicorn[standard]>=0.32.0",
]
```

(In a real workspace, you'd use the CLI: `qx new service hello-qx`.)

## 2. Define a command and a handler

```python
# hello/application/commands.py
from typing import ClassVar
from qx.core import Result
from qx.cqrs import Command, command_handler

class GreetCommand(Command[str]):
    name: str

@command_handler(GreetCommand)
class GreetHandler:
    async def handle(self, cmd: GreetCommand) -> Result[str]:
        if not cmd.name.strip():
            from qx.core import ValidationError
            return Result.failure(
                ValidationError(code="name.empty", message="name is required")
            )
        return Result.success(f"hello, {cmd.name}")
```

## 3. Wire the service

```python
# hello/main.py
from fastapi import FastAPI

from qx.core import QxSettings
from qx.cqrs import Mediator
from qx.di import Container
from qx.http import Inject, envelope_success, setup_qx_app, unwrap
from qx.observability import setup_observability

from hello.application import commands

def make_app() -> FastAPI:
    settings = QxSettings()
    metrics, health = setup_observability(settings)

    container = Container()
    mediator = Mediator(container)
    mediator.register_decorated(commands)  # walks the module, finds @command_handler classes
    container.register_instance(Mediator, mediator)

    app = setup_qx_app(container, settings, metrics=metrics, health=health)

    @app.post("/greet")
    async def greet(cmd: commands.GreetCommand, m: Mediator = Inject(Mediator)):
        result = await m.send(cmd)
        return envelope_success(unwrap(result))

    return app

app = make_app()
```

## 4. Run it

```bash
uv run uvicorn hello.main:app --reload
```

Hit it:

```bash
curl -X POST localhost:8000/greet \
  -H 'Content-Type: application/json' \
  -d '{"name": "ada"}'
```

You should see:

```json
{
  "success": true,
  "data": "hello, ada",
  "error": null,
  "metadata": {
    "correlation_id": "...",
    "request_id": "...",
    "trace_id": null
  }
}
```

The framework gave you, for free:

- A correlation id that flows through logs and traces.
- A standard envelope on every response, success or failure.
- `/healthz`, `/readyz`, `/metrics` already mounted.
- Structured logs in JSON (if `LOGGING__JSON_OUTPUT=true`).
- A pipeline you can extend with behaviors (authz, retries, transactions).

## 5. Try an error

```bash
curl -X POST localhost:8000/greet -H 'Content-Type: application/json' -d '{"name": ""}'
```

```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "name.empty",
    "message": "name is required",
    "details": {}
  },
  "metadata": { "correlation_id": "..." }
}
```

400 Bad Request, envelope shape preserved, code is the framework's stable
identifier. Clients can pattern-match on `error.code` without parsing the
human-readable message.

## Next steps

- Add a Postgres repository: see `examples/identity-service`.
- Add events: define an `IntegrationEvent`, record it on an aggregate, watch
  the outbox fill up.
- Add a worker: `examples/identity-service` includes one that consumes
  `user.registered` events.
- Read `architecture.md` for the why behind each piece.
