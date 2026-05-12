"""Common transport / repository types: pagination, sorting, filtering.

These types are shared between HTTP query parsing, repository methods, and
response envelopes. Defining them once here removes the temptation to invent
ad-hoc shapes in each feature slice — which always leads to client confusion.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, ClassVar, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "SortDirection",
    "Sort",
    "Filter",
    "FilterOp",
    "OffsetPagination",
    "OffsetPage",
    "CursorPagination",
    "CursorPage",
    "Page",
]

T = TypeVar("T")

SortDirection = Literal["asc", "desc"]

FilterOp = Literal[
    "eq",
    "neq",
    "in",
    "not_in",
    "gt",
    "gte",
    "lt",
    "lte",
    "contains",
    "starts_with",
    "ends_with",
    "is_null",
    "is_not_null",
]


class Sort(BaseModel):
    """Single sort directive. Compose into a list for multi-key sort."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    field: str
    direction: SortDirection = "asc"


class Filter(BaseModel):
    """Single filter directive. Repository layer validates ``field`` against an allow-list."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    field: str
    op: FilterOp
    value: Any = None  # None is valid for is_null / is_not_null


class OffsetPagination(BaseModel):
    """Request shape for offset-based pagination."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=200)
    sort: tuple[Sort, ...] = ()
    filters: tuple[Filter, ...] = ()

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class OffsetPage(BaseModel, Generic[T]):
    """Response shape for offset-based pagination."""

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    items: Sequence[T]
    page: int
    page_size: int
    total: int

    @property
    def total_pages(self) -> int:
        if self.page_size == 0:
            return 0
        return (self.total + self.page_size - 1) // self.page_size

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages

    @property
    def has_previous(self) -> bool:
        return self.page > 1


class CursorPagination(BaseModel):
    """Request shape for cursor-based pagination.

    Prefer cursors for hot endpoints — they remain stable as data shifts, and
    they avoid the deep-offset performance cliff. Offset is fine for admin
    backoffices where users want to jump to "page 47".
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    cursor: str | None = None
    limit: int = Field(default=20, ge=1, le=200)
    sort: tuple[Sort, ...] = ()
    filters: tuple[Filter, ...] = ()


class CursorPage(BaseModel, Generic[T]):
    """Response shape for cursor-based pagination.

    ``next_cursor`` is opaque — the repository encodes whatever it needs (sort
    key + tiebreaker) and clients must not parse it.
    """

    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    items: Sequence[T]
    next_cursor: str | None = None
    has_next: bool = False


# Convenience union for handlers that don't care which shape they return.
Page = OffsetPage[T] | CursorPage[T]
