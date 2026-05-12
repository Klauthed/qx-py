"""Tests for TracingBehavior and MetricsBehavior."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from prometheus_client import CollectorRegistry

from qx.core import InfrastructureError, NotFoundError, Result
from qx.cqrs import Command, Query
from qx.observability.behaviors import MetricsBehavior, TracingBehavior
from qx.observability.metrics import Metrics


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Cmd(Command[str]):
    pass


class _Qry(Query[str]):
    pass


class _Other:
    pass


async def _ok_next(msg: Any) -> Result[str]:
    return Result.success("done")


async def _fail_next(msg: Any) -> Result[str]:
    return Result.failure(NotFoundError(code="x.not_found", message="not found"))


def _test_metrics() -> Metrics:
    return Metrics(registry=CollectorRegistry())


# ---------------------------------------------------------------------------
# MetricsBehavior
# ---------------------------------------------------------------------------


class TestMetricsBehavior:
    def _counter_value(self, counter: Any, name: str, outcome: str) -> float:
        for mf in counter.collect():
            for s in mf.samples:
                if s.labels.get("name") == name and s.labels.get("outcome") == outcome:
                    return s.value
        return 0.0

    @pytest.mark.asyncio
    async def test_increments_command_counter_on_success(self):
        metrics = _test_metrics()
        behavior = MetricsBehavior(metrics)
        result = await behavior.handle(_Cmd(), _ok_next)
        assert result.is_success
        assert self._counter_value(metrics.command_total, "_Cmd", "success") == 1.0

    @pytest.mark.asyncio
    async def test_increments_command_counter_on_failure(self):
        metrics = _test_metrics()
        behavior = MetricsBehavior(metrics)
        result = await behavior.handle(_Cmd(), _fail_next)
        assert result.is_failure
        assert self._counter_value(metrics.command_total, "_Cmd", "failure") == 1.0

    @pytest.mark.asyncio
    async def test_increments_query_counter(self):
        metrics = _test_metrics()
        behavior = MetricsBehavior(metrics)
        result = await behavior.handle(_Qry(), _ok_next)
        assert result.is_success
        assert self._counter_value(metrics.query_total, "_Qry", "success") == 1.0

    @pytest.mark.asyncio
    async def test_non_command_query_passes_through_without_metrics(self):
        metrics = _test_metrics()
        behavior = MetricsBehavior(metrics)
        result = await behavior.handle(_Other(), _ok_next)
        assert result.is_success
        # No metric recorded for _Other — counter samples should all be zero
        all_values = [
            s.value
            for mf in metrics.command_total.collect()
            for s in mf.samples
        ]
        assert all(v == 0.0 for v in all_values)

    @pytest.mark.asyncio
    async def test_records_duration(self):
        metrics = _test_metrics()
        behavior = MetricsBehavior(metrics)
        await behavior.handle(_Cmd(), _ok_next)
        # Histogram emits a _count sample after at least one observation
        samples = {
            s.name: s.value
            for metric_family in metrics.command_duration.collect()
            for s in metric_family.samples
            if s.labels.get("name") == "_Cmd" and s.labels.get("outcome") == "success"
        }
        assert samples.get("qx_command_duration_seconds_count") == 1.0


# ---------------------------------------------------------------------------
# TracingBehavior
# ---------------------------------------------------------------------------


class TestTracingBehavior:
    @pytest.mark.asyncio
    async def test_passes_through_result_success(self):
        behavior = TracingBehavior()
        result = await behavior.handle(_Cmd(), _ok_next)
        assert result.is_success

    @pytest.mark.asyncio
    async def test_passes_through_result_failure(self):
        behavior = TracingBehavior()
        result = await behavior.handle(_Cmd(), _fail_next)
        assert result.is_failure

    @pytest.mark.asyncio
    async def test_non_command_query_passes_through(self):
        behavior = TracingBehavior()
        result = await behavior.handle(_Other(), _ok_next)
        assert result.is_success

    @pytest.mark.asyncio
    async def test_span_names_for_command_and_query(self):
        """OTel provider can only be set once globally; test both in one provider scope."""
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        behavior = TracingBehavior()
        await behavior.handle(_Cmd(), _ok_next)
        await behavior.handle(_Qry(), _ok_next)

        spans = exporter.get_finished_spans()
        names = [s.name for s in spans]
        assert "qx.command._Cmd" in names
        assert "qx.query._Qry" in names
