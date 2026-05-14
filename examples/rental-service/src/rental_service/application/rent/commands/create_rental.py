"""CreateRental command + handler."""

from __future__ import annotations

from datetime import date  # noqa: TC003
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel
from qx.core import NotFoundError, Result
from qx.cqrs import Command, command_handler
from qx.db import SessionFactory, UnitOfWork  # noqa: TC002

from rental_service.domain.rental import Rental
from rental_service.infrastructure.house import HouseRepository
from rental_service.infrastructure.rental import RentalRepository
from rental_service.infrastructure.user import UserRepository


class CreateRentalDto(BaseModel):
    rental_id: UUID
    total_cents: int


class CreateRentalCommand(Command[CreateRentalDto]):
    user_id: UUID
    house_id: UUID
    check_in: date
    check_out: date


@command_handler(CreateRentalCommand)
class CreateRentalHandler:
    def __init__(self, uow: UnitOfWork, session_factory: SessionFactory) -> None:
        self._uow = uow
        self._sf = session_factory

    async def handle(self, cmd: CreateRentalCommand) -> Result[CreateRentalDto]:
        async with self._sf() as session:
            user_repo = UserRepository(session)
            house_repo = HouseRepository(session)
            user_r = await user_repo.get(cmd.user_id)
            if user_r.is_failure:
                return Result.failure(
                    NotFoundError(code="user.not_found", message=f"user {cmd.user_id} not found")
                )
            house_r = await house_repo.get(cmd.house_id)
            if house_r.is_failure:
                return Result.failure(
                    NotFoundError(code="house.not_found", message=f"house {cmd.house_id} not found")
                )
        house = house_r.value

        rental_result = Rental.create(
            user_id=cmd.user_id,
            house_id=cmd.house_id,
            check_in=cmd.check_in,
            check_out=cmd.check_out,
            price_per_night_cents=house.price_per_night_cents,
        )
        if rental_result.is_failure:
            return Result.failure(rental_result.error)

        rental = rental_result.value
        async with self._uow:
            repo = RentalRepository(self._uow.session)
            add_result = await repo.add(rental)
            if add_result.is_failure:
                return Result.failure(add_result.error)
            self._uow.track(rental)
            await self._uow.commit()

        return Result.success(
            CreateRentalDto(rental_id=rental.id.value, total_cents=rental.total_cents)
        )
