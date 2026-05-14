"""Benchmark: EventStore append and load throughput.

Measures the framework serialization/deserialization overhead — event
packing for append and event reconstruction for load — with a mocked
SQLAlchemy session so no database I/O is involved.

Run with: uv run pytest tests/benchmarks/test_eventstore_throughput.py -v -s -m slow
"""

from __future__ import annotations

import dataclasses
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from qx.core import DomainEvent, Result
from qx.eventstore import EventSourcedAggregate, EventStore, include_eventstore_tables
from sqlalchemy import MetaData

from tests.benchmarks.conftest import measure, report

pytestmark = pytest.mark.slow

N_APPEND = 5_000
N_LOAD = 2_000


# ---------------------------------------------------------------------------
# Minimal domain objects
# ---------------------------------------------------------------------------


class ItemAdded(DomainEvent):
    event_name: ClassVar[str] = "bench.item_added"
    item_id: UUID
    quantity: int


@dataclasses.dataclass
class Cart(EventSourcedAggregate[UUID]):
    item_count: int = 0

    @classmethod
    def create(cls) -> Cart:
        cart = cls(id=uuid4())
        cart.record_event(ItemAdded(item_id=uuid4(), quantity=3))
        return cart

    def apply_itemadded(self, ev: ItemAdded) -> None:
        self.item_count += ev.quantity


# ---------------------------------------------------------------------------
# Shared table fixtures (no DB connection)
# ---------------------------------------------------------------------------

_meta = MetaData()
_events_table, _snapshots_table = include_eventstore_tables(_meta)


def _mock_session_for_append() -> AsyncMock:
    session = AsyncMock()
    # simulate successful INSERT (no exception)
    session.execute = AsyncMock(return_value=MagicMock())
    return session


def _mock_session_for_load(event_rows: list[dict]) -> AsyncMock:
    session = AsyncMock()

    # snapshot query → empty (no snapshot)
    snap_result = MagicMock()
    snap_result.mappings.return_value.first.return_value = None

    # events query → our synthetic rows
    events_result = MagicMock()
    events_result.mappings.return_value = event_rows

    session.execute = AsyncMock(side_effect=[snap_result, events_result])
    return session


def _build_event_rows(n: int) -> list[dict]:
    """Build n synthetic event rows that _deserialize_event can process."""
    aggregate_id = str(uuid4())
    return [
        {
            "id": uuid4(),
            "aggregate_type": "tests.benchmarks.test_eventstore_throughput.Cart",
            "aggregate_id": aggregate_id,
            "sequence": i + 1,
            "event_type": "tests.benchmarks.test_eventstore_throughput.ItemAdded",
            "event_name": "bench.item_added",
            "payload": {"item_id": str(uuid4()), "quantity": 1},
            "occurred_at": None,
            "tenant_id": None,
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------


async def test_bench_eventstore_append_single_event() -> None:
    """Time the EventStore.append() path for a new aggregate with one event."""
    with patch("qx.eventstore.store._HAS_PROMETHEUS", False):

        async def _one_append() -> None:
            session = _mock_session_for_append()
            store = EventStore(session, _events_table, _snapshots_table)
            cart = Cart.create()
            result = await store.append(cart, aggregate_type="bench.Cart")
            assert isinstance(result, Result)

        ops, ms = await measure(N_APPEND, _one_append)
    report("EventStore.append (1 event, no snapshot)", ops, ms, N_APPEND)


async def test_bench_eventstore_load_10_events() -> None:
    """Time the EventStore.load() path replaying 10 events (no snapshot)."""
    rows = _build_event_rows(10)
    with patch("qx.eventstore.store._HAS_PROMETHEUS", False):

        async def _one_load() -> None:
            # Re-build mock each call so side_effect resets.
            session = _mock_session_for_load(rows)
            store = EventStore(session, _events_table, _snapshots_table)
            result = await store.load(
                Cart,
                aggregate_id=rows[0]["aggregate_id"],
                aggregate_type="bench.Cart",
            )
            assert isinstance(result, Result)

        ops, ms = await measure(N_LOAD, _one_load)
    report("EventStore.load (replay 10 events, no snapshot)", ops, ms, N_LOAD)


async def test_bench_eventstore_load_1_event() -> None:
    """Time the EventStore.load() path replaying a single event."""
    rows = _build_event_rows(1)
    with patch("qx.eventstore.store._HAS_PROMETHEUS", False):

        async def _one_load() -> None:
            session = _mock_session_for_load(rows)
            store = EventStore(session, _events_table, _snapshots_table)
            result = await store.load(
                Cart,
                aggregate_id=rows[0]["aggregate_id"],
                aggregate_type="bench.Cart",
            )
            assert isinstance(result, Result)

        ops, ms = await measure(N_LOAD, _one_load)
    report("EventStore.load (replay 1 event, no snapshot)", ops, ms, N_LOAD)
