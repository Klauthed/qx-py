# qx-testing

Testing helpers for Qx services — testcontainers, mediator and repository doubles, outbox assertions, and pytest fixture factories.

## What lives here

- **`qx.testing.postgres_container` / `redis_container` / `nats_container`** — context managers that start Docker containers via testcontainers. Each yields the running container with a `get_connection_url()` or equivalent accessor. Docker Desktop on macOS is detected automatically via `~/.docker/run/docker.sock`.
- **`qx.testing.MediatorStub`** — in-memory `Mediator` replacement. Pre-register handler return values with `stub.on(CommandType, result)`. Asserts that each stubbed command was sent exactly once.
- **`qx.testing.RepositoryStub`** — in-memory `Repository[TEntity]` backed by a plain `dict`. Supports `add`, `get`, `list`, `save`, and `soft_delete`. No database required.
- **`qx.testing.OutboxAssert`** — queries the real `qx_outbox_events` table and provides `assert_event_published(event_name, where={...})`. Used in integration tests to verify that the transactional outbox was populated correctly.
- **`qx.testing.container_factory`** — pytest fixture that builds and tears down a DI `Container` with all singletons initialized.
- **`qx.testing.mediator_factory`** — pytest fixture that creates a `Mediator` wired to a test container.
- **`qx.testing.http_client_factory`** — pytest fixture that wraps a FastAPI app in a `TestClient` with a fresh Prometheus registry per test.

## Usage

### Integration test with a real Postgres container

```python
import pytest
from qx.testing.containers import postgres_container
from qx.testing.assertions import OutboxAssert

@pytest.fixture(scope="session")
def db_url():
    with postgres_container() as pg:
        yield pg.get_connection_url()

def test_create_user_writes_outbox(client, outbox: OutboxAssert):
    client.post("/v1/users", json={"email": "a@b.com", "name": "Ada"})

    import asyncio
    asyncio.run(
        outbox.assert_event_published(
            "identity.user.registered",
            where={"email": "a@b.com"},
        )
    )
```

### Unit test with doubles

```python
from qx.testing import MediatorStub, RepositoryStub
from qx.core import Result

async def test_create_user_handler():
    users = RepositoryStub(User)
    uow = FakeUnitOfWork(users)
    handler = CreateUserHandler(uow)

    result = await handler.handle(CreateUserCommand(email="a@b.com", name="Ada"))
    assert result.is_success
    assert len(users.all()) == 1
```

## Design rules

- Use `NullPool` for all test engines that call `asyncio.run()` in fixtures — asyncpg connections are bound to the event loop that created them and cannot be reused across `asyncio.run()` boundaries.
- `RepositoryStub` raises the same `NotFoundError` / `ConflictError` shapes as the real repository so handler tests exercise error paths without a database.
- `MediatorStub.assert_all_sent()` can be called in `teardown` to catch unexpected commands that were sent but not stubbed.
