"""SearchRepository abstract base.

Mirrors the shape of ``qx.db.Repository`` so application code reading
from the search index has the same surface as one reading from Postgres.
Reduces context-switch overhead and lets CQRS-style projections plug in
cleanly: the same query model can be backed by either source.

Two implementations ship in V2:

- ``OpenSearchRepository`` (this module): for elastic / OpenSearch.
- ``InMemorySearchRepository`` (test double): held in ``qx.testing``.

The abstraction deliberately stops at "search for indexed documents".
Aggregations, suggesters, percolators all live in service-specific code
that uses the raw client (``OpenSearchClient`` here). Build the framework
for the boring 80% and let the long tail use the underlying tool.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from collections.abc import Sequence

    from qx.core import Result

__all__ = ["SearchHit", "SearchQuery", "SearchRepository"]


TDoc = TypeVar("TDoc")


class SearchQuery:
    """A simple search query shape.

    Intentionally minimal — anything more elaborate goes through the raw
    OpenSearch client. The point of this abstraction is to keep most queries
    boring and consistent.
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
