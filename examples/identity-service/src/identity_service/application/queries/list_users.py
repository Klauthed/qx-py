"""ListUsers query — demonstrates offset pagination."""

from __future__ import annotations

from pydantic import BaseModel, Field

from qx.core import OffsetPage, OffsetPagination, Result
from qx.cqrs import Query, query_handler
from qx.db import SessionFactory, open_session

from identity_service.application.queries.get_user import UserDto
from identity_service.infrastructure.persistence.user import UserRepository


class ListUsersQuery(Query[OffsetPage[UserDto]]):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


@query_handler(ListUsersQuery)
class ListUsersHandler:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def handle(self, query: ListUsersQuery) -> Result[OffsetPage[UserDto]]:
        async with open_session(self._sessions) as session:
            repo = UserRepository(session)
            page = await repo.list(
                OffsetPagination(page=query.page, page_size=query.page_size)
            )

        dtos = [
            UserDto(
                id=u.id.value,
                email=u.email,
                name=u.name,
                is_active=u.is_active,
            )
            for u in page.items
        ]
        return Result.success(
            OffsetPage(
                items=dtos,
                page=page.page,
                page_size=page.page_size,
                total=page.total,
            )
        )
