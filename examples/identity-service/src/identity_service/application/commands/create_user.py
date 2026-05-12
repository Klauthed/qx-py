"""CreateUser command + handler.

End-to-end example: validates input, constructs the User aggregate,
persists via UnitOfWork, lets the UoW route the recorded
``UserRegisteredIntegration`` into the outbox table within the same
transaction.
"""

from __future__ import annotations

from uuid import UUID  # noqa: TC003

from pydantic import BaseModel
from qx.core import ConflictError, Result
from qx.cqrs import Command, command_handler
from qx.db import SessionFactory, UnitOfWork  # noqa: TC002

from identity_service.domain.aggregates.user import User
from identity_service.infrastructure.persistence.user import UserRepository


class CreateUserDto(BaseModel):
    """Wire-level response shape for CreateUser."""

    id: UUID
    email: str
    name: str


class CreateUserCommand(Command[CreateUserDto]):
    email: str
    name: str


@command_handler(CreateUserCommand)
class CreateUserHandler:
    def __init__(self, uow: UnitOfWork, sessions: SessionFactory) -> None:
        self._uow = uow
        self._sessions = sessions

    async def handle(self, command: CreateUserCommand) -> Result[CreateUserDto]:
        async with self._uow:
            repo = UserRepository(self._uow.session)

            # Domain-level uniqueness check inside the transaction. The
            # DB-level UNIQUE constraint is the ultimate guarantor; this
            # check just gives a friendly error path without relying on
            # IntegrityError translation.
            existing = await repo.find_by_email(command.email)
            if existing.is_success:
                return Result.failure(
                    ConflictError(
                        code="user.duplicate_email",
                        message="a user with this email already exists",
                        details={"email": command.email},
                    )
                )

            user_result = User.register(command.email, command.name)
            if user_result.is_failure:
                return user_result.map(
                    lambda _: CreateUserDto(  # unreachable but typed
                        id=user_result.value.id.value,
                        email=user_result.value.email,
                        name=user_result.value.name,
                    )
                )

            user = user_result.value
            self._uow.track(user)
            add_result = await repo.add(user)
            if add_result.is_failure:
                return Result.failure(add_result.error)

            await self._uow.commit()

        return Result.success(CreateUserDto(id=user.id.value, email=user.email, name=user.name))
