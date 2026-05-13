"""Outbox relay worker.

Background worker that drains the ``qx_outbox_events`` table to the message
broker. Architecture:

1. Periodically (default every 500ms) take a small batch of unpublished rows
   with ``SELECT ... FOR UPDATE SKIP LOCKED`` so concurrent relay instances
   never claim the same row.
2. For each row, publish to NATS JetStream and wait for the stream's ack.
3. On success: mark ``published_at = now()``.
4. On failure: increment ``attempts``, log, leave for retry.

**HA sharding** — run N relay workers in parallel, each owning a hash-based
shard of the outbox table (e.g., one per Kubernetes pod replica)::

    # pod-0
    relay = OutboxRelay(engine, publisher, shard_id=0, shard_count=3)

    # pod-1
    relay = OutboxRelay(engine, publisher, shard_id=1, shard_count=3)

    # pod-2
    relay = OutboxRelay(engine, publisher, shard_id=2, shard_count=3)

Each relay adds ``ABS(HASHTEXT(id::text)::bigint) % shard_count = shard_id``
to the batch query, so shards are disjoint with no coordinator overhead.

**Single-instance mode** — the default (``shard_count=1``) omits the shard
filter entirely and optionally uses a ``DistributedLock`` to guarantee a
single active relay under multi-replica deployments.

The relay is **separate** from the consumer worker (``qx-worker``) by
design — relay is service-internal infrastructure; consumer runs the
business handlers.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from qx.db.outbox import OUTBOX_TABLE_NAME
from qx.observability import get_logger, trace_span
from sqlalchemy import text

if TYPE_CHECKING:
    from qx.cache import DistributedLock
    from qx.events.nats import NatsPublisher
    from sqlalchemy.ext.asyncio import AsyncEngine

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
        shard_id: int = 0,
        shard_count: int = 1,
        table_name: str = OUTBOX_TABLE_NAME,
        batch_size: int = 100,
        poll_interval_seconds: float = 0.5,
        idle_poll_interval_seconds: float = 2.0,
        max_attempts: int = 10,
    ) -> None:
        if shard_count < 1:
            raise ValueError(f"shard_count must be >= 1, got {shard_count}")
        if not (0 <= shard_id < shard_count):
            raise ValueError(
                f"shard_id must be in [0, shard_count), got shard_id={shard_id}, shard_count={shard_count}"
            )
        self._engine = engine
        self._publisher = publisher
        self._lock = leader_lock
        self._shard_id = shard_id
        self._shard_count = shard_count
        self._table = table_name
        self._batch = batch_size
        self._poll = poll_interval_seconds
        self._idle_poll = idle_poll_interval_seconds
        self._max_attempts = max_attempts
        self._stop = asyncio.Event()
        self._log = get_logger(f"qx.outbox-relay[{shard_id}/{shard_count}]")

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
            except Exception as exc:
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

    def _shard_clause(self) -> str:
        if self._shard_count == 1:
            return ""
        # Cast to bigint before ABS to avoid int4 overflow on INT_MIN.
        return "  AND ABS(HASHTEXT(id::text)::bigint) % :shard_count = :shard_id\n"

    async def _process_one_batch(self) -> int:
        async with self._engine.begin() as conn:
            res = await conn.execute(
                text(
                    f"""
                    SELECT id, event_name, event_version, payload,
                           correlation_id, tenant_id, attempts
                    FROM {self._table}
                    WHERE published_at IS NULL
                      AND attempts < :max_attempts
                    {self._shard_clause()}ORDER BY occurred_at ASC
                    LIMIT :limit
                    FOR UPDATE SKIP LOCKED
                    """
                ),
                {
                    "limit": self._batch,
                    "max_attempts": self._max_attempts,
                    "shard_count": self._shard_count,
                    "shard_id": self._shard_id,
                },
            )
            rows = list(res.mappings())
            if not rows:
                return 0

            published_ids: list[str] = []
            failed: list[tuple[str, str]] = []  # (id, error)
            for row in rows:
                envelope = (
                    json.loads(row["payload"])
                    if isinstance(row["payload"], str)
                    else row["payload"]
                )
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
                except Exception as exc:
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
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
