"""GetUserProfile query — returns an enhanced user profile, feature-flag gated.

When the ``identity.enhanced-profile`` flag is **on**, the response includes
``is_active`` and ``registered_at``. When off, it returns just the core fields.
This demonstrates ``FlagClient`` usage inside a query handler.
"""

from __future__ import annotations

from uuid import UUID  # noqa: TC003

from pydantic import BaseModel
from qx.core import NotFoundError, Result
from qx.cqrs import Query, query_handler
from qx.db import UnitOfWork  # noqa: TC002
from qx.flags import FlagClient  # noqa: TC002

from identity_service.infrastructure.persistence.user import UserRepository

_ENHANCED_FLAG = "identity.enhanced-profile"


class UserProfileDto(BaseModel):
    id: UUID
    email: str
    name: str
    is_active: bool | None = None


class GetUserProfileQuery(Query[UserProfileDto]):
    user_id: UUID


@query_handler(GetUserProfileQuery)
class GetUserProfileHandler:
    def __init__(self, uow: UnitOfWork, flags: FlagClient) -> None:
        self._uow = uow
        self._flags = flags

    async def handle(self, query: GetUserProfileQuery) -> Result[UserProfileDto]:
        async with self._uow:
            repo = UserRepository(self._uow.session)
            result = await repo.find_by_id(query.user_id)
            if result.is_failure:
                return Result.failure(
                    NotFoundError(
                        code="user.not_found",
                        message=f"user {query.user_id} not found",
                        details={"user_id": str(query.user_id)},
                    )
                )
            user = result.value

        enhanced = await self._flags.bool(_ENHANCED_FLAG, default=False)
        return Result.success(
            UserProfileDto(
                id=user.id.value,
                email=user.email,
                name=user.name,
                is_active=user.is_active if enhanced else None,
            )
        )
