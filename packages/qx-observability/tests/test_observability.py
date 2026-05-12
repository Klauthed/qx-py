"""Observability tests: metrics, health, logging smoke."""

from __future__ import annotations

from prometheus_client import CollectorRegistry
from qx.core import QxSettings
from qx.observability import (
    HealthRegistry,
    HealthStatus,
    Metrics,
    configure_logging,
    get_logger,
)
from qx.observability.health import CheckResult

# ---- Metrics ----


def test_metrics_define_framework_counters() -> None:
    m = Metrics(registry=CollectorRegistry())
    m.command_total.labels(name="X", outcome="success").inc()
    # No assertion on prometheus_client internals; presence is enough.
    assert m.command_total is not None


def test_metrics_custom_counter() -> None:
    m = Metrics(registry=CollectorRegistry())
    c = m.counter("biz_user_signups_total", "User signups", labelnames=("source",))
    c.labels(source="web").inc()


# ---- Health ----


async def test_healthy_check_returns_healthy() -> None:
    r = HealthRegistry()

    async def check() -> None:
        return None

    r.add_liveness("ok", check)
    result = await r.liveness()
    assert result.status is HealthStatus.HEALTHY
    assert len(result.checks) == 1


async def test_unhealthy_check_aggregates_unhealthy() -> None:
    r = HealthRegistry()

    async def good() -> None:
        return None

    async def bad() -> None:
        raise RuntimeError("db down")

    r.add_readiness("db", bad)
    r.add_readiness("cache", good)
    result = await r.readiness()
    assert result.status is HealthStatus.UNHEALTHY
    failed = next(c for c in result.checks if c.name == "db")
    assert "db down" in (failed.message or "")


async def test_degraded_when_partial_failure() -> None:
    r = HealthRegistry()

    async def good() -> None:
        return None

    async def degraded() -> CheckResult:
        return CheckResult(name="cache", status=HealthStatus.DEGRADED, message="warming up")

    r.add_readiness("db", good)
    r.add_readiness("cache", degraded)
    result = await r.readiness()
    assert result.status is HealthStatus.DEGRADED


async def test_check_timeout() -> None:
    r = HealthRegistry(default_timeout_seconds=0.05)

    async def slow() -> None:
        import asyncio  # noqa: PLC0415

        await asyncio.sleep(0.5)

    r.add_liveness("slow", slow)
    result = await r.liveness()
    assert result.status is HealthStatus.UNHEALTHY
    assert "timed out" in (result.checks[0].message or "")


# ---- Logging ----


def test_configure_logging_does_not_crash() -> None:
    settings = QxSettings()
    configure_logging(settings.logging)
    log = get_logger("test")
    log.info("hello", user="x")  # smoke: should not throw
