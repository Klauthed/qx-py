"""SQLAlchemy table definitions for the event store."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Column, DateTime, Index, Integer, String, Table, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

if TYPE_CHECKING:
    from sqlalchemy import MetaData

__all__ = [
    "AGGREGATE_EVENTS_TABLE_NAME",
    "AGGREGATE_SNAPSHOTS_TABLE_NAME",
    "include_eventstore_tables",
]

AGGREGATE_EVENTS_TABLE_NAME = "qx_aggregate_events"
AGGREGATE_SNAPSHOTS_TABLE_NAME = "qx_aggregate_snapshots"


def include_eventstore_tables(metadata: MetaData) -> tuple[Table, Table]:
    """Add event-store tables to ``metadata`` and return ``(events, snapshots)``.

    Call once at startup alongside your aggregate tables::

        metadata = make_metadata()
        include_outbox_table(metadata)
        events_table, snapshots_table = include_eventstore_tables(metadata)

    Schema — events
    ---------------
    id              UUID PK
    aggregate_type  string  — fully-qualified aggregate class name
    aggregate_id    string  — aggregate identifier (stringified)
    sequence        int     — monotonic per-aggregate sequence number (1-based)
    event_type      string  — fully-qualified event class name
    event_name      string  — stable human-readable name (e.g. "account.money_deposited")
    payload         JSONB   — serialised event body
    occurred_at     timestamptz
    tenant_id       string | NULL

    Schema — snapshots
    ------------------
    id              UUID PK
    aggregate_type  string
    aggregate_id    string  — unique per (aggregate_type, aggregate_id)
    state           JSONB   — serialised aggregate state at ``version``
    version         int     — sequence number of the last event baked in
    taken_at        timestamptz
    tenant_id       string | NULL
    """
    events = Table(
        AGGREGATE_EVENTS_TABLE_NAME,
        metadata,
        Column("id", PG_UUID(as_uuid=True), primary_key=True),
        Column("aggregate_type", String(255), nullable=False),
        Column("aggregate_id", String(255), nullable=False),
        Column("sequence", Integer, nullable=False),
        Column("event_type", String(255), nullable=False),
        Column("event_name", String(255), nullable=False),
        Column("payload", JSONB, nullable=False, server_default="{}"),
        Column(
            "occurred_at",
            DateTime(timezone=True),
            nullable=False,
            server_default=text("now()"),
        ),
        Column("tenant_id", String(255), nullable=True),
        Index(
            f"ix_{AGGREGATE_EVENTS_TABLE_NAME}_aggregate",
            "aggregate_type",
            "aggregate_id",
            "sequence",
            unique=True,
        ),
        Index(f"ix_{AGGREGATE_EVENTS_TABLE_NAME}_event_name", "event_name"),
        extend_existing=True,
    )

    snapshots = Table(
        AGGREGATE_SNAPSHOTS_TABLE_NAME,
        metadata,
        Column("id", PG_UUID(as_uuid=True), primary_key=True),
        Column("aggregate_type", String(255), nullable=False),
        Column("aggregate_id", String(255), nullable=False),
        Column("state", JSONB, nullable=False, server_default="{}"),
        Column("version", Integer, nullable=False),
        Column(
            "taken_at",
            DateTime(timezone=True),
            nullable=False,
            server_default=text("now()"),
        ),
        Column("tenant_id", String(255), nullable=True),
        Index(
            f"ix_{AGGREGATE_SNAPSHOTS_TABLE_NAME}_aggregate",
            "aggregate_type",
            "aggregate_id",
            unique=True,
        ),
        extend_existing=True,
    )

    return events, snapshots
