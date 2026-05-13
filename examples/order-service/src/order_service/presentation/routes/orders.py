"""Orders REST routes."""

from __future__ import annotations

from typing import Any
from uuid import UUID  # noqa: TC003

from fastapi import APIRouter
from pydantic import BaseModel
from qx.cqrs import Mediator
from qx.http import Inject, unwrap

from order_service.application.commands.cancel_order import CancelOrderCommand
from order_service.application.commands.confirm_order import ConfirmOrderCommand
from order_service.application.commands.place_order import OrderItemInput, PlaceOrderCommand
from order_service.application.queries.get_order import GetOrderDto, GetOrderQuery

__all__ = ["router"]

router = APIRouter(prefix="/orders", tags=["orders"])


class PlaceOrderRequest(BaseModel):
    customer_id: UUID
    items: list[OrderItemInput]
    total_cents: int


class CancelOrderRequest(BaseModel):
    reason: str = "cancelled by request"


@router.post("", response_model=dict, status_code=201)
async def place_order(
    body: PlaceOrderRequest,
    mediator: Mediator = Inject(Mediator),  # noqa: B008
) -> dict[str, Any]:
    result = await mediator.send(
        PlaceOrderCommand(
            customer_id=body.customer_id,
            items=body.items,
            total_cents=body.total_cents,
        ),
    )
    dto = unwrap(result)
    return {"order_id": str(dto.order_id)}


@router.get("/{order_id}", response_model=GetOrderDto)
async def get_order(
    order_id: UUID,
    mediator: Mediator = Inject(Mediator),  # noqa: B008
) -> GetOrderDto:
    result = await mediator.send(GetOrderQuery(order_id=order_id))
    return unwrap(result)  # type: ignore[no-any-return]


@router.post("/{order_id}/confirm", status_code=204)
async def confirm_order(
    order_id: UUID,
    mediator: Mediator = Inject(Mediator),  # noqa: B008
) -> None:
    result = await mediator.send(ConfirmOrderCommand(order_id=order_id))
    unwrap(result)


@router.post("/{order_id}/cancel", status_code=204)
async def cancel_order(
    order_id: UUID,
    body: CancelOrderRequest,
    mediator: Mediator = Inject(Mediator),  # noqa: B008
) -> None:
    result = await mediator.send(CancelOrderCommand(order_id=order_id, reason=body.reason))
    unwrap(result)
