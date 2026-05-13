"""Qx saga orchestrator.

Provides durable process managers for multi-step workflows that span multiple
aggregates or services. Sagas react to integration events, dispatch commands,
track state in Postgres, and support compensation (rollback) on failure.

Usage::

    from qx.saga import Saga, SagaManager, SagaState, on, on_timeout

    class OrderFulfillmentState(SagaState):
        order_id: str = ""
        payment_id: str = ""
        reservation_id: str = ""

    class OrderFulfillmentSaga(Saga[OrderFulfillmentState]):
        state_type = OrderFulfillmentState

        @on(OrderPlaced)
        async def start(self, ev: OrderPlaced) -> None:
            self.state.order_id = ev.order_id
            await self.dispatch(ReserveInventoryCommand(order_id=ev.order_id))

        @on(InventoryReserved)
        async def inventory_done(self, ev: InventoryReserved) -> None:
            self.state.reservation_id = ev.reservation_id
            await self.dispatch(ChargePaymentCommand(order_id=self.state.order_id))

        @on(PaymentCharged)
        async def payment_done(self, ev: PaymentCharged) -> None:
            self.state.payment_id = ev.payment_id
            await self.dispatch(ShipOrderCommand(order_id=self.state.order_id))

        @on(OrderShipped)
        async def finish(self, ev: OrderShipped) -> None:
            self.mark_complete()

        @on_timeout(after=timedelta(hours=1))
        async def handle_timeout(self) -> None:
            await self.compensate()

        async def compensate(self) -> None:
            if self.state.reservation_id:
                await self.dispatch(ReleaseInventoryCommand(...))
            if self.state.payment_id:
                await self.dispatch(RefundPaymentCommand(...))

Persistence: one row per saga instance in ``qx_saga_instances``.
The SagaManager is wired as an integration-event handler in the worker.
"""

from __future__ import annotations

from qx.saga.decorators import on, on_timeout
from qx.saga.manager import SagaManager
from qx.saga.saga import Saga
from qx.saga.state import SagaState
from qx.saga.store import SagaStore
from qx.saga.table import SAGA_TABLE_NAME, include_saga_table

__version__ = "0.1.0"

__all__ = [
    "SAGA_TABLE_NAME",
    "Saga",
    "SagaManager",
    "SagaState",
    "SagaStore",
    "__version__",
    "include_saga_table",
    "on",
    "on_timeout",
]
