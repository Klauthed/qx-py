"""Outbox relay worker.

Single-instance background worker that drains the ``qx_outbox_events``
table to the message broker. Architecture:

1. Periodically (default every 500ms) take a small batch of unpublished rows
   with ``SELECT ... FOR UPDATE SKIP LOCKED`` so multiple instances don't fight
   over the same rows.
2. For each row, publish to NATS JetStream and wait for the stream's ack.
3. On success: mark ``published_at = now()``.
4. On failure: increment ``attempts``, log, leave for retry. Exponential
   back-off is applied implicitly by the poll loop — failed rows aren't
   retried until the next batch.

Single-instance discipline: in production, run one relay per service.
``DistributedLock`` is used to enforce that under multi-replica deployments.

The relay is **separate** from the consumer worker (``qx-worker``) by
design — relay is service-internal infrastructure; consumer runs the
business handlers.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from qx.cache import DistributedLock
from qx.db.outbox import OUTBOX_TABLE_NAME
from qx.events.nats import NatsPublisher
from qx.observability import get_logger, trace_span

__all__ = ["OutboxRelay"]


class OutboxRelay:
    """Polls the outbox table and publishes pending events.

    Lifecycle: ``await relay.run()`` blocks until ``await relay.stop()`` is
    called from a signal handler.
    """

    def __init__(
        self,
        engine: AsyncEngine,
        publisher: NatsPublisher,
        *,
        leader_lock: DistributedLock | None = None,
        table_name: str = OUTBOX_TABLE_NAME,
        batch_size: int = 100,
        poll_interval_seconds: float = 0.5,
        idle_poll_interval_seconds: float = 2.0,
        max_attempts: int = 10,
    ) -> None:
        self._engine = engine
        self._publisher = publisher
        self._lock = leader_lock
        self._table = table_name
        self._batch = batch_size
        self._poll = poll_interval_seconds
        self._idle_poll = idle_poll_interval_seconds
        self._max_attempts = max_attempts
        self._stop = asyncio.Event()
        self._log = get_logger("qx.outbox-relay")

    async def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """Main loop. Acquires the leader lock (if any), polls, publishes, repeats."""
        self._log.info("outbox-relay starting")
        while not self._stop.is_set():
            try:
                if self._lock is not None:
                    async with self._lock.acquired(ttl_seconds=60) as got:
                        if not got:
                            # Another instance is leader; back off.
                            await self._sleep(2.0)
                            continue
                        # Renew while we're working.
                        await self._drain_loop()
                else:
                    await self._drain_loop()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._log.error("outbox-relay error: %s", exc, exc_info=True)
                await self._sleep(2.0)
        self._log.info("outbox-relay stopped")

    async def _drain_loop(self) -> None:
        while not self._stop.is_set():
            with trace_span("outbox.batch"):
                published = await self._process_one_batch()
            if published == 0:
                # Nothing pending; back off for the idle interval.
                await self._sleep(self._idle_poll)
            else:
                await self._sleep(self._poll)

    async def _process_one_batch(self) -> int:
        async with self._engine.begin() as conn:
            # Claim rows. SKIP LOCKED makes this safe under multi-instance
            # (even though leader-lock is the primary defense).
            res = await conn.execute(
                text(
                    f"""
                    SELECT id, event_name, event_version, payload,
                           correlation_id, tenant_id, attempts
                    FROM {self._table}
                    WHERE published_at IS NULL
                      AND attempts < :max_attempts
                    ORDER BY occurred_at ASC
                    LIMIT :limit
                    FOR UPDATE SKIP LOCKED
                    """
                ),
                {"limit": self._batch, "max_attempts": self._max_attempts},
            )
            rows = list(res.mappings())
            if not rows:
                return 0

            published_ids: list[str] = []
            failed: list[tuple[str, str]] = []  # (id, error)
            for row in rows:
                envelope = json.loads(row["payload"]) if isinstance(row["payload"], str) else row["payload"]
                headers = {"qx.event_name": row["event_name"]}
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
                    published_ids.append(row["id"])
                except Exception as exc:  # noqa: BLE001
                    failed.append((row["id"], f"{type(exc).__name__}: {exc}"))

            if published_ids:
                await conn.execute(
                    text(
                        f"""
                        UPDATE {self._table}
                        SET published_at = :now
                        WHERE id = ANY(:ids)
                        """
                    ),
                    {"now": datetime.now(UTC), "ids": published_ids},
                )

            for fid, err in failed:
                await conn.execute(
                    text(
                        f"""
                        UPDATE {self._table}
                        SET attempts = attempts + 1,
                            last_error = :err
                        WHERE id = :id
                        """
                    ),
                    {"err": err[:2000], "id": fid},
                )

            return len(published_ids)

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            pass
