"""Benchmark: OutboxRelay batch-processing throughput.

Measures how many events/second the relay can process end-to-end through
one ``_process_one_batch()`` call — DB fetch simulation, NATS publish
simulation, and DB mark-published simulation — with all I/O mocked so the
result reflects framework overhead only.

Run with: uv run pytest tests/benchmarks/test_outbox_relay_throughput.py -v -s -m slow
"""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from qx.events import OutboxRelay

pytestmark = pytest.mark.slow

BATCH_SIZES = [10, 50, 100]


# ---------------------------------------------------------------------------
# Mock factories
# ---------------------------------------------------------------------------


def _make_outbox_rows(n: int) -> list[dict[str, Any]]:
    return [
        {
            "id": str(uuid4()),
            "event_name": "bench.item_added",
            "event_version": 1,
            "payload": json.dumps({"item_id": str(uuid4()), "quantity": 1}),
            "correlation_id": str(uuid4()),
            "tenant_id": None,
            "attempts": 0,
        }
        for _ in range(n)
    ]


def _make_mock_engine(rows: list[dict[str, Any]]) -> MagicMock:
    mock_conn = AsyncMock()

    # First execute = SELECT batch (returns rows)
    mock_result = MagicMock()
    mock_result.mappings.return_value.fetchall.return_value = rows

    # Subsequent executes = UPDATE (returns nothing interesting)
    mock_conn.execute = AsyncMock(side_effect=[mock_result] + [MagicMock()] * (len(rows) + 1))

    mock_engine = MagicMock()
    mock_engine.begin = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    return mock_engine


def _make_mock_publisher() -> AsyncMock:
    publisher = AsyncMock()
    publisher.publish = AsyncMock(return_value=None)
    return publisher


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------


async def _run_relay_batch(batch_size: int, n_iterations: int) -> tuple[float, float, float]:
    """Return (events_per_sec, batches_per_sec, total_ms)."""
    publisher = _make_mock_publisher()
    rows = _make_outbox_rows(batch_size)

    relay = OutboxRelay(
        _make_mock_engine(rows),
        publisher,
        batch_size=batch_size,
    )

    t0 = time.perf_counter()
    for _ in range(n_iterations):
        relay._engine = _make_mock_engine(rows)
        await relay._process_one_batch()
    elapsed = time.perf_counter() - t0

    batches_per_sec = n_iterations / elapsed
    events_per_sec = (n_iterations * batch_size) / elapsed
    return events_per_sec, batches_per_sec, elapsed * 1000


@pytest.mark.parametrize("batch_size", BATCH_SIZES)
async def test_bench_outbox_relay_batch(batch_size: int) -> None:
    n = max(200, 5_000 // batch_size)
    events_ps, batches_ps, ms = await _run_relay_batch(batch_size, n)
    print(
        f"\n  OutboxRelay._process_one_batch (batch={batch_size:>3})  "
        f"{events_ps:>10,.0f} events/s  "
        f"{batches_ps:>8,.0f} batches/s  "
        f"({ms:.1f} ms / {n} iterations)"
    )
