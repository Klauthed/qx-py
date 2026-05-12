"""User table mapping (imperative)."""

from __future__ import annotations

from sqlalchemy import Boolean, Column, String, Table, UniqueConstraint

from qx.db import make_registry, standard_audit_columns, uuid_column

from identity_service.domain.aggregates.user import User
from identity_service.infrastructure import metadata

registry = make_registry(metadata=metadata)

users_table = Table(
    "users",
    metadata,
    uuid_column("id", primary_key=True),
    Column("email", String(255), nullable=False),
    Column("name", String(255), nullable=False),
    Column("is_active", Boolean, nullable=False, default=True),
    *standard_audit_columns(),
    UniqueConstraint("email", "tenant_id", name="uq_users_email_tenant"),
)

# Imperative mapping: bind User to the users_table. Keeps the domain object
# free of any SQLAlchemy dependency.
registry.map_imperatively(User, users_table)
