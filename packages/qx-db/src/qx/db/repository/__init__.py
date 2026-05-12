"""Generic Repository base class.

Repositories are the boundary between domain code and persistence. They:

- Accept and return domain entities/aggregates (never raw rows).
- Translate filter/sort/pagination shapes into SQL.
- Honor tenant isolation (when the entity is tenanted).
- Honor soft-delete by default (callers can opt in to including deleted).
- Pull domain events out of aggregates on save and hand them to the unit of work
  (which routes them to in-process handlers and/or the outbox).

This base implements the boring 80%. Aggregate-specific repositories subclass
and add domain-flavored finders (``find_by_email``, ``find_active_admins``,
etc.). Avoid generic ``find(where=...)`` APIs leaking up — they couple HTTP
controllers to SQL syntax and prevent the repo from validating queries.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Generic, TypeVar
from uuid import UUID

from sqlalchemy import Column, Table, and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement, Select

from qx.core import (
    ConflictError,
    Entity,
    Filter,
    NotFoundError,
    OffsetPage,
    OffsetPagination,
    Result,
    current_context,
    utcnow,
)
from qx.core.types.pagination import FilterOp, Sort

__all__ = ["Repository"]


TEntity = TypeVar("TEntity", bound=Entity[Any])


class Repository(Generic[TEntity]):
    """Base repository with typed CRUD against a single aggregate table.

    Subclass and supply the entity type and table at class-definition time::

        class UserRepository(Repository[User]):
            entity_cls = User
            table = users_table
            # Allow-list of filterable fields. Reject anything else.
            filterable_fields = {"email", "name", "is_admin"}
            sortable_fields = {"created_at", "email"}
    """

    entity_cls: type[TEntity]
    table: Table
    filterable_fields: set[str] = set()
    sortable_fields: set[str] = {"created_at"}

    # Subclasses can override to disable tenant filtering for global-scoped tables.
    tenanted: bool = True

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ============================================================
    # Reads
    # ============================================================

    async def get(
        self,
        id: UUID,
        *,
        include_deleted: bool = False,
    ) -> Result[TEntity]:
        stmt = select(self.table).where(self.table.c.id == id)
        stmt = self._scope_query(stmt, include_deleted=include_deleted)
        row = (await self._session.execute(stmt)).mappings().first()
        return Result.from_optional(
            self._row_to_entity(row) if row else None,
            on_none=NotFoundError(
                code=f"{self.entity_cls.__name__.lower()}.not_found",
                message=f"{self.entity_cls.__name__} {id} not found",
                details={"id": str(id)},
            ),
        )

    async def exists(self, id: UUID) -> bool:
        stmt = select(func.count()).select_from(self.table).where(self.table.c.id == id)
        stmt = self._scope_query(stmt, include_deleted=False)
        return ((await self._session.execute(stmt)).scalar() or 0) > 0

    async def list(
        self,
        pagination: OffsetPagination = OffsetPagination(),
        *,
        include_deleted: bool = False,
    ) -> OffsetPage[TEntity]:
        stmt = select(self.table)
        stmt = self._scope_query(stmt, include_deleted=include_deleted)
        stmt = self._apply_filters(stmt, pagination.filters)
        stmt = self._apply_sort(stmt, pagination.sort)

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await self._session.execute(count_stmt)).scalar() or 0

        stmt = stmt.limit(pagination.page_size).offset(pagination.offset)
        rows = (await self._session.execute(stmt)).mappings().all()
        items = [self._row_to_entity(r) for r in rows]
        return OffsetPage(
            items=items,
            page=pagination.page,
            page_size=pagination.page_size,
            total=int(total),
        )

    # ============================================================
    # Writes
    # ============================================================

    async def add(self, entity: TEntity) -> Result[TEntity]:
        """Insert a new aggregate. Sets created_at/created_by from context."""
        ctx = current_context()
        values = self._entity_to_dict(entity)
        values.setdefault("created_at", utcnow())
        values.setdefault("created_by", ctx.user_id)
        if self.tenanted and "tenant_id" not in values:
            values["tenant_id"] = ctx.tenant_id
        values.setdefault("version", 1)
        await self._session.execute(self.table.insert().values(**values))
        return Result.success(entity)

    async def save(self, entity: TEntity) -> Result[TEntity]:
        """Update an existing aggregate with optimistic concurrency on ``version``."""
        ctx = current_context()
        current_version = entity.version
        values = self._entity_to_dict(entity)
        values["version"] = current_version + 1
        values["updated_at"] = utcnow()
        values["updated_by"] = ctx.user_id
        stmt = (
            update(self.table)
            .where(self.table.c.id == entity.id, self.table.c.version == current_version)
            .values(**values)
        )
        result = await self._session.execute(stmt)
        if result.rowcount == 0:
            return Result.failure(
                ConflictError(
                    code=f"{self.entity_cls.__name__.lower()}.version_conflict",
                    message=(
                        f"{self.entity_cls.__name__} {entity.id} was modified concurrently "
                        f"(expected version {current_version})."
                    ),
                    details={"id": str(entity.id), "version": current_version},
                )
            )
        entity.version = current_version + 1
        return Result.success(entity)

    async def soft_delete(self, id: UUID) -> Result[None]:
        ctx = current_context()
        stmt = (
            update(self.table)
            .where(
                self.table.c.id == id,
                self.table.c.deleted_at.is_(None),
            )
            .values(deleted_at=utcnow(), deleted_by=ctx.user_id)
        )
        result = await self._session.execute(stmt)
        if result.rowcount == 0:
            return Result.failure(
                NotFoundError(
                    code=f"{self.entity_cls.__name__.lower()}.not_found",
                    message=f"{self.entity_cls.__name__} {id} not found or already deleted",
                )
            )
        return Result.success(None)

    async def hard_delete(self, id: UUID) -> Result[None]:
        """Physical delete. Avoid except for GDPR / right-to-be-forgotten paths."""
        stmt = delete(self.table).where(self.table.c.id == id)
        result = await self._session.execute(stmt)
        if result.rowcount == 0:
            return Result.failure(
                NotFoundError(
                    code=f"{self.entity_cls.__name__.lower()}.not_found",
                    message=f"{self.entity_cls.__name__} {id} not found",
                )
            )
        return Result.success(None)

    # ============================================================
    # Internals — subclasses can override
    # ============================================================

    def _row_to_entity(self, row: Any) -> TEntity:
        """Default: pass-through to the entity constructor.

        Subclasses override when the table schema doesn't match the entity
        shape (renamed columns, JSON-packed value objects, etc.).
        """
        return self.entity_cls(**dict(row))

    def _entity_to_dict(self, entity: TEntity) -> dict[str, Any]:
        """Default: shallow vars(). Override for non-trivial mappings."""
        result = {k: v for k, v in vars(entity).items() if not k.startswith("_")}
        return result

    def _scope_query(self, stmt: Select[Any], *, include_deleted: bool) -> Select[Any]:
        if not include_deleted and "deleted_at" in self.table.c:
            stmt = stmt.where(self.table.c.deleted_at.is_(None))
        if self.tenanted and "tenant_id" in self.table.c:
            tid = current_context().tenant_id
            # No tenant in context means service-internal call; don't filter.
            # Production deployments should enforce a tenant via middleware.
            if tid is not None:
                stmt = stmt.where(self.table.c.tenant_id == tid)
        return stmt

    def _apply_filters(self, stmt: Select[Any], filters: Sequence[Filter]) -> Select[Any]:
        for f in filters:
            if f.field not in self.filterable_fields:
                # Silently dropping a filter is a security hazard. Refuse.
                raise ValueError(
                    f"Field {f.field!r} is not filterable on "
                    f"{self.entity_cls.__name__} (allow-list: {sorted(self.filterable_fields)})"
                )
            col: Column[Any] = self.table.c[f.field]
            stmt = stmt.where(_apply_op(col, f.op, f.value))
        return stmt

    def _apply_sort(self, stmt: Select[Any], sorts: Sequence[Sort]) -> Select[Any]:
        sortable = self.sortable_fields | {"created_at"}
        order_cols: list[Any] = []
        for s in sorts:
            if s.field not in sortable:
                raise ValueError(
                    f"Field {s.field!r} is not sortable on {self.entity_cls.__name__}"
                )
            col = self.table.c[s.field]
            order_cols.append(col.desc() if s.direction == "desc" else col.asc())
        # Always include id as a stable tiebreaker for deterministic pagination.
        order_cols.append(self.table.c.id.asc())
        return stmt.order_by(*order_cols)


def _apply_op(col: ColumnElement[Any], op: FilterOp, value: Any) -> ColumnElement[bool]:
    if op == "eq":
        return col == value
    if op == "neq":
        return col != value
    if op == "in":
        return col.in_(value)
    if op == "not_in":
        return ~col.in_(value)
    if op == "gt":
        return col > value
    if op == "gte":
        return col >= value
    if op == "lt":
        return col < value
    if op == "lte":
        return col <= value
    if op == "contains":
        return col.ilike(f"%{value}%")
    if op == "starts_with":
        return col.ilike(f"{value}%")
    if op == "ends_with":
        return col.ilike(f"%{value}")
    if op == "is_null":
        return col.is_(None)
    if op == "is_not_null":
        return col.is_not(None)
    raise ValueError(f"Unsupported filter op: {op}")
