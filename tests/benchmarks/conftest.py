"""Shared fixtures and helpers for the benchmark suite."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


async def measure[T](n: int, fn: Callable[[], Awaitable[T]]) -> tuple[float, float]:
    """Run *fn* n times and return (ops_per_sec, total_ms)."""
    t0 = time.perf_counter()
    for _ in range(n):
        await fn()
    elapsed = time.perf_counter() - t0
    return n / elapsed, elapsed * 1000


def report(label: str, ops: float, total_ms: float, n: int) -> None:
    print(
        f"\n  {label:<50}  {ops:>10,.0f} ops/s  "
        f"({total_ms:.1f} ms / {n:,} iterations)"
    )
