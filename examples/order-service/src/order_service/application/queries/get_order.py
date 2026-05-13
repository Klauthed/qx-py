"""GetOrder query — reads from the OrderSummary projection table."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel
from qx.core import NotFoundError, Result
from qx.cqrs import Query, query_handler
from sqlalchemy import select

if TYPE_CHECKING:
    from qx.db import SessionFactory
    from sqlalchemy import Table

__all__ = ["GetOrderDto", "GetOrderHandler", "GetOrderQuery"]


class GetOrderDto(BaseModel):
    order_id: UUID
    customer_id: UUID
    total_cents: int
    status: str
    item_count: int


class GetOrderQuery(Query[GetOrderDto]):
    order_id: UUID


@query_handler(GetOrderQuery)
class GetOrderHandler:
    def __init__(
        self,
        session_factory: SessionFactory,
        order_summaries_table: Table,
    ) -> None:
        self._sf = session_factory
        self._table = order_summaries_table

    async def handle(self, query: GetOrderQuery) -> Result[GetOrderDto]:
        async with self._sf() as session:
            row = (
                (
                    await session.execute(
                        select(self._table).where(
                            self._table.c.order_id == str(query.order_id),
                        ),
                    )
                )
                .mappings()
                .first()
            )

        if row is None:
            return Result.failure(
                NotFoundError(
                    code="order.not_found",
                    message=f"Order {query.order_id} not found.",
                ),
            )

        return Result.success(
            GetOrderDto(
                order_id=UUID(row["order_id"]),
                customer_id=UUID(row["customer_id"]),
                total_cents=row["total_cents"],
                status=row["status"],
                item_count=row["item_count"],
            ),
        )
