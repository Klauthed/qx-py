"""SQLAlchemy imperative mapping.

We deliberately do **not** use declarative-style ORM (no ``DeclarativeBase``,
no ``Mapped`` on entities). Reasons:

- Keeps domain entities decoupled from SQLAlchemy. They remain plain
  ``@dataclass``-style objects that the framework (and tests) can construct
  trivially without an active session.
- Lets us define multiple persistence representations of the same aggregate
  (e.g., a write model and a denormalized read model) without inheritance
  gymnastics.
- Mirrors how clean-architecture-discipline services in other ecosystems
  (Java/Spring's JPA EntityManager, .NET's EF Core Fluent API) handle the
  domain/infrastructure boundary.

The trade-off: a small amount of boilerplate per aggregate in ``mapping.py``
files declaring ``Table`` + ``registry.map_imperatively(Entity, table)``. Worth it.

This module supplies the framework-level ``mapping_registry()`` and a few
common column types so services stay consistent.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import registry as sa_registry

__all__ = [
    "make_registry",
    "make_metadata",
    "standard_audit_columns",
    "uuid_column",
    "jsonb_column",
]


def make_metadata(schema: str | None = None) -> MetaData:
    """Build a metadata object with consistent naming conventions.

    Predictable index/constraint names matter for Alembic — without these,
    autogenerate produces unstable names that flip between revisions.
    """
    return MetaData(
        schema=schema,
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        },
    )


def make_registry(metadata: MetaData | None = None) -> sa_registry:
    """Create an SQLAlchemy registry for imperative mapping."""
    md = metadata or make_metadata()
    return sa_registry(metadata=md)


def standard_audit_columns(
    *,
    include_tenant: bool = True,
    include_deletion: bool = True,
) -> list[Column[Any]]:
    """Common audit columns mirroring the ``Entity`` base fields.

    Use in every aggregate table::

        users = Table(
            "users",
            metadata,
            Column("id", PG_UUID(as_uuid=True), primary_key=True),
            Column("email", String(255), nullable=False, unique=True),
            *standard_audit_columns(),
        )
    """
    cols: list[Column[Any]] = [
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=True),
        Column("created_by", PG_UUID(as_uuid=True), nullable=True),
        Column("updated_by", PG_UUID(as_uuid=True), nullable=True),
        Column("version", Integer, nullable=False, default=1),
    ]
    if include_tenant:
        cols.append(Column("tenant_id", PG_UUID(as_uuid=True), nullable=True, index=True))
    if include_deletion:
        cols.extend(
            [
                Column("deleted_at", DateTime(timezone=True), nullable=True, index=True),
                Column("deleted_by", PG_UUID(as_uuid=True), nullable=True),
            ]
        )
    return cols


def uuid_column(name: str, *, primary_key: bool = False, **kwargs: Any) -> Column[UUID]:
    """Conventional UUID column. Uses Postgres native UUID type."""
    return Column(name, PG_UUID(as_uuid=True), primary_key=primary_key, **kwargs)


def jsonb_column(name: str, **kwargs: Any) -> Column[dict[str, Any]]:
    """Conventional JSONB column."""
    return Column(name, JSONB, **kwargs)
