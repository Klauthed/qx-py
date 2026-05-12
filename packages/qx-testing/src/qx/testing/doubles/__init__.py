"""Test doubles for handler isolation tests.

These let you exercise a command/query handler in isolation without standing
up the full container / mediator graph. Typical use::

    repo = RepositoryStub[User]()
    repo.preload(some_user)
    handler = GetUserHandler(repo)
    result = await handler.handle(GetUserQuery(id=some_user.id))

The stubs are deliberately *not* mocks (we don't ``assert_called_with``); they
hold a small in-memory store so the handler exercises real logic against fake
storage. This is the "test against the contract, not the implementation" school.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Generic, TypeVar
from uuid import UUID

from qx.core import (
    ConflictError,
    DomainEvent,
    Entity,
    NotFoundError,
    OffsetPage,
    OffsetPagination,
    Result,
)
from qx.cqrs import Mediator, MediatorError
from qx.cqrs.messages import Command, Query

__all__ = ["MediatorStub", "RepositoryStub"]


TEntity = TypeVar("TEntity", bound=Entity[Any])


class RepositoryStub(Generic[TEntity]):
    """In-memory stand-in for a Qx Repository.

    Behavior mirrors the real Repository's contract closely enough that
    handlers can't tell the difference for the common cases: optimistic
    concurrency, soft-delete, NotFound on missing IDs.
    """

    def __init__(self) -> None:
        self._store: dict[UUID, TEntity] = {}
        self._deleted: set[UUID] = set()

    def preload(self, *entities: TEntity) -> None:
        """Seed the stub with entities (no events emitted)."""
        for e in entities:
            self._store[e.id] = e

    async def get(self, id: UUID, *, include_deleted: bool = False) -> Result[TEntity]:
        if id in self._deleted and not include_deleted:
            return Result.failure(NotFoundError(code="not_found", message=str(id)))
        entity = self._store.get(id)
        if entity is None:
            return Result.failure(NotFoundError(code="not_found", message=str(id)))
        return Result.success(entity)

    async def exists(self, id: UUID) -> bool:
        return id in self._store and id not in self._deleted

    async def list(
        self,
        pagination: OffsetPagination = OffsetPagination(),
        *,
        include_deleted: bool = False,
    ) -> OffsetPage[TEntity]:
        items = [
            e for id_, e in self._store.items()
            if include_deleted or id_ not in self._deleted
        ]
        offset = pagination.offset
        sliced = items[offset : offset + pagination.page_size]
        return OffsetPage(
            items=sliced,
            page=pagination.page,
            page_size=pagination.page_size,
            total=len(items),
        )

    async def add(self, entity: TEntity) -> Result[TEntity]:
        if entity.id in self._store:
            return Result.failure(
                ConflictError(code="duplicate", message=f"id {entity.id} exists")
            )
        self._store[entity.id] = entity
        return Result.success(entity)

    async def save(self, entity: TEntity) -> Result[TEntity]:
        existing = self._store.get(entity.id)
        if existing is None:
            return Result.failure(NotFoundError(code="not_found", message=str(entity.id)))
        if existing.version != entity.version:
            return Result.failure(
                ConflictError(
                    code="version_conflict",
                    message=f"expected v{existing.version}, got v{entity.version}",
                )
            )
        entity.version += 1
        self._store[entity.id] = entity
        return Result.success(entity)

    async def soft_delete(self, id: UUID) -> Result[None]:
        if id not in self._store:
            return Result.failure(NotFoundError(code="not_found", message=str(id)))
        self._deleted.add(id)
        return Result.success(None)

    async def hard_delete(self, id: UUID) -> Result[None]:
        if id not in self._store:
            return Result.failure(NotFoundError(code="not_found", message=str(id)))
        self._store.pop(id, None)
        self._deleted.discard(id)
        return Result.success(None)


class MediatorStub:
    """In-memory mediator: route to registered handlers, record published events."""

    def __init__(self) -> None:
        self._cmd: dict[type[Command[Any]], Any] = {}
        self._qry: dict[type[Query[Any]], Any] = {}
        self.published_domain: list[DomainEvent] = []
        self.published_integration: list[Any] = []

    def register_command(self, msg: type[Command[Any]], handler: Any) -> None:
        self._cmd[msg] = handler

    def register_query(self, msg: type[Query[Any]], handler: Any) -> None:
        self._qry[msg] = handler

    async def send(self, message: Any) -> Any:
        if isinstance(message, Command):
            handler = self._cmd.get(type(message))
            if handler is None:
                raise MediatorError(f"no handler for {type(message).__name__}")
            return await handler.handle(message)
        if isinstance(message, Query):
            handler = self._qry.get(type(message))
            if handler is None:
                raise MediatorError(f"no handler for {type(message).__name__}")
            return await handler.handle(message)
        raise MediatorError(f"unsupported message: {type(message).__name__}")

    async def publish(self, event: DomainEvent) -> None:
        self.published_domain.append(event)
