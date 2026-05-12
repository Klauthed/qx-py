"""Prometheus metrics.

We define a small set of *framework-level* metrics that every Qx service
exposes automatically:

- ``qx_command_total{name, outcome}`` — count of command dispatches
- ``qx_command_duration_seconds{name, outcome}`` — histogram
- ``qx_query_total{name, outcome}`` — count of query dispatches
- ``qx_query_duration_seconds{name, outcome}`` — histogram
- ``qx_event_total{name}`` — count of domain events published
- ``qx_integration_event_total{name, direction}`` — outbox/consumer events
- ``qx_http_request_total{method, route, status}`` — HTTP requests
- ``qx_http_request_duration_seconds{method, route}`` — histogram
- ``qx_outbox_pending`` — gauge of unprocessed outbox rows

Services get these for free via the framework. They can also add their own
business-specific metrics through the same registry.

Histogram buckets are chosen for typical service latency: sub-millisecond up
to 10 seconds. Adjust per-service if you have stricter SLOs.
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

__all__ = ["Metrics", "default_metrics", "render_metrics", "CONTENT_TYPE_LATEST"]


# Buckets in seconds. Tail matters more than the median for SLI graphs, so we
# concentrate buckets where slow-but-not-broken requests live.
DEFAULT_BUCKETS = (
    0.001, 0.005, 0.010, 0.025, 0.050, 0.100, 0.250, 0.500,
    1.0, 2.5, 5.0, 10.0,
)


class Metrics:
    """A bundle of framework-level metrics.

    Constructed once at startup and registered in DI as a singleton. Services
    inject this and use its counters/histograms directly, or call ``counter()``
    / ``histogram()`` to define their own.
    """

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or REGISTRY

        self.command_total = Counter(
            "qx_command_total",
            "Number of commands dispatched.",
            labelnames=("name", "outcome"),
            registry=self.registry,
        )
        self.command_duration = Histogram(
            "qx_command_duration_seconds",
            "Time spent handling a command (end-to-end through the mediator).",
            labelnames=("name", "outcome"),
            buckets=DEFAULT_BUCKETS,
            registry=self.registry,
        )
        self.query_total = Counter(
            "qx_query_total",
            "Number of queries dispatched.",
            labelnames=("name", "outcome"),
            registry=self.registry,
        )
        self.query_duration = Histogram(
            "qx_query_duration_seconds",
            "Time spent handling a query.",
            labelnames=("name", "outcome"),
            buckets=DEFAULT_BUCKETS,
            registry=self.registry,
        )
        self.event_total = Counter(
            "qx_event_total",
            "Number of domain events published in-process.",
            labelnames=("name",),
            registry=self.registry,
        )
        self.integration_event_total = Counter(
            "qx_integration_event_total",
            "Number of integration events that crossed the service boundary.",
            labelnames=("name", "direction"),  # direction: 'out' (outbox) | 'in' (consumed)
            registry=self.registry,
        )
        self.http_request_total = Counter(
            "qx_http_request_total",
            "Number of HTTP requests served.",
            labelnames=("method", "route", "status"),
            registry=self.registry,
        )
        self.http_request_duration = Histogram(
            "qx_http_request_duration_seconds",
            "HTTP request duration.",
            labelnames=("method", "route"),
            buckets=DEFAULT_BUCKETS,
            registry=self.registry,
        )
        self.outbox_pending = Gauge(
            "qx_outbox_pending",
            "Number of unprocessed rows in the outbox table.",
            registry=self.registry,
        )

    # Convenience for services adding their own metrics that share the registry.
    def counter(self, name: str, doc: str, labelnames: tuple[str, ...] = ()) -> Counter:
        return Counter(name, doc, labelnames=labelnames, registry=self.registry)

    def histogram(
        self,
        name: str,
        doc: str,
        labelnames: tuple[str, ...] = (),
        buckets: tuple[float, ...] = DEFAULT_BUCKETS,
    ) -> Histogram:
        return Histogram(
            name, doc, labelnames=labelnames, buckets=buckets, registry=self.registry
        )

    def gauge(self, name: str, doc: str, labelnames: tuple[str, ...] = ()) -> Gauge:
        return Gauge(name, doc, labelnames=labelnames, registry=self.registry)


_default: Metrics | None = None


def default_metrics() -> Metrics:
    """Lazily constructed module-level Metrics for adapter code that can't take an injection."""
    global _default
    if _default is None:
        _default = Metrics()
    return _default


def render_metrics(metrics: Metrics | None = None) -> bytes:
    """Render the metrics exposition format. Mount at ``/metrics``."""
    m = metrics or default_metrics()
    return generate_latest(m.registry)
