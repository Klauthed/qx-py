"""Integration test: OutboxRelay publishes events from Postgres to NATS.

Verifies the full outbox → relay → NATS path:
1. Write events directly to the outbox table (simulating what UoW does).
2. Run OutboxRelay._process_one_batch() against a real Postgres + real NATS.
3. Subscribe to NATS and assert the events arrived and were acked.
4. Assert outbox rows have published_at set.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from qx.db import include_outbox_table
from qx.events import NatsPublisher, NatsSettings, OutboxRelay
from qx.events.nats import create_nats_connection
from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def engine(db_url: str) -> AsyncEngine:
    meta = MetaData()
    include_outbox_table(meta)

    eng = create_async_engine(db_url, echo=False, poolclass=NullPool)

    async def _setup() -> None:
        async with eng.begin() as conn:
            await conn.run_sync(meta.create_all)

    asyncio.run(_setup())
    yield eng  # type: ignore[misc]
    asyncio.run(eng.dispose())


@pytest.fixture(autouse=True)
def _clean_outbox(db_url: str) -> None:  # type: ignore[misc]
    yield  # type: ignore[misc]
    eng = create_async_engine(db_url, echo=False, poolclass=NullPool)

    async def _truncate() -> None:
        async with eng.begin() as conn:
            await conn.execute(text("TRUNCATE TABLE qx_outbox_events"))
        await eng.dispose()

    asyncio.run(_truncate())


async def _write_outbox_row(engine: AsyncEngine, event_name: str, payload: dict) -> str:
    row_id = str(uuid4())
    async with engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO qx_outbox_events
                    (id, event_name, event_version, payload, occurred_at, attempts)
                VALUES
                    (:id, :name, 1, CAST(:payload AS jsonb), :now, 0)
            """),
            {
                "id": row_id,
                "name": event_name,
                "payload": json.dumps({"event_name": event_name, "payload": payload}),
                "now": datetime.now(UTC),
            },
        )
    return row_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_relay_publishes_outbox_events_to_nats(engine: AsyncEngine, nats_url: str) -> None:
    """Outbox relay picks up unpublished rows and delivers them to JetStream."""
    # 1. Set up NATS stream
    nc = await create_nats_connection(NatsSettings(servers=(nats_url,)))
    js = nc.jetstream()
    stream_name = f"test-relay-{uuid4().hex[:8]}"
    subject_prefix = f"relay-{uuid4().hex[:8]}"
    await js.add_stream(name=stream_name, subjects=[f"{subject_prefix}.>"])

    # 2. Subscribe to the subject before publishing
    received: list[str] = []

    async def _subscriber() -> None:
        sub = await js.pull_subscribe(f"{subject_prefix}.>", stream=stream_name)
        try:
            msgs = await sub.fetch(10, timeout=5.0)
            for m in msgs:
                received.append((m.headers or {}).get("qx.event_name", ""))
                await m.ack()
        except TimeoutError:
            pass
        await sub.unsubscribe()

    # 3. Write events to outbox
    event_name = "test.order_placed"
    row_id = await _write_outbox_row(engine, event_name, {"order_id": str(uuid4())})

    # 4. Run relay
    publisher = NatsPublisher(nc, subject_prefix=subject_prefix)
    relay = OutboxRelay(engine, publisher, poll_interval_seconds=0.1)
    published = await relay._process_one_batch()
    assert published == 1

    # 5. Pull messages from NATS and assert
    await _subscriber()
    assert event_name in received

    # 6. Assert outbox row marked as published
    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT published_at FROM qx_outbox_events WHERE id = :id"),
            {"id": row_id},
        )
        row = result.mappings().first()
        assert row is not None
        assert row["published_at"] is not None

    await nc.drain()


@pytest.mark.asyncio
async def test_relay_increments_attempts_on_publish_failure(
    engine: AsyncEngine,
) -> None:
    """When publisher fails, relay increments the attempts counter."""
    event_name = "test.payment_failed"
    row_id = await _write_outbox_row(engine, event_name, {"amount": 42})

    failing_publisher = AsyncMock()
    failing_publisher.publish_raw.side_effect = RuntimeError("NATS unavailable")

    relay = OutboxRelay(engine, failing_publisher)
    published = await relay._process_one_batch()
    assert published == 0

    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT attempts, last_error FROM qx_outbox_events WHERE id = :id"),
            {"id": row_id},
        )
        row = result.mappings().first()
        assert row is not None
        assert row["attempts"] == 1
        assert "NATS unavailable" in (row["last_error"] or "")


@pytest.mark.asyncio
async def test_relay_skips_rows_at_max_attempts(engine: AsyncEngine, nats_url: str) -> None:
    """Rows that hit max_attempts are ignored by the relay."""
    nc = await create_nats_connection(NatsSettings(servers=(nats_url,)))
    js = nc.jetstream()
    subject_prefix = f"relay-{uuid4().hex[:8]}"
    await js.add_stream(name=f"test-max-{uuid4().hex[:8]}", subjects=[f"{subject_prefix}.>"])

    event_name = "test.order_placed"

    # Insert row already at max_attempts (default=10)
    async with engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO qx_outbox_events
                    (id, event_name, event_version, payload, occurred_at, attempts)
                VALUES
                    (:id, :name, 1, CAST(:payload AS jsonb), :now, 10)
            """),
            {
                "id": str(uuid4()),
                "name": event_name,
                "payload": json.dumps({"event_name": event_name, "payload": {}}),
                "now": datetime.now(UTC),
            },
        )

    publisher = NatsPublisher(nc, subject_prefix=subject_prefix)
    relay = OutboxRelay(engine, publisher, max_attempts=10)
    published = await relay._process_one_batch()
    assert published == 0

    await nc.drain()


@pytest.mark.asyncio
async def test_relay_publishes_multiple_events_in_one_batch(
    engine: AsyncEngine, nats_url: str
) -> None:
    """Relay processes multiple pending rows in a single batch."""
    nc = await create_nats_connection(NatsSettings(servers=(nats_url,)))
    js = nc.jetstream()
    subject_prefix = f"relay-multi-{uuid4().hex[:8]}"
    await js.add_stream(name=f"test-multi-{uuid4().hex[:8]}", subjects=[f"{subject_prefix}.>"])

    for _ in range(3):
        await _write_outbox_row(engine, "test.item_created", {"value": str(uuid4())})

    publisher = NatsPublisher(nc, subject_prefix=subject_prefix)
    relay = OutboxRelay(engine, publisher)
    published = await relay._process_one_batch()
    assert published == 3

    await nc.drain()
