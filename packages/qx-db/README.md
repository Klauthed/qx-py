# qx-db

Database layer for the Qx framework — SQLAlchemy 2 async, a generic `Repository[TEntity]`, `UnitOfWork` with domain-event routing, transactional outbox, and cursor/offset pagination.

## What lives here

- **`qx.db.Repository[TEntity]`** — generic async CRUD with optimistic concurrency (`version` column), soft-delete, tenant filtering, and allow-listed filter/sort fields.
- **`qx.db.UnitOfWork`** — wraps a SQLAlchemy session; commits the aggregate write and the outbox `INSERT` in one transaction, then drains and dispatches domain events.
- **`qx.db.OutboxRecorder` / `DefaultOutboxRecorder`** — persists `IntegrationEvent` payloads to `qx_outbox_events` for reliable delivery.
- **`qx.db.SessionFactory`** — factory for `AsyncSession` instances; injected into repositories.
- **`qx.db.make_metadata` / `make_registry`** — SQLAlchemy `MetaData` and `registry` helpers for imperative mapping (no declarative base).
- **`qx.db.standard_audit_columns` / `uuid_column` / `jsonb_column`** — column helpers for consistent schema conventions.
- **`qx.db.build_cursor_page` / `encode_cursor` / `decode_cursor`** — opaque cursor pagination utilities.
- **`qx.db.include_outbox_table`** — attaches the `qx_outbox_events` table to your `MetaData`.

## Defining a repository

```python
from qx.db import Repository
from sqlalchemy import Table

class UserRepository(Repository[User]):
    entity_cls = User
    table: Table  # set to your SQLAlchemy Table at class or instance level
    filterable_fields = {"email", "name", "is_active"}
    sortable_fields = {"created_at", "email"}
    tenanted = False  # set True for multi-tenant tables
```

## Unit of Work

```python
from qx.db import UnitOfWork

class CreateUserHandler:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def handle(self, cmd: CreateUserCommand) -> Result[UserDto]:
        async with self._uow.begin() as ctx:
            user_result = User.register(cmd.email, cmd.name)
            user = unwrap(user_result)
            await ctx.users.add(user)
        # commit + outbox INSERT + event dispatch all happened above
        return Result.success(UserDto.from_domain(user))
```

## Design rules

- **Optimistic concurrency** — `save()` uses `WHERE id = ? AND version = ?`; returns `ConflictError` (not an exception) on mismatch.
- **Soft-delete by default** — `list()` and `get()` exclude rows where `deleted_at IS NOT NULL` unless `include_deleted=True`.
- **Allow-listed filters** — `filterable_fields` and `sortable_fields` are class-level sets; querying an unlisted field raises `ValueError` to prevent controller logic leaking into SQL.
- **Imperative mapping** — domain entities are plain dataclasses with no SQLAlchemy decorators. The mapping lives in the infrastructure layer, not the domain.
- **UUID v7** — `uuid_column()` defaults to UUID v7 primary keys for sequential B-tree index locality.
