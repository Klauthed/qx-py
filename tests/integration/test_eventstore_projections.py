"""Integration test: EventStore → ProjectionRunner.

Verifies the full event-sourcing + projections path:
1. Append domain events via EventStore against a real Postgres.
2. Run ProjectionRunner.run_once() → projection handler receives every event.
3. Run ProjectionRunner.rebuild() → full replay from sequence 0.
4. Verify checkpoints advance correctly.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, ClassVar

import pytest
from qx.core import DomainEvent
from qx.eventstore import EventSourcedAggregate, EventStore
from qx.eventstore.table import include_eventstore_tables
from qx.projections import ProjectionRunner, include_projection_tables
from qx.projections.projection import Projection
from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Domain model
# ---------------------------------------------------------------------------


class ItemAdded(DomainEvent):
    event_name: ClassVar[str] = "test.item_added"
    item_id: str
    quantity: int


class ItemRemoved(DomainEvent):
    event_name: ClassVar[str] = "test.item_removed"
    item_id: str
    quantity: int


@dataclass
class Inventory(EventSourcedAggregate[str]):
    items: dict[str, int] = field(default_factory=dict)

    def add(self, item_id: str, qty: int) -> None:
        self.record_event(ItemAdded(item_id=item_id, quantity=qty))

    def remove(self, item_id: str, qty: int) -> None:
        self.record_event(ItemRemoved(item_id=item_id, quantity=qty))

    def apply_itemadded(self, ev: ItemAdded) -> None:
        self.items[ev.item_id] = self.items.get(ev.item_id, 0) + ev.quantity

    def apply_itemremoved(self, ev: ItemRemoved) -> None:
        current = self.items.get(ev.item_id, 0)
        self.items[ev.item_id] = max(0, current - ev.quantity)


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


class ItemCountProjection(Projection):
    """Keeps a running total per item_id in memory (for testing)."""

    name = "item_count"
    counts: dict[str, int]

    def __init__(self) -> None:
        self.counts = {}

    async def apply(self, event: Any, *, aggregate_id: str) -> None:
        if isinstance(event, ItemAdded):
            self.counts[event.item_id] = self.counts.get(event.item_id, 0) + event.quantity
        elif isinstance(event, ItemRemoved):
            current = self.counts.get(event.item_id, 0)
            self.counts[event.item_id] = max(0, current - event.quantity)

    async def reset(self) -> None:
        self.counts.clear()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tables() -> tuple[Any, Any, Any, Any]:
    """Create SQLAlchemy Table objects and return them."""
    meta = MetaData()
    events_table, snapshots_table = include_eventstore_tables(meta)
    checkpoints_table = include_projection_tables(meta)
    return meta, events_table, snapshots_table, checkpoints_table


@pytest.fixture(scope="module")
def engine(db_url: str, tables: tuple) -> AsyncEngine:
    meta, *_ = tables
    eng = create_async_engine(db_url, echo=False, poolclass=NullPool)

    async def _setup() -> None:
        async with eng.begin() as conn:
            await conn.run_sync(meta.create_all)

    asyncio.run(_setup())
    yield eng  # type: ignore[misc]
    asyncio.run(eng.dispose())


@pytest.fixture()
def async_session(engine: AsyncEngine) -> Any:
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _session() -> AsyncSession:
        async with factory() as session:
            yield session

    return factory


@pytest.fixture(autouse=True)
def _clean_tables(db_url: str) -> None:  # type: ignore[misc]
    yield  # type: ignore[misc]
    eng = create_async_engine(db_url, echo=False, poolclass=NullPool)

    async def _truncate() -> None:
        async with eng.begin() as conn:
            await conn.execute(
                text(
                    "TRUNCATE TABLE qx_aggregate_events, qx_aggregate_snapshots, "
                    "qx_projection_checkpoints RESTART IDENTITY CASCADE"
                )
            )
        await eng.dispose()

    asyncio.run(_truncate())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_eventstore_append_and_projection_run(engine: AsyncEngine, tables: tuple) -> None:
    """Append events → run_once → projection reflects correct state."""
    _meta, events_table, snapshots_table, checkpoints_table = tables
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    # Append events
    async with factory() as session:
        store = EventStore(session, events_table, snapshots_table)
        inv = Inventory(id="inv-1")
        inv.add("apple", 10)
        inv.add("banana", 5)
        inv.remove("apple", 3)
        result = await store.append(inv, aggregate_type="test.Inventory")
        assert result.is_success
        assert result.value == 3
        await session.commit()

    # Run projection
    projection = ItemCountProjection()
    runner = ProjectionRunner(checkpoints_table, events_table)
    runner.register(projection)

    async with factory() as session:
        applied = await runner.run_once(session)
        await session.commit()

    assert applied == 3
    assert projection.counts["apple"] == 7  # 10 - 3
    assert projection.counts["banana"] == 5


@pytest.mark.asyncio
async def test_projection_run_once_advances_checkpoint(engine: AsyncEngine, tables: tuple) -> None:
    """Checkpoint advances to the last applied sequence."""
    _meta, events_table, snapshots_table, checkpoints_table = tables
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        store = EventStore(session, events_table, snapshots_table)
        inv = Inventory(id="inv-2")
        inv.add("cherry", 20)
        await store.append(inv, aggregate_type="test.Inventory")
        await session.commit()

    projection = ItemCountProjection()
    runner = ProjectionRunner(checkpoints_table, events_table)
    runner.register(projection)

    async with factory() as session:
        await runner.run_once(session)
        await session.commit()

    # No new events → run_once returns 0 and doesn't crash
    async with factory() as session:
        applied = await runner.run_once(session)
        await session.commit()

    assert applied == 0
    assert projection.counts.get("cherry") == 20


@pytest.mark.asyncio
async def test_projection_rebuild_replays_all_events(engine: AsyncEngine, tables: tuple) -> None:
    """rebuild() resets and replays from event sequence 0."""
    _meta, events_table, snapshots_table, checkpoints_table = tables
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        store = EventStore(session, events_table, snapshots_table)
        inv = Inventory(id="inv-3")
        inv.add("mango", 15)
        inv.add("mango", 5)
        inv.remove("mango", 4)
        await store.append(inv, aggregate_type="test.Inventory")
        await session.commit()

    projection = ItemCountProjection()
    runner = ProjectionRunner(checkpoints_table, events_table)
    runner.register(projection)

    # Run once first
    async with factory() as session:
        await runner.run_once(session)
        await session.commit()

    assert projection.counts["mango"] == 16

    # Manually corrupt projection state
    projection.counts["mango"] = 0

    # Rebuild → must reset and replay everything
    async with factory() as session:
        applied = await runner.rebuild(session, projection_name="item_count")
        await session.commit()

    assert applied == 3
    assert projection.counts["mango"] == 16


@pytest.mark.asyncio
async def test_eventstore_snapshot_and_reload(engine: AsyncEngine, tables: tuple) -> None:
    """After snapshot threshold, load uses snapshot + tail events only."""
    _meta, events_table, snapshots_table, _checkpoints_table = tables
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    inv_id = "inv-snap"
    async with factory() as session:
        # snapshot_every=2 → snapshot after 2nd append
        store = EventStore(session, events_table, snapshots_table, snapshot_every=2)
        inv = Inventory(id=inv_id)
        inv.add("kiwi", 10)
        inv.add("kiwi", 10)  # triggers snapshot at version 2
        await store.append(inv, aggregate_type="test.Inventory")
        await session.commit()

    async with factory() as session:
        store = EventStore(session, events_table, snapshots_table, snapshot_every=2)
        result = await store.load(Inventory, aggregate_id=inv_id, aggregate_type="test.Inventory")
        assert result.is_success
        loaded = result.value
        assert loaded is not None
        assert loaded.items["kiwi"] == 20
        assert loaded.version == 2
