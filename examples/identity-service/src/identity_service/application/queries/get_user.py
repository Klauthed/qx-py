"""GetUser query."""

from __future__ import annotations

from uuid import UUID  # noqa: TC003

from pydantic import BaseModel
from qx.core import Result
from qx.cqrs import Query, query_handler
from qx.db import SessionFactory, open_session

from identity_service.infrastructure.persistence.user import UserRepository


class UserDto(BaseModel):
    id: UUID
    email: str
    name: str
    is_active: bool


class GetUserQuery(Query[UserDto]):
    user_id: UUID


@query_handler(GetUserQuery)
class GetUserHandler:
    """Read-only query — opens its own session (no UoW needed)."""

    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def handle(self, query: GetUserQuery) -> Result[UserDto]:
        async with open_session(self._sessions) as session:
            repo = UserRepository(session)
            result = await repo.get(query.user_id)
            if result.is_failure:
                return Result.failure(result.error)
            u = result.value
        return Result.success(
            UserDto(
                id=u.id.value,
                email=u.email,
                name=u.name,
                is_active=u.is_active,
            )
        )
