"""RegionReplicator — forward published outbox events to a remote region's broker."""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from qx.db.outbox import OUTBOX_TABLE_NAME
from qx.observability import get_logger, trace_span
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

if TYPE_CHECKING:
    from sqlalchemy import Table
    from sqlalchemy.ext.asyncio import AsyncEngine

__all__ = ["RegionReplicator"]


class RegionReplicator:
    """Forwards published outbox events to a remote region's message broker.

    Reads events from the local ``qx_outbox_events`` table (already published
    locally) and re-publishes them to a ``remote_publisher``. Progress is tracked
    in ``qx_region_checkpoints`` keyed by ``target_region`` so the replicator can
    resume after restart without re-sending events.

    Intended as an application-level fallback when NATS JetStream cross-cluster
    mirroring is not available. When JetStream mirroring is configured, you do
    not need this worker.

    Lifecycle::

        replicator = RegionReplicator(
            engine, remote_publisher,
            target_region="eu-west-1",
            events_table=events_t,
            checkpoints_table=checkpoints_t,
        )
        # in background task:
        await replicator.run()
        # from signal handler:
        await replicator.stop()
    """

    def __init__(
        self,
        engine: AsyncEngine,
        remote_publisher: Any,
        *,
        target_region: str,
        events_table: Table,
        checkpoints_table: Table,
        events_table_name: str = OUTBOX_TABLE_NAME,
        batch_size: int = 100,
        poll_interval_seconds: float = 1.0,
        idle_poll_interval_seconds: float = 5.0,
    ) -> None:
        self._engine = engine
        self._publisher = remote_publisher
        self._target_region = target_region
        self._events_table = events_table
        self._checkpoints_table = checkpoints_table
        self._events_table_name = events_table_name
        self._batch = batch_size
        self._poll = poll_interval_seconds
        self._idle_poll = idle_poll_interval_seconds
        self._stop = asyncio.Event()
        self._log = get_logger(f"qx.region-replicator[{target_region}]")

    async def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """Main loop. Polls for new events and forwards them to the remote region."""
        self._log.info("region-replicator starting", target=self._target_region)
        while not self._stop.is_set():
            try:
                with trace_span("region.replicate.batch"):
                    forwarded = await self._process_one_batch()
                interval = self._poll if forwarded > 0 else self._idle_poll
                await self._sleep(interval)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._log.error("region-replicator error: %s", exc, exc_info=True)
                await self._sleep(self._idle_poll)
        self._log.info("region-replicator stopped", target=self._target_region)

    async def _process_one_batch(self) -> int:
        async with self._engine.begin() as conn:
            checkpoint = await self._load_checkpoint(conn)
            last_occurred_at, last_event_id = checkpoint

            # Fetch the next batch of published events after the checkpoint,
            # ordered by (occurred_at, id) for a stable cursor.
            where_clause = "published_at IS NOT NULL"
            params: dict[str, Any] = {"limit": self._batch}

            if last_occurred_at is not None and last_event_id is not None:
                where_clause += (
                    " AND (occurred_at > :last_occurred_at"
                    "      OR (occurred_at = :last_occurred_at AND id::text > :last_event_id))"
                )
                params["last_occurred_at"] = last_occurred_at
                params["last_event_id"] = str(last_event_id)

            res = await conn.execute(
                text(
                    f"""
                    SELECT id, event_name, payload, correlation_id, tenant_id, occurred_at
                    FROM {self._events_table_name}
                    WHERE {where_clause}
                    ORDER BY occurred_at ASC, id ASC
                    LIMIT :limit
                    """
                ),
                params,
            )
            rows = list(res.mappings())
            if not rows:
                return 0

            forwarded_count = 0
            new_occurred_at = last_occurred_at
            new_event_id = last_event_id

            for row in rows:
                envelope = (
                    json.loads(row["payload"])
                    if isinstance(row["payload"], str)
                    else row["payload"]
                )
                headers = {
                    "qx.event_name": row["event_name"],
                    "qx.source_region": self._target_region,
                }
                if row["correlation_id"]:
                    headers["qx.correlation_id"] = str(row["correlation_id"])
                if row["tenant_id"]:
                    headers["qx.tenant_id"] = str(row["tenant_id"])

                try:
                    await self._publisher.publish_raw(
                        row["event_name"],
                        envelope,
                        headers=headers,
                    )
                    forwarded_count += 1
                    new_occurred_at = row["occurred_at"]
                    new_event_id = row["id"]
                except Exception as exc:
                    self._log.warning("failed to replicate event %s: %s", row["id"], exc)
                    # Stop batch on first failure so we don't skip events.
                    break

            if forwarded_count > 0:
                await self._save_checkpoint(conn, new_occurred_at, new_event_id)

            return forwarded_count

    async def _load_checkpoint(self, conn: Any) -> tuple[datetime | None, str | None]:
        row = (
            (
                await conn.execute(
                    select(
                        self._checkpoints_table.c.last_occurred_at,
                        self._checkpoints_table.c.last_event_id,
                    ).where(self._checkpoints_table.c.target_region == self._target_region)
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None, None
        return row["last_occurred_at"], row["last_event_id"]

    async def _save_checkpoint(self, conn: Any, occurred_at: Any, event_id: Any) -> None:
        stmt = (
            pg_insert(self._checkpoints_table)
            .values(
                target_region=self._target_region,
                last_occurred_at=occurred_at,
                last_event_id=str(event_id) if event_id else None,
                updated_at=datetime.now(UTC),
            )
            .on_conflict_do_update(
                index_elements=["target_region"],
                set_={
                    "last_occurred_at": occurred_at,
                    "last_event_id": str(event_id) if event_id else None,
                    "updated_at": datetime.now(UTC),
                },
            )
        )
        await conn.execute(stmt)

    async def _sleep(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
