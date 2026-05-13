"""SearchRepository — abstract base and OpenSearch implementation.

The abstraction deliberately stops at "search for indexed documents".
Aggregations, suggesters, and percolators live in service-specific code
that uses the raw client. Build the framework for the boring 80% and let
the long tail use the underlying tool.

Implementations:

- ``OpenSearchRepository`` — production; backed by an ``AsyncOpenSearch`` client.
- ``InMemorySearchRepository`` — test double; held in ``qx.testing``.
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, TypeVar, cast

from opensearchpy.exceptions import NotFoundError as _OSNotFoundError
from opensearchpy.exceptions import TransportError as _OSTransportError
from qx.core import InfrastructureError, Result

if TYPE_CHECKING:
    from collections.abc import Sequence

    from opensearchpy import AsyncOpenSearch

_AnyDoc = Any  # alias used in cast() calls below to keep lines short

__all__ = ["OpenSearchRepository", "SearchHit", "SearchQuery", "SearchRepository"]


TDoc = TypeVar("TDoc")


class SearchQuery:
    """Minimal search query.

    For anything beyond text + filters + sort (aggregations, suggesters,
    painless scripts) use the raw ``AsyncOpenSearch`` client directly.
    """

    def __init__(
        self,
        *,
        text: str | None = None,
        filters: dict[str, Any] | None = None,
        page: int = 1,
        page_size: int = 20,
        sort: Sequence[tuple[str, str]] = (),
    ) -> None:
        self.text = text
        self.filters = filters or {}
        self.page = page
        self.page_size = page_size
        self.sort = sort


class SearchHit[TDoc]:
    def __init__(self, doc: TDoc, score: float, source: dict[str, Any]) -> None:
        self.doc = doc
        self.score = score
        self.source = source


class SearchRepository[TDoc](ABC):
    """Abstract base for typed search repositories."""

    @abstractmethod
    async def index(self, doc_id: str, document: TDoc) -> Result[None]: ...

    @abstractmethod
    async def delete(self, doc_id: str) -> Result[None]: ...

    @abstractmethod
    async def search(self, query: SearchQuery) -> Result[tuple[list[SearchHit[TDoc]], int]]:
        """Search → ``(hits, total)`` (total is approximate for large result sets)."""


# ---------------------------------------------------------------------------
# OpenSearchRepository
# ---------------------------------------------------------------------------


class OpenSearchRepository[TDoc](SearchRepository[TDoc]):
    """Concrete search repository backed by an ``AsyncOpenSearch`` client.

    Subclass to add domain-specific finders::

        class ProductRepository(OpenSearchRepository[ProductDoc]):
            index_name = "products"
            doc_type = ProductDoc

            async def find_by_category(self, category: str, page: int = 1):
                return await self.search(
                    SearchQuery(filters={"category": category}, page=page)
                )

    **Serialization:** Pydantic models use ``model_dump(mode="json")``;
    dataclasses use ``dataclasses.asdict``; plain objects fall back to
    ``__dict__``. Override ``_serialize`` / ``_deserialize`` for custom shapes.

    **Error handling:** All OpenSearch transport errors become
    ``InfrastructureError`` Results. A ``delete`` of a non-existent document
    is treated as a no-op (idempotent).
    """

    def __init__(
        self,
        client: AsyncOpenSearch,
        index: str,
        doc_type: type[TDoc],
    ) -> None:
        self._client = client
        self._index = index
        self._doc_type = doc_type

    async def index(self, doc_id: str, document: TDoc) -> Result[None]:
        try:
            await self._client.index(
                index=self._index,
                id=doc_id,
                body=self._serialize(document),
            )
            return Result.success(None)
        except _OSTransportError as exc:
            return Result.failure(
                InfrastructureError(
                    code="search.index_failed",
                    message=f"failed to index document {doc_id}: {exc}",
                    cause=exc,
                )
            )

    async def delete(self, doc_id: str) -> Result[None]:
        try:
            await self._client.delete(index=self._index, id=doc_id)
            return Result.success(None)
        except _OSNotFoundError:
            return Result.success(None)  # idempotent
        except _OSTransportError as exc:
            return Result.failure(
                InfrastructureError(
                    code="search.delete_failed",
                    message=f"failed to delete document {doc_id}: {exc}",
                    cause=exc,
                )
            )

    async def search(self, query: SearchQuery) -> Result[tuple[list[SearchHit[TDoc]], int]]:
        try:
            response = await self._client.search(
                index=self._index,
                body=self._build_dsl(query),
                from_=(query.page - 1) * query.page_size,
                size=query.page_size,
            )
        except _OSTransportError as exc:
            return Result.failure(
                InfrastructureError(
                    code="search.query_failed",
                    message=f"search query failed: {exc}",
                    cause=exc,
                )
            )

        hits = [
            SearchHit(
                doc=self._deserialize(h["_source"]),
                score=h.get("_score") or 0.0,
                source=h["_source"],
            )
            for h in response["hits"]["hits"]
        ]
        total: int = response["hits"]["total"]["value"]
        return Result.success((hits, total))

    # ------------------------------------------------------------------
    # Overridable serialization hooks
    # ------------------------------------------------------------------

    def _serialize(self, document: TDoc) -> dict[str, Any]:
        d = cast("_AnyDoc", document)
        if hasattr(d, "model_dump"):
            return cast("dict[str, Any]", d.model_dump(mode="json"))
        if dataclasses.is_dataclass(d) and not isinstance(d, type):
            return dataclasses.asdict(d)
        return {k: v for k, v in vars(d).items() if not k.startswith("_")}

    def _deserialize(self, source: dict[str, Any]) -> TDoc:
        dt = cast("_AnyDoc", self._doc_type)
        if hasattr(dt, "model_validate"):
            return cast("TDoc", dt.model_validate(source))
        return cast("TDoc", dt(**source))

    # ------------------------------------------------------------------
    # Query DSL builder
    # ------------------------------------------------------------------

    def _build_dsl(self, query: SearchQuery) -> dict[str, Any]:
        must: list[dict[str, Any]] = []
        filters: list[dict[str, Any]] = []

        if query.text:
            must.append({"multi_match": {"query": query.text, "type": "best_fields"}})

        for field, value in query.filters.items():
            filters.append({"term": {field: value}})

        if must or filters:
            bool_clause: dict[str, Any] = {}
            if must:
                bool_clause["must"] = must
            if filters:
                bool_clause["filter"] = filters
            dsl: dict[str, Any] = {"query": {"bool": bool_clause}}
        else:
            dsl = {"query": {"match_all": {}}}

        if query.sort:
            dsl["sort"] = [{field: {"order": order}} for field, order in query.sort]

        return dsl
