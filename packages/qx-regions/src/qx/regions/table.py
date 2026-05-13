"""SQLAlchemy table definitions for multi-region support."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Column, DateTime, Index, String, Table, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

if TYPE_CHECKING:
    from sqlalchemy import MetaData

__all__ = ["include_region_tables"]

TENANT_REGIONS_TABLE = "qx_tenant_regions"
REPLICATION_CHECKPOINTS_TABLE = "qx_region_checkpoints"


def include_region_tables(metadata: MetaData) -> tuple[Table, Table]:
    """Register region tables in the given metadata.

    Returns ``(tenant_regions_table, replication_checkpoints_table)``.

    ``qx_tenant_regions`` stores the home region for each tenant.
    ``qx_region_checkpoints`` tracks replication progress per target region.
    """
    tenant_regions = Table(
        TENANT_REGIONS_TABLE,
        metadata,
        Column("tenant_id", PG_UUID(as_uuid=True), primary_key=True),
        Column("home_region", String(64), nullable=False),
        Column(
            "created_at",
            DateTime(timezone=True),
            nullable=False,
            server_default=text("now()"),
        ),
        Index(f"ix_{TENANT_REGIONS_TABLE}_home_region", "home_region"),
    )

    replication_checkpoints = Table(
        REPLICATION_CHECKPOINTS_TABLE,
        metadata,
        Column("target_region", String(64), primary_key=True),
        Column("last_occurred_at", DateTime(timezone=True), nullable=True),
        Column("last_event_id", PG_UUID(as_uuid=True), nullable=True),
        Column(
            "updated_at",
            DateTime(timezone=True),
            nullable=False,
            server_default=text("now()"),
        ),
    )

    return tenant_regions, replication_checkpoints
