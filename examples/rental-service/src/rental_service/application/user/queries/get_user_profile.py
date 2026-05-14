"""GetUserProfile query + handler."""

from __future__ import annotations

from uuid import UUID  # noqa: TC003

from pydantic import BaseModel
from qx.core import NotFoundError, Result
from qx.cqrs import Query, query_handler
from qx.db import SessionFactory  # noqa: TC002

from rental_service.infrastructure.user import UserRepository


class UserProfileDto(BaseModel):
    user_id: UUID
    email: str
    name: str


class GetUserProfileQuery(Query[UserProfileDto]):
    user_id: UUID


@query_handler(GetUserProfileQuery)
class GetUserProfileHandler:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sf = session_factory

    async def handle(self, query: GetUserProfileQuery) -> Result[UserProfileDto]:
        async with self._sf() as session:
            repo = UserRepository(session)
            result = await repo.get(query.user_id)
            if result.is_failure:
                return Result.failure(
                    NotFoundError(code="user.not_found", message=f"user {query.user_id} not found")
                )
        user = result.value
        return Result.success(
            UserProfileDto(user_id=user.id.value, email=user.email, name=user.name)
        )
