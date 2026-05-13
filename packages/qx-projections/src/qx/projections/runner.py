"""ProjectionRunner — reads the event log and drives projection updates."""

from __future__ import annotations

import importlib
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

if TYPE_CHECKING:
    from qx.projections.projection import Projection
    from sqlalchemy import Table
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["ProjectionRunner"]

logger = logging.getLogger(__name__)


class ProjectionRunner:
    """Reads events from ``qx_aggregate_events`` and drives registered projections.

    Two modes of operation:

    **Incremental** (``run_once``) — processes all events since the last
    checkpoint and updates it. Call periodically from a background task::

        runner = ProjectionRunner(engine, checkpoints_table, events_table)
        runner.register(AccountBalanceProjection())

        while True:
            async with engine.begin() as conn:
                await runner.run_once(conn)
            await asyncio.sleep(5)

    **Rebuild** (``rebuild``) — resets the projection and replays the full
    event log from the beginning. Use for cold-start or schema migrations::

        async with engine.begin() as conn:
            await runner.rebuild(conn, projection_name="account_balance")
    """

    def __init__(
        self,
        checkpoints_table: Table,
        events_table: Table,
        *,
        batch_size: int = 500,
    ) -> None:
        self._checkpoints = checkpoints_table
        self._events = events_table
        self._batch_size = batch_size
        self._projections: dict[str, Projection] = {}

    def register(self, projection: Projection) -> None:
        """Register a projection. ``projection.name`` must be unique."""
        self._projections[projection.name] = projection

    async def run_once(self, session: AsyncSession) -> int:
        """Process a batch of new events for all registered projections.

        Returns the total number of events applied across all projections.
        """
        total = 0
        for projection in self._projections.values():
            total += await self._run_projection(session, projection)
        return total

    async def rebuild(self, session: AsyncSession, *, projection_name: str) -> int:
        """Full rebuild: reset checkpoint, call projection.reset(), replay all events.

        Returns the number of events replayed.
        """
        projection = self._projections.get(projection_name)
        if projection is None:
            raise KeyError(f"No projection registered with name {projection_name!r}")

        await projection.reset()
        await self._set_checkpoint(session, projection_name, 0)
        return await self._run_projection(session, projection, from_sequence=0)

    async def _run_projection(
        self,
        session: AsyncSession,
        projection: Projection,
        *,
        from_sequence: int | None = None,
    ) -> int:
        last_seq = from_sequence
        if last_seq is None:
            last_seq = await self._get_checkpoint(session, projection.name)

        rows = await session.execute(
            select(self._events)
            .where(self._events.c.sequence > last_seq)
            .order_by(self._events.c.sequence)
            .limit(self._batch_size)
        )
        event_rows = list(rows.mappings())
        if not event_rows:
            return 0

        for row in event_rows:
            event = _deserialize_event(dict(row))
            aggregate_id: str = row["aggregate_id"]
            try:
                await projection.apply(event, aggregate_id=aggregate_id)
            except Exception:
                logger.exception(
                    "projection %s failed on sequence %s event %s",
                    projection.name,
                    row["sequence"],
                    row["event_name"],
                )
                raise

        new_checkpoint = int(event_rows[-1]["sequence"])
        await self._set_checkpoint(session, projection.name, new_checkpoint)
        return len(event_rows)

    async def _get_checkpoint(self, session: AsyncSession, projection_name: str) -> int:
        row = await session.execute(
            select(self._checkpoints).where(self._checkpoints.c.projection_name == projection_name)
        )
        r = row.mappings().first()
        return int(r["last_sequence"]) if r is not None else 0

    async def _set_checkpoint(
        self, session: AsyncSession, projection_name: str, sequence: int
    ) -> None:
        stmt = (
            pg_insert(self._checkpoints)
            .values(
                projection_name=projection_name,
                last_sequence=sequence,
                updated_at=datetime.now(UTC),
            )
            .on_conflict_do_update(
                index_elements=["projection_name"],
                set_={"last_sequence": sequence, "updated_at": datetime.now(UTC)},
            )
        )
        await session.execute(stmt)


def _deserialize_event(row: dict[str, Any]) -> Any:
    event_type_path: str = row["event_type"]
    module_path, _, class_name = event_type_path.rpartition(".")
    module = importlib.import_module(module_path)
    event_cls = getattr(module, class_name)
    return event_cls.model_validate(row["payload"])
