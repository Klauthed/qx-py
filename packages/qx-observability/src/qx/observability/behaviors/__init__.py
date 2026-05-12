"""Observability pipeline behaviors.

Drop-in behaviors for the CQRS pipeline that add OpenTelemetry tracing and
Prometheus metrics to every command/query dispatch.

Recommended position in the pipeline (outermost → innermost)::

    LoggingBehavior
    TracingBehavior      ← opens a span covering the full dispatch
    MetricsBehavior      ← records counter + histogram per dispatch
    ValidationBehavior
    ...

Both behaviors are no-ops for messages that are neither Command nor Query, so
it is safe to place them on a shared pipeline.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from opentelemetry.trace import Status, StatusCode
from qx.core import Result, current_context
from qx.cqrs.messages import Command, Query
from qx.cqrs.pipeline import Behavior  # noqa: F401 (re-exported for convenience)
from qx.observability.tracing import get_tracer

if TYPE_CHECKING:
    from qx.cqrs.pipeline import Next
    from qx.observability.metrics import Metrics

__all__ = ["MetricsBehavior", "TracingBehavior"]


class TracingBehavior:
    """Open an OpenTelemetry span for each command/query dispatch.

    The span is named ``"qx.command.<MessageClass>"`` or
    ``"qx.query.<MessageClass>"``. Standard request attributes are attached
    (``qx.correlation_id``, ``qx.tenant_id``, ``enduser.id``) alongside the
    message class name and the final outcome.

    Failures in the ``Result`` are recorded as span events. Exceptions that
    escape the handler (should only happen if
    ``ExceptionTranslationBehavior`` is absent) mark the span ``ERROR`` and
    re-raise.

    Args:
        tracer_name: The instrumentation scope name passed to
            ``opentelemetry.trace.get_tracer``. Defaults to ``"qx"``.
    """

    def __init__(self, *, tracer_name: str = "qx") -> None:
        self._tracer_name = tracer_name

    async def handle(self, message: Any, next_: Next) -> Result[Any]:
        if isinstance(message, Command):
            kind = "command"
        elif isinstance(message, Query):
            kind = "query"
        else:
            return await next_(message)

        msg_cls = type(message).__name__
        span_name = f"qx.{kind}.{msg_cls}"

        ctx = current_context()
        attrs: dict[str, Any] = {"qx.message_type": kind, "qx.message_class": msg_cls}
        if ctx.correlation_id is not None:
            attrs["qx.correlation_id"] = str(ctx.correlation_id)
        if ctx.tenant_id is not None:
            attrs["qx.tenant_id"] = str(ctx.tenant_id)
        if ctx.user_id is not None:
            attrs["enduser.id"] = str(ctx.user_id)

        tracer = get_tracer(self._tracer_name)
        with tracer.start_as_current_span(span_name, attributes=attrs) as span:
            try:
                result = await next_(message)
            except BaseException as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR, description=str(exc)))
                raise

            if result.is_success:
                span.set_attribute("qx.outcome", "success")
            else:
                err = result.error
                span.set_attribute("qx.outcome", "failure")
                span.set_attribute("qx.error_code", err.code)
                span.set_attribute("qx.error_category", err.category)
                span.add_event("handler_failed", {"error_code": err.code})

            return result


class MetricsBehavior:
    """Record Prometheus metrics for each command/query dispatch.

    Uses the framework-level ``Metrics`` bundle (``qx.observability.metrics``)
    to increment ``qx_command_total`` / ``qx_query_total`` counters and observe
    ``qx_command_duration_seconds`` / ``qx_query_duration_seconds`` histograms.

    Both are labelled with ``name`` (the message class name) and ``outcome``
    (``"success"`` or ``"failure"``).

    Args:
        metrics: A ``Metrics`` instance. Typically a DI-injected singleton
            constructed at application startup via ``setup_observability()``.
    """

    def __init__(self, metrics: Metrics) -> None:
        self._metrics = metrics

    async def handle(self, message: Any, next_: Next) -> Result[Any]:
        if isinstance(message, Command):
            kind = "command"
        elif isinstance(message, Query):
            kind = "query"
        else:
            return await next_(message)

        msg_cls = type(message).__name__
        start = time.perf_counter()

        result = await next_(message)

        elapsed = time.perf_counter() - start
        outcome = "success" if result.is_success else "failure"

        if kind == "command":
            self._metrics.command_total.labels(name=msg_cls, outcome=outcome).inc()
            self._metrics.command_duration.labels(name=msg_cls, outcome=outcome).observe(elapsed)
        else:
            self._metrics.query_total.labels(name=msg_cls, outcome=outcome).inc()
            self._metrics.query_duration.labels(name=msg_cls, outcome=outcome).observe(elapsed)

        return result
