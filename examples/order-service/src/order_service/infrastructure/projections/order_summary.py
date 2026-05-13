"""OrderSummary projection — builds a denormalized read model from the event log.

The ``qx_order_summaries`` table is the read-side for ``GetOrderQuery`` and
the orders list endpoint.  It is updated incrementally by the
``ProjectionRunner`` as new events are appended to the event log.

Each projection handler is idempotent: ``on_orderplaced`` uses an upsert
so replaying the same event twice is a no-op.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qx.projections import Projection
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from order_service.domain.aggregates.order import OrderCancelled, OrderConfirmed, OrderPlaced

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

__all__ = [
    "ORDER_SUMMARIES_TABLE_NAME",
    "OrderSummaryProjection",
    "include_order_summaries_table",
]

ORDER_SUMMARIES_TABLE_NAME = "qx_order_summaries"


def include_order_summaries_table(meta: MetaData) -> Table:
    return Table(
        ORDER_SUMMARIES_TABLE_NAME,
        meta,
        Column("order_id", String, primary_key=True),
        Column("customer_id", String, nullable=False),
        Column("total_cents", Integer, nullable=False),
        Column("status", String, nullable=False, server_default="pending"),
        Column("item_count", Integer, nullable=False, server_default="0"),
        Column("placed_at", DateTime(timezone=True), nullable=False, server_default=text("NOW()")),
        Column("updated_at", DateTime(timezone=True), nullable=False, server_default=text("NOW()")),
    )


class OrderSummaryProjection(Projection):
    """Maintain the ``qx_order_summaries`` read model.

    Requires ``engine`` and ``table`` at construction time.  Each handler
    opens a short connection so that read-model updates are committed
    independently of the projection runner's checkpoint transaction — this
    keeps the runner's session free of per-event work and simplifies
    error recovery (upserts handle re-processing idempotently).
    """

    name = "order_summary"

    def __init__(self, engine: AsyncEngine, table: Table) -> None:
        self._engine = engine
        self._table = table

    async def on_orderplaced(self, ev: OrderPlaced) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                pg_insert(self._table)
                .values(
                    order_id=str(ev.order_id),
                    customer_id=str(ev.customer_id),
                    total_cents=ev.total_cents,
                    status="pending",
                    item_count=len(ev.items),
                )
                .on_conflict_do_nothing(index_elements=["order_id"]),
            )

    async def on_orderconfirmed(self, ev: OrderConfirmed) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                self._table.update()
                .where(self._table.c.order_id == str(ev.order_id))
                .values(status="confirmed", updated_at=text("NOW()")),
            )

    async def on_ordercancelled(self, ev: OrderCancelled) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                self._table.update()
                .where(self._table.c.order_id == str(ev.order_id))
                .values(status="cancelled", updated_at=text("NOW()")),
            )

    async def reset(self) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(self._table.delete())
