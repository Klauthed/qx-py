"""User persistence: table mapping + repository."""

from __future__ import annotations

from identity_service.infrastructure.persistence.user.mapping import users_table
from identity_service.infrastructure.persistence.user.repository import UserRepository

__all__ = ["UserRepository", "users_table"]
