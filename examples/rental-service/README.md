# rental-service

Reference Qx service demonstrating **vertical-slice architecture**.

Three feature slices — `user`, `house`, `rent` — each owns its commands,
queries, domain model, infrastructure adapters, and HTTP routes.

## Project layout

```
src/rental_service/
  shared/         # MetaData singleton + outbox table
  user/           # User slice: register & profile
  house/          # House slice: list & browse properties
  rent/           # Rent slice: create & view bookings
  main.py         # composition root — wires DI, mounts all routers
```

## Key patterns

- `main.py` calls `mediator.register_decorated(rental_service)` — handlers
  across all slices are auto-discovered; no per-slice registration needed.
- Routes are mounted per slice: `app.include_router(user_router, prefix="/v1")`.
- New slices added with `qx generate slice <name>`.

## Quickstart

```bash
uv sync
docker compose -f ../../deploy/docker-compose.yaml up -d
uv run alembic upgrade head
uv run uvicorn rental_service.main:app --reload
```

## Testing

```bash
uv run pytest          # domain unit tests + smoke test
```
