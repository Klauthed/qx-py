"""CancelOrder command + handler."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from qx.core import NotFoundError, Result
from qx.cqrs import Command, command_handler
from qx.db import SessionFactory
from qx.eventstore import EventStore

from order_service.domain.aggregates.order import ORDER_AGGREGATE_TYPE, Order

if TYPE_CHECKING:
    from sqlalchemy import Table

__all__ = ["CancelOrderCommand", "CancelOrderHandler"]


class CancelOrderCommand(Command[None]):
    order_id: UUID
    reason: str = "cancelled by request"


@command_handler(CancelOrderCommand)
class CancelOrderHandler:
    def __init__(
        self,
        session_factory: SessionFactory,
        events_table: Table,
        snapshots_table: Table,
    ) -> None:
        self._sf = session_factory
        self._events_table = events_table
        self._snapshots_table = snapshots_table

    async def handle(self, cmd: CancelOrderCommand) -> Result[None]:
        async with self._sf() as session, session.begin():
            store = EventStore(session, self._events_table, self._snapshots_table)
            load_result = await store.load(
                Order,
                aggregate_id=str(cmd.order_id),
                aggregate_type=ORDER_AGGREGATE_TYPE,
            )
            if load_result.is_failure:
                return Result.failure(load_result.error)
            if load_result.value is None:
                return Result.failure(
                    NotFoundError(
                        code="order.not_found",
                        message=f"Order {cmd.order_id} not found.",
                    ),
                )

            order: Order = load_result.value  # type: ignore[assignment]
            cancel_result = order.cancel(cmd.reason)
            if cancel_result.is_failure:
                return cancel_result

            await store.append(order, aggregate_type=ORDER_AGGREGATE_TYPE)

        return Result.success(None)
