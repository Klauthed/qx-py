"""ConfirmOrder command + handler."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID  # noqa: TC003

from qx.core import NotFoundError, Result
from qx.cqrs import Command, command_handler
from qx.eventstore import EventStore

from order_service.domain.aggregates.order import ORDER_AGGREGATE_TYPE, Order

if TYPE_CHECKING:
    from qx.db import SessionFactory
    from sqlalchemy import Table

__all__ = ["ConfirmOrderCommand", "ConfirmOrderHandler"]


class ConfirmOrderCommand(Command[None]):
    order_id: UUID


@command_handler(ConfirmOrderCommand)
class ConfirmOrderHandler:
    def __init__(
        self,
        session_factory: SessionFactory,
        events_table: Table,
        snapshots_table: Table,
    ) -> None:
        self._sf = session_factory
        self._events_table = events_table
        self._snapshots_table = snapshots_table

    async def handle(self, cmd: ConfirmOrderCommand) -> Result[None]:
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
            confirm_result = order.confirm()
            if confirm_result.is_failure:
                return confirm_result

            await store.append(order, aggregate_type=ORDER_AGGREGATE_TYPE)

        return Result.success(None)
