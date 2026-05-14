"""ListHouse command + handler."""

from __future__ import annotations

from uuid import UUID  # noqa: TC003

from pydantic import BaseModel
from qx.core import Result
from qx.cqrs import Command, command_handler
from qx.db import UnitOfWork  # noqa: TC002

from rental_service.domain.house import House
from rental_service.infrastructure.house import HouseRepository


class ListHouseDto(BaseModel):
    house_id: UUID


class ListHouseCommand(Command[ListHouseDto]):
    address: str
    price_per_night_cents: int


@command_handler(ListHouseCommand)
class ListHouseHandler:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def handle(self, cmd: ListHouseCommand) -> Result[ListHouseDto]:
        house_result = House.create_listing(cmd.address, cmd.price_per_night_cents)
        if house_result.is_failure:
            return Result.failure(house_result.error)

        house = house_result.value
        async with self._uow:
            repo = HouseRepository(self._uow.session)
            add_result = await repo.add(house)
            if add_result.is_failure:
                return Result.failure(add_result.error)
            self._uow.track(house)
            await self._uow.commit()

        return Result.success(ListHouseDto(house_id=house.id.value))
