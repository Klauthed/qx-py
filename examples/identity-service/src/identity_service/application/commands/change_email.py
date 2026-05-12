"""ChangeEmail command + handler.

Demonstrates load → mutate → save with optimistic concurrency and an
integration event recorded by the aggregate.
"""

from __future__ import annotations

from uuid import UUID  # noqa: TC003

from qx.core import Result
from qx.cqrs import Command, command_handler
from qx.db import UnitOfWork  # noqa: TC002

from identity_service.infrastructure.persistence.user import UserRepository


class ChangeEmailCommand(Command[None]):
    user_id: UUID
    new_email: str


@command_handler(ChangeEmailCommand)
class ChangeEmailHandler:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def handle(self, command: ChangeEmailCommand) -> Result[None]:
        async with self._uow:
            repo = UserRepository(self._uow.session)
            load_result = await repo.get(command.user_id)
            if load_result.is_failure:
                return Result.failure(load_result.error)

            user = load_result.value
            self._uow.track(user)
            change_result = user.change_email(command.new_email)
            if change_result.is_failure:
                return Result.failure(change_result.error)

            save_result = await repo.save(user)
            if save_result.is_failure:
                return Result.failure(save_result.error)

            await self._uow.commit()
        return Result.success(None)
