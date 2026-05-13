"""SQLAlchemy table definition for projection checkpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Column, DateTime, Integer, String, Table, text

if TYPE_CHECKING:
    from sqlalchemy import MetaData

__all__ = ["PROJECTION_CHECKPOINTS_TABLE_NAME", "include_projection_tables"]

PROJECTION_CHECKPOINTS_TABLE_NAME = "qx_projection_checkpoints"


def include_projection_tables(metadata: MetaData) -> Table:
    """Add the projection checkpoints table to ``metadata`` and return it.

    Schema
    ------
    projection_name   string PK  — unique name declared on the Projection subclass
    last_sequence     int        — global sequence of the last event successfully applied
    updated_at        timestamptz
    """
    return Table(
        PROJECTION_CHECKPOINTS_TABLE_NAME,
        metadata,
        Column("projection_name", String(255), primary_key=True),
        Column("last_sequence", Integer, nullable=False, server_default="0"),
        Column(
            "updated_at",
            DateTime(timezone=True),
            nullable=False,
            server_default=text("now()"),
        ),
        extend_existing=True,
    )
