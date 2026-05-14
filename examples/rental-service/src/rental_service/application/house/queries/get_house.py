"""GetHouse query + handler."""

from __future__ import annotations

from uuid import UUID  # noqa: TC003

from pydantic import BaseModel
from qx.core import NotFoundError, Result
from qx.cqrs import Query, query_handler
from qx.db import SessionFactory  # noqa: TC002

from rental_service.infrastructure.house import HouseRepository


class HouseDto(BaseModel):
    house_id: UUID
    address: str
    price_per_night_cents: int
    available: bool


class GetHouseQuery(Query[HouseDto]):
    house_id: UUID


@query_handler(GetHouseQuery)
class GetHouseHandler:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._sf = session_factory

    async def handle(self, query: GetHouseQuery) -> Result[HouseDto]:
        async with self._sf() as session:
            repo = HouseRepository(session)
            result = await repo.get(query.house_id)
            if result.is_failure:
                return Result.failure(
                    NotFoundError(
                        code="house.not_found", message=f"house {query.house_id} not found"
                    )
                )
        house = result.value
        return Result.success(
            HouseDto(
                house_id=house.id.value,
                address=house.address,
                price_per_night_cents=house.price_per_night_cents,
                available=house.available,
            )
        )
