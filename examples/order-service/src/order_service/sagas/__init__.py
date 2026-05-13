"""Order sagas — durable process managers for multi-step workflows."""

from __future__ import annotations

from datetime import timedelta

from qx.saga import Saga, SagaState, on, on_timeout

from order_service.application.commands.cancel_order import CancelOrderCommand
from order_service.application.commands.confirm_order import ConfirmOrderCommand
from order_service.domain.aggregates.order import (
    OrderCancelled,
    OrderConfirmed,
    OrderPlacedIntegration,
)

__all__ = ["OrderFulfillmentSaga", "OrderFulfillmentState"]


class OrderFulfillmentState(SagaState):
    """Mutable state carried by an OrderFulfillmentSaga instance."""

    order_id: str = ""


class OrderFulfillmentSaga(Saga[OrderFulfillmentState]):
    """Coordinate order lifecycle from placement through confirmation.

    Flow::

        OrderPlacedIntegration
            → record order_id
            → dispatch ConfirmOrderCommand (auto-confirm for demo)

        OrderConfirmed
            → mark saga complete

        OrderCancelled
            → mark saga as failed (terminal)

        timeout (30 min)
            → cancel the order

    In a real system the saga would wait for a payment event before
    confirming.  Here, we auto-confirm to keep the demo self-contained.
    """

    state_type = OrderFulfillmentState

    @on(OrderPlacedIntegration)
    async def on_order_placed(self, ev: OrderPlacedIntegration) -> None:
        from uuid import UUID  # noqa: PLC0415

        self.state.order_id = str(ev.order_id)
        await self.dispatch(ConfirmOrderCommand(order_id=UUID(self.state.order_id)))

    @on(OrderConfirmed)
    async def on_order_confirmed(self, ev: OrderConfirmed) -> None:
        self.mark_complete()

    @on(OrderCancelled)
    async def on_order_cancelled(self, ev: OrderCancelled) -> None:
        self.mark_failed()

    @on_timeout(after=timedelta(minutes=30))
    async def handle_timeout(self) -> None:
        from uuid import UUID  # noqa: PLC0415

        if self.state.order_id:
            await self.dispatch(
                CancelOrderCommand(
                    order_id=UUID(self.state.order_id),
                    reason="order timed out awaiting confirmation",
                ),
            )

    async def compensate(self) -> None:
        pass
