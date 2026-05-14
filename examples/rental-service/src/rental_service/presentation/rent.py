"""Rent HTTP routes."""

from __future__ import annotations

from datetime import date  # noqa: TC003
from uuid import UUID  # noqa: TC003

from fastapi import APIRouter
from pydantic import BaseModel
from qx.cqrs import Mediator
from qx.http import Inject, unwrap

from rental_service.application.rent.commands.create_rental import (
    CreateRentalCommand,
    CreateRentalDto,
)
from rental_service.application.rent.queries.get_rental import GetRentalQuery, RentalDto

router = APIRouter(prefix="/rentals", tags=["rentals"])


class CreateRentalRequest(BaseModel):
    user_id: UUID
    house_id: UUID
    check_in: date
    check_out: date


@router.post("", response_model=CreateRentalDto, status_code=201)
async def create_rental(
    body: CreateRentalRequest,
    mediator: Mediator = Inject(Mediator),  # noqa: B008
) -> CreateRentalDto:
    result = await mediator.send(
        CreateRentalCommand(
            user_id=body.user_id,
            house_id=body.house_id,
            check_in=body.check_in,
            check_out=body.check_out,
        )
    )
    return unwrap(result)  # type: ignore[no-any-return]


@router.get("/{rental_id}", response_model=RentalDto)
async def get_rental(
    rental_id: UUID,
    mediator: Mediator = Inject(Mediator),  # noqa: B008
) -> RentalDto:
    result = await mediator.send(GetRentalQuery(rental_id=rental_id))
    return unwrap(result)  # type: ignore[no-any-return]
