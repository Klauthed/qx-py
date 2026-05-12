# qx-cli

Scaffolding, code generation, and local-dev orchestration for the Qx framework. Invoked as `qx` once installed, or `uv run qx` during development.

## What lives here

- **`qx new service NAME`** — scaffold a complete new Qx service: directory structure, pyproject.toml, DI bootstrap, FastAPI app factory, Alembic config, Docker Compose overrides, and a `README.md`.
- **`qx generate aggregate NAME`** — add a domain aggregate with `Entity`, events, and a stub repository to an existing service.
- **`qx generate command NAME`** — add a `Command` and handler class with the standard boilerplate.
- **`qx generate query NAME`** — add a `Query` and handler class.
- **`qx generate endpoint`** — add a FastAPI route file wired to the Mediator.
- **`qx generate event NAME`** — add an `IntegrationEvent` and its handler skeleton.
- **`qx dev up`** — start the local Docker Compose stack (Postgres, Redis, NATS, Prometheus, Tempo, Grafana, MailHog, MinIO).
- **`qx dev down`** — stop and remove the local stack.
- **`qx version`** — print the framework version.

## Usage

```bash
# Install (or use via uv in the workspace)
pip install qx-cli

# Scaffold a new service
qx new service payments-service
cd payments-service

# Add domain objects
qx generate aggregate Payment
qx generate command ProcessPayment
qx generate query GetPayment
qx generate event PaymentProcessed

# Start local infra
qx dev up

# Run
uv run uvicorn payments_service.main:app --reload
```

## Generated structure

`qx new service` produces:

```
my-service/
├── src/my_service/
│   ├── domain/aggregates/        # domain model
│   ├── application/
│   │   ├── commands/             # command handlers
│   │   └── queries/              # query handlers
│   ├── infrastructure/
│   │   └── persistence/          # repositories, SA mapping
│   └── presentation/routes/      # FastAPI routes
├── tests/
│   ├── unit/
│   └── integration/
├── alembic/
├── pyproject.toml
└── README.md
```

## Design rules

- Generated code follows the same conventions as `examples/identity-service` — it is the canonical reference for what the generator should produce.
- `qx dev up/down` is a thin wrapper around `docker compose` pointing at `deploy/docker-compose.yaml` in the workspace root. It does not manage application containers, only infrastructure.
