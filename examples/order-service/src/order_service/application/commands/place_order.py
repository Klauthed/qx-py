"""PlaceOrder command + handler."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel
from qx.core import Result
from qx.cqrs import Command, command_handler
from qx.db import DefaultOutboxRecorder, SessionFactory
from qx.eventstore import EventStore

from order_service.domain.aggregates.order import (
    ORDER_AGGREGATE_TYPE,
    Order,
    OrderItem,
    OrderPlacedIntegration,
)

if TYPE_CHECKING:
    from sqlalchemy import Table

__all__ = ["PlaceOrderCommand", "PlaceOrderDto", "PlaceOrderHandler"]


class OrderItemInput(BaseModel):
    sku: str
    quantity: int
    unit_price_cents: int


class PlaceOrderDto(BaseModel):
    order_id: UUID


class PlaceOrderCommand(Command[PlaceOrderDto]):
    customer_id: UUID
    items: list[OrderItemInput]
    total_cents: int


@command_handler(PlaceOrderCommand)
class PlaceOrderHandler:
    def __init__(
        self,
        session_factory: SessionFactory,
        events_table: Table,
        snapshots_table: Table,
        outbox: DefaultOutboxRecorder,
    ) -> None:
        self._sf = session_factory
        self._events_table = events_table
        self._snapshots_table = snapshots_table
        self._outbox = outbox

    async def handle(self, cmd: PlaceOrderCommand) -> Result[PlaceOrderDto]:
        items = [
            OrderItem(
                sku=it.sku,
                quantity=it.quantity,
                unit_price_cents=it.unit_price_cents,
            )
            for it in cmd.items
        ]

        order_result = Order.place(
            customer_id=cmd.customer_id,
            items=items,
            total_cents=cmd.total_cents,
        )
        if order_result.is_failure:
            return order_result.map(lambda _: PlaceOrderDto(order_id=order_result.value.id))  # type: ignore[union-attr]

        order = order_result.value

        async with self._sf() as session, session.begin():
            store = EventStore(session, self._events_table, self._snapshots_table)
            append_result = await store.append(order, aggregate_type=ORDER_AGGREGATE_TYPE)
            if append_result.is_failure:
                return Result.failure(append_result.error)

            await self._outbox.record(
                session,
                [
                    OrderPlacedIntegration(
                        order_id=order.id,
                        customer_id=order.customer_id,
                        total_cents=order.total_cents,
                    ),
                ],
            )

        return Result.success(PlaceOrderDto(order_id=order.id))
