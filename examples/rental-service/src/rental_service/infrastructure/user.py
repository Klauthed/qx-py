"""User infrastructure — SQLAlchemy table + repository."""

from __future__ import annotations

from typing import ClassVar

from qx.db import Repository
from sqlalchemy import Boolean, Column, String, Table, Uuid

from rental_service.domain.user import User
from rental_service.shared import metadata

users_table: Table = Table(
    "rental_users",
    metadata,
    Column("id", Uuid, primary_key=True),
    Column("email", String(255), nullable=False, unique=True),
    Column("name", String(255), nullable=False),
    Column("is_active", Boolean, nullable=False, server_default="true"),
)


class UserRepository(Repository[User]):
    entity_cls = User
    table = users_table
    filterable_fields: ClassVar[set[str]] = {"email"}
