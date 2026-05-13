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

import dataclasses
from typing import TYPE_CHECKING, Any, TypeVar, cast

from qx.core import (
    ConflictError,
    DomainEvent,
    Entity,
    NotFoundError,
    OffsetPage,
    OffsetPagination,
    Result,
)
from qx.cqrs import MediatorError
from qx.cqrs.messages import Command, Query
from qx.search.repository import SearchHit, SearchQuery, SearchRepository

if TYPE_CHECKING:
    from uuid import UUID

__all__ = ["InMemorySearchRepository", "MediatorStub", "RepositoryStub"]


TEntity = TypeVar("TEntity", bound=Entity[Any])


class RepositoryStub[TEntity: Entity[Any]]:
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
        pagination: OffsetPagination = OffsetPagination(),  # noqa: B008
        *,
        include_deleted: bool = False,
    ) -> OffsetPage[TEntity]:
        items = [e for id_, e in self._store.items() if include_deleted or id_ not in self._deleted]
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
            return Result.failure(ConflictError(code="duplicate", message=f"id {entity.id} exists"))
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


TDoc = TypeVar("TDoc")


class InMemorySearchRepository[TDoc](SearchRepository[TDoc]):
    """In-memory search repository for unit tests.

    Stores documents in a plain dict; text matching is substring-based;
    filter matching is exact equality. Fast and zero-dependency — no running
    OpenSearch required.

    Typical use::

        repo = InMemorySearchRepository[ProductDoc]()
        await repo.index("p1", ProductDoc(name="Red shoes", category="footwear"))
        result = await repo.search(SearchQuery(text="shoes"))
        assert result.value[1] == 1  # total
    """

    def __init__(self) -> None:
        self._store: dict[str, tuple[Any, dict[str, Any]]] = {}

    def preload(self, doc_id: str, document: TDoc) -> None:
        """Seed the store without going through async index()."""
        self._store[doc_id] = (document, self._to_dict(document))

    async def index(self, doc_id: str, document: TDoc) -> Result[None]:
        self._store[doc_id] = (document, self._to_dict(document))
        return Result.success(None)

    async def delete(self, doc_id: str) -> Result[None]:
        self._store.pop(doc_id, None)
        return Result.success(None)

    async def search(self, query: SearchQuery) -> Result[tuple[list[SearchHit[TDoc]], int]]:
        hits: list[SearchHit[TDoc]] = []
        for doc, source in self._store.values():
            if not self._matches_filters(source, query):
                continue
            score = self._text_score(source, query)
            if query.text and score == 0.0:
                continue
            hits.append(SearchHit(doc=doc, score=score, source=source))

        if query.sort:
            for field, order in reversed(query.sort):

                def _key(h: SearchHit[TDoc], f: str = field) -> Any:
                    return h.source.get(f, "")

                hits.sort(key=_key, reverse=(order.lower() == "desc"))
        else:
            hits.sort(key=lambda h: h.score, reverse=True)

        total = len(hits)
        offset = (query.page - 1) * query.page_size
        return Result.success((hits[offset : offset + query.page_size], total))

    @staticmethod
    def _matches_filters(source: dict[str, Any], query: SearchQuery) -> bool:
        return all(source.get(k) == v for k, v in query.filters.items())

    @staticmethod
    def _text_score(source: dict[str, Any], query: SearchQuery) -> float:
        if not query.text:
            return 1.0
        needle = query.text.lower()
        return sum(1.0 for v in source.values() if isinstance(v, str) and needle in v.lower())

    @staticmethod
    def _to_dict(document: Any) -> dict[str, Any]:
        if hasattr(document, "model_dump"):
            return cast("dict[str, Any]", document.model_dump(mode="json"))
        if dataclasses.is_dataclass(document) and not isinstance(document, type):
            return dataclasses.asdict(document)
        return {k: v for k, v in vars(document).items() if not k.startswith("_")}
