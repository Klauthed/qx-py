"""Qx observability surface.

One-call setup for typical services::

    from qx.observability import setup_observability
    metrics, health = setup_observability(settings, otlp_endpoint="...")
"""

from __future__ import annotations

from qx.observability.health import (
    AggregateResult,
    CheckResult,
    HealthRegistry,
    HealthStatus,
)
from qx.observability.logging import (
    configure_logging,
    context_processor,
    get_logger,
)
from qx.observability.metrics import (
    CONTENT_TYPE_LATEST,
    Metrics,
    default_metrics,
    render_metrics,
)
from prometheus_client import CollectorRegistry  # for setup_observability typing
from qx.observability.tracing import (
    configure_tracing,
    extract_context,
    get_tracer,
    inject_context,
    trace_span,
)

from qx.observability.behaviors import MetricsBehavior, TracingBehavior
from qx.core import QxSettings

__version__ = "0.1.0"


def setup_observability(
    settings: QxSettings,
    *,
    otlp_endpoint: str | None = None,
    console_traces: bool = False,
    metrics_registry: "CollectorRegistry | None" = None,
) -> tuple[Metrics, HealthRegistry]:
    """One-call setup: configure logging + tracing, return metrics + health bundles.

    Pass ``metrics_registry`` to use a custom Prometheus CollectorRegistry —
    useful in tests where multiple service builds in one process would
    otherwise collide on the default registry.
    """
    configure_logging(
        settings.logging,
        service_name=settings.app.name,
        service_version=settings.app.version,
    )
    configure_tracing(
        settings,
        exporter_endpoint=otlp_endpoint,
        console_export=console_traces,
    )
    return Metrics(registry=metrics_registry), HealthRegistry()


__all__ = [
    "setup_observability",
    "configure_logging",
    "configure_tracing",
    "context_processor",
    "get_logger",
    "get_tracer",
    "trace_span",
    "inject_context",
    "extract_context",
    "Metrics",
    "default_metrics",
    "render_metrics",
    "CONTENT_TYPE_LATEST",
    "HealthRegistry",
    "HealthStatus",
    "CheckResult",
    "AggregateResult",
    "TracingBehavior",
    "MetricsBehavior",
    "__version__",
]
