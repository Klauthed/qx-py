"""Health check registry.

Two probe styles, per Kubernetes convention:

- **Liveness**: "is this process still functioning?" — should fail only when
  the process is genuinely broken and a restart is the right response.
  Lightweight; runs frequently. Should NOT depend on downstream systems
  (because their outage shouldn't take *this* service down with it).

- **Readiness**: "is this instance ready to serve traffic?" — should fail
  when this instance is up but can't actually do useful work yet (DB
  connections still warming, leader-election pending, dependency outage).

Check functions are async (most real checks need I/O) and return either a
``HealthStatus.HEALTHY`` value or raise / return ``UNHEALTHY``. Failure
messages get included in the probe response payload for debugging.

The registry is DI-registered as a singleton. Adapters (HTTP endpoint, gRPC
health service) wrap it; this module is transport-agnostic.
"""

from __future__ import annotations

import asyncio
import enum
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

__all__ = [
    "AggregateResult",
    "CheckResult",
    "HealthRegistry",
    "HealthStatus",
]


class HealthStatus(enum.Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"  # partial — readiness can still pass, but signal SREs
    UNHEALTHY = "unhealthy"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: HealthStatus
    message: str | None = None
    duration_ms: float = 0.0
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AggregateResult:
    status: HealthStatus
    checks: tuple[CheckResult, ...]


CheckFn = Callable[[], Awaitable[CheckResult | None]]


class HealthRegistry:
    """Registry of liveness/readiness checks."""

    def __init__(self, *, default_timeout_seconds: float = 2.0) -> None:
        self._liveness: dict[str, CheckFn] = {}
        self._readiness: dict[str, CheckFn] = {}
        self._timeout = default_timeout_seconds

    def add_liveness(self, name: str, check: CheckFn) -> None:
        self._liveness[name] = check

    def add_readiness(self, name: str, check: CheckFn) -> None:
        self._readiness[name] = check

    async def liveness(self) -> AggregateResult:
        return await self._run_all(self._liveness)

    async def readiness(self) -> AggregateResult:
        return await self._run_all(self._readiness)

    async def _run_all(self, checks: dict[str, CheckFn]) -> AggregateResult:
        results = await asyncio.gather(
            *[self._run_one(name, fn) for name, fn in checks.items()],
            return_exceptions=False,
        )
        # Aggregate: HEALTHY only if everything is. Any UNHEALTHY → UNHEALTHY.
        # Otherwise (some DEGRADED) → DEGRADED.
        worst = HealthStatus.HEALTHY
        for r in results:
            if r.status is HealthStatus.UNHEALTHY:
                worst = HealthStatus.UNHEALTHY
                break
            if r.status is HealthStatus.DEGRADED and worst is HealthStatus.HEALTHY:
                worst = HealthStatus.DEGRADED
        return AggregateResult(status=worst, checks=tuple(results))

    async def _run_one(self, name: str, fn: CheckFn) -> CheckResult:
        start = time.perf_counter()
        try:
            result = await asyncio.wait_for(fn(), timeout=self._timeout)
        except TimeoutError:
            return CheckResult(
                name=name,
                status=HealthStatus.UNHEALTHY,
                message=f"timed out after {self._timeout}s",
                duration_ms=(time.perf_counter() - start) * 1000,
            )
        except Exception as exc:
            return CheckResult(
                name=name,
                status=HealthStatus.UNHEALTHY,
                message=f"{type(exc).__name__}: {exc}",
                duration_ms=(time.perf_counter() - start) * 1000,
            )
        if result is None:
            return CheckResult(
                name=name,
                status=HealthStatus.HEALTHY,
                duration_ms=(time.perf_counter() - start) * 1000,
            )
        # Patch in duration if check didn't supply one
        if result.duration_ms == 0:
            return CheckResult(
                name=result.name,
                status=result.status,
                message=result.message,
                duration_ms=(time.perf_counter() - start) * 1000,
                details=result.details,
            )
        return result
