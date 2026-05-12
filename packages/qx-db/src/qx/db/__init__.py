"""Qx database layer.

Public surface — import from ``qx.db``.
"""

from __future__ import annotations

from qx.db.engine import (
    DatabaseSettings,
    SessionFactory,
    create_engine,
    make_session_factory,
    open_session,
)
from qx.db.mapping import (
    jsonb_column,
    make_metadata,
    make_registry,
    standard_audit_columns,
    uuid_column,
)
from qx.db.outbox import (
    DefaultOutboxRecorder,
    OUTBOX_TABLE_NAME,
    OutboxRecorder,
    include_outbox_table,
)
from qx.db.pagination import (
    build_cursor_page,
    decode_cursor,
    encode_cursor,
)
from qx.db.repository import Repository
from qx.db.uow import EventDispatcher, UnitOfWork

__version__ = "0.1.0"

__all__ = [
    # Engine / sessions
    "DatabaseSettings",
    "SessionFactory",
    "create_engine",
    "make_session_factory",
    "open_session",
    # Mapping
    "make_metadata",
    "make_registry",
    "standard_audit_columns",
    "uuid_column",
    "jsonb_column",
    # Repository
    "Repository",
    # UoW
    "UnitOfWork",
    "EventDispatcher",
    # Outbox
    "OutboxRecorder",
    "DefaultOutboxRecorder",
    "include_outbox_table",
    "OUTBOX_TABLE_NAME",
    # Pagination
    "encode_cursor",
    "decode_cursor",
    "build_cursor_page",
    "__version__",
]
