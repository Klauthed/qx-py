"""User repository — domain-specific finders on top of the generic base."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from qx.core import Identifier, NotFoundError, Result
from qx.db import Repository

from identity_service.domain.aggregates.user import User
from identity_service.infrastructure.persistence.user.mapping import users_table


class UserRepository(Repository[User]):
    entity_cls = User
    table = users_table
    filterable_fields = {"email", "name", "is_active"}
    sortable_fields = {"created_at", "email"}

    async def find_by_email(self, email: str) -> Result[User]:
        """Look up a user by email within the current tenant scope."""
        stmt = select(self.table).where(self.table.c.email == email.lower().strip())
        stmt = self._scope_query(stmt, include_deleted=False)
        row = (await self._session.execute(stmt)).mappings().first()
        if row is None:
            return Result.failure(
                NotFoundError(
                    code="user.not_found",
                    message=f"no user with email {email}",
                    details={"email": email},
                )
            )
        return Result.success(self._row_to_entity(row))

    def _row_to_entity(self, row: Any) -> User:
        """Reconstitute a User from a row. The default ``vars()`` projection
        doesn't work here because the row has flat columns while ``User.id`` is
        an Identifier value object — we wrap it explicitly.
        """
        data = dict(row)
        return User(
            id=Identifier(value=data["id"]),
            email=data["email"],
            name=data["name"],
            is_active=data["is_active"],
            version=data.get("version", 1),
        )

    def _entity_to_dict(self, entity: User) -> dict[str, Any]:
        """Flatten the User to column values. Inverse of ``_row_to_entity``."""
        return {
            "id": entity.id.value,
            "email": entity.email,
            "name": entity.name,
            "is_active": entity.is_active,
            "version": entity.version,
        }
