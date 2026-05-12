# identity-service (example)

A reference Qx service demonstrating the full vertical slice:

```
HTTP → Command → Handler → UoW(Repository.add + Outbox INSERT) → COMMIT
                                                          ↓
                                       OutboxRelay → NATS JetStream
                                                          ↓
                                          WorkerRuntime → Integration Handler
```

## What it shows

- **CQRS**: `CreateUserCommand`, `ChangeEmailCommand`, `GetUserQuery`, `ListUsersQuery`.
- **Aggregate** (`User`) with invariants enforced via factory + mutation methods.
- **Two event types** recorded by the aggregate: an in-process `UserRegistered`
  (in case any in-service handler needs it) and a cross-service
  `UserRegisteredIntegration` for downstream consumers.
- **Imperative SQLAlchemy mapping** keeping the domain pure.
- **UnitOfWork** routing events to mediator (in-process) and outbox
  (cross-process), atomically with the aggregate write.
- **HTTP envelope** + correlation id propagation.
- **Worker** that consumes the same integration event back, just to close
  the loop.

## Run locally

```bash
# 1) Start the supporting stack
docker compose -f ../../deploy/docker-compose.yaml up -d

# 2) Migrations
uv run alembic upgrade head

# 3) HTTP API
uv run uvicorn identity_service.main:app --reload --port 8000

# 4) Worker (in a separate terminal)
uv run python -m identity_service.worker

# 5) Outbox relay (in a third terminal). Or run it as a sidecar in prod.
uv run python -m qx.events.outbox_relay  # (run-script TBD; see V2)
```

Try it:

```bash
curl -X POST localhost:8000/v1/users \
  -H 'Content-Type: application/json' \
  -d '{"email": "ada@example.com", "name": "Ada"}'
```

You should see the response envelope, the row in `users`, the row in
`qx_outbox_events`, and (once the relay runs) a corresponding log
line from the worker.

## File map

```
src/identity_service/
  domain/aggregates/user/        ← entity + domain events
  application/
    commands/                    ← CreateUser, ChangeEmail
    queries/                     ← GetUser, ListUsers
    integration_handlers/        ← OnUserRegistered
  infrastructure/
    persistence/user/            ← Table mapping + repository
  presentation/routes/users.py   ← HTTP routes
  main.py                        ← FastAPI app composition
  worker.py                      ← worker entrypoint
```
