"""House HTTP routes."""

from __future__ import annotations

from uuid import UUID  # noqa: TC003

from fastapi import APIRouter
from pydantic import BaseModel
from qx.cqrs import Mediator
from qx.http import Inject, unwrap

from rental_service.application.house.commands.list_house import ListHouseCommand, ListHouseDto
from rental_service.application.house.queries.get_house import GetHouseQuery, HouseDto

router = APIRouter(prefix="/houses", tags=["houses"])


class ListHouseRequest(BaseModel):
    address: str
    price_per_night_cents: int


@router.post("", response_model=ListHouseDto, status_code=201)
async def list_house(
    body: ListHouseRequest,
    mediator: Mediator = Inject(Mediator),  # noqa: B008
) -> ListHouseDto:
    result = await mediator.send(
        ListHouseCommand(address=body.address, price_per_night_cents=body.price_per_night_cents)
    )
    return unwrap(result)  # type: ignore[no-any-return]


@router.get("/{house_id}", response_model=HouseDto)
async def get_house(
    house_id: UUID,
    mediator: Mediator = Inject(Mediator),  # noqa: B008
) -> HouseDto:
    result = await mediator.send(GetHouseQuery(house_id=house_id))
    return unwrap(result)  # type: ignore[no-any-return]
