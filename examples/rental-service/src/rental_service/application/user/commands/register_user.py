"""RegisterUser command + handler."""

from __future__ import annotations

from uuid import UUID  # noqa: TC003

from pydantic import BaseModel
from qx.core import Result
from qx.cqrs import Command, command_handler
from qx.db import UnitOfWork  # noqa: TC002

from rental_service.domain.user import User
from rental_service.infrastructure.user import UserRepository


class RegisterUserDto(BaseModel):
    user_id: UUID


class RegisterUserCommand(Command[RegisterUserDto]):
    email: str
    name: str


@command_handler(RegisterUserCommand)
class RegisterUserHandler:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def handle(self, cmd: RegisterUserCommand) -> Result[RegisterUserDto]:
        user_result = User.register(cmd.email, cmd.name)
        if user_result.is_failure:
            return Result.failure(user_result.error)

        user = user_result.value
        async with self._uow:
            repo = UserRepository(self._uow.session)
            add_result = await repo.add(user)
            if add_result.is_failure:
                return Result.failure(add_result.error)
            self._uow.track(user)
            await self._uow.commit()

        return Result.success(RegisterUserDto(user_id=user.id.value))
