"""SQLAlchemy table definition for saga instance storage."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Column, DateTime, Index, String, Table, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

if TYPE_CHECKING:
    from sqlalchemy import MetaData

__all__ = ["SAGA_TABLE_NAME", "include_saga_table"]

SAGA_TABLE_NAME = "qx_saga_instances"


def include_saga_table(metadata: MetaData) -> Table:
    """Add the saga instances table to ``metadata`` and return it.

    Call once during application startup alongside your aggregate tables::

        metadata = make_metadata()
        include_outbox_table(metadata)
        include_saga_table(metadata)

    Schema
    ------
    id            UUID PK — saga instance identifier (one per correlation key)
    saga_type     string  — fully-qualified saga class name
    correlation_key string — the business key that identifies this instance
                             (e.g. order_id).  Unique per saga_type.
    status        string  — "running" | "completed" | "failed" | "compensated"
    state         JSONB   — serialised SagaState
    version       int     — optimistic-concurrency counter
    created_at    timestamptz
    updated_at    timestamptz — used for timeout detection
    """
    return Table(
        SAGA_TABLE_NAME,
        metadata,
        Column("id", PG_UUID(as_uuid=True), primary_key=True),
        Column("saga_type", String(255), nullable=False),
        Column("correlation_key", String(255), nullable=False),
        Column("status", String(32), nullable=False, server_default="running"),
        Column("state", JSONB, nullable=False, server_default="{}"),
        Column("version", String, nullable=False, server_default="0"),
        Column(
            "created_at",
            DateTime(timezone=True),
            nullable=False,
            server_default=text("now()"),
        ),
        Column(
            "updated_at",
            DateTime(timezone=True),
            nullable=False,
            server_default=text("now()"),
            onupdate=text("now()"),
        ),
        Index(
            f"ix_{SAGA_TABLE_NAME}_type_key",
            "saga_type",
            "correlation_key",
            unique=True,
        ),
        Index(
            f"ix_{SAGA_TABLE_NAME}_status",
            "status",
        ),
        extend_existing=True,
    )
