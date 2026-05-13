"""Projection base class."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qx.core.domain.events import DomainEvent

__all__ = ["Projection"]

_HANDLER_PREFIX = "on_"


class Projection:
    """Base class for event-driven read-model projections.

    Subclass and implement ``on_<EventClassName>`` methods. The runner calls
    them in event-stream order::

        class AccountBalanceProjection(Projection):
            name = "account_balance"

            async def on_moneydeposited(self, ev: MoneyDeposited) -> None:
                await self._db.execute(
                    "UPDATE balances SET balance = balance + :amt WHERE id = :id",
                    {"amt": ev.amount, "id": ev.aggregate_id},
                )

            async def on_moneywithdrawn(self, ev: MoneyWithdrawn) -> None:
                await self._db.execute(
                    "UPDATE balances SET balance = balance - :amt WHERE id = :id",
                    {"amt": ev.amount, "id": ev.aggregate_id},
                )

    ``name`` must be unique across projections; the runner uses it to track
    the checkpoint (last processed sequence number) in the checkpoint table.
    """

    name: str

    async def apply(self, event: DomainEvent, *, aggregate_id: str) -> None:
        """Dispatch to the matching ``on_*`` method, if one exists."""
        method_name = f"{_HANDLER_PREFIX}{type(event).__name__.lower()}"
        handler = getattr(self, method_name, None)
        if handler is not None:
            await handler(event)

    async def reset(self) -> None:
        """Called before a full rebuild. Override to truncate the read model."""
