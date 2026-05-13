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
    OUTBOX_TABLE_NAME,
    DefaultOutboxRecorder,
    OutboxRecorder,
    include_outbox_table,
)
from qx.db.pagination import (
    build_cursor_page,
    decode_cursor,
    encode_cursor,
)
from qx.db.repository import Repository
from qx.db.tenancy import TenantSchemaManager, open_schema_session
from qx.db.uow import EventDispatcher, UnitOfWork

__version__ = "0.1.0"

__all__ = [
    "OUTBOX_TABLE_NAME",
    # Engine / sessions
    "DatabaseSettings",
    "DefaultOutboxRecorder",
    "EventDispatcher",
    # Outbox
    "OutboxRecorder",
    # Repository
    "Repository",
    "SessionFactory",
    # Schema-per-tenant
    "TenantSchemaManager",
    # UoW
    "UnitOfWork",
    "__version__",
    "build_cursor_page",
    "create_engine",
    "decode_cursor",
    # Pagination
    "encode_cursor",
    "include_outbox_table",
    "jsonb_column",
    # Mapping
    "make_metadata",
    "make_registry",
    "make_session_factory",
    "open_schema_session",
    "open_session",
    "standard_audit_columns",
    "uuid_column",
]
