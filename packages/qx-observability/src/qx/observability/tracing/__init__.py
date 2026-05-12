"""OpenTelemetry tracing setup.

Configures a tracer provider with OTLP/gRPC export by default. Spans get
service-resource attributes (service.name, service.version, deployment.env)
so they're queryable in Tempo/Jaeger/Honeycomb without extra hops.

Two convenience APIs:

- ``configure_tracing(settings, exporter_endpoint=...)`` — set up the SDK.
- ``trace_span("name", attrs={...})`` — open a span in async code with the
  current request context attached.

We also export ``inject_context`` / ``extract_context`` for use at message
boundaries (NATS publish/consume) — they handle W3C trace-context propagation
without callers needing to know the propagator API.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import extract, inject
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.trace import Status, StatusCode

from qx.core import QxSettings, current_context

__all__ = [
    "configure_tracing",
    "trace_span",
    "get_tracer",
    "inject_context",
    "extract_context",
]


_PROVIDER: TracerProvider | None = None


def configure_tracing(
    settings: QxSettings,
    *,
    exporter_endpoint: str | None = None,
    console_export: bool = False,
    sample_ratio: float = 1.0,
) -> TracerProvider:
    """Initialize the global tracer provider.

    ``exporter_endpoint`` is OTLP/gRPC URL (e.g., ``http://otel-collector:4317``).
    If None, no remote export is configured — useful for tests and local
    development. ``console_export`` adds a stderr-printing processor; handy
    while iterating but very loud.
    """
    global _PROVIDER
    resource = Resource.create(
        {
            "service.name": settings.app.name,
            "service.version": settings.app.version,
            "deployment.environment": settings.environment,
        }
    )
    provider = TracerProvider(resource=resource)

    if exporter_endpoint is not None:
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=exporter_endpoint, insecure=True))
        )
    if console_export:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)
    _PROVIDER = provider
    return provider


def get_tracer(name: str = "qx") -> trace.Tracer:
    return trace.get_tracer(name)


@contextlib.contextmanager
def trace_span(
    name: str,
    *,
    attributes: dict[str, Any] | None = None,
    tracer_name: str = "qx",
) -> Iterator[trace.Span]:
    """Open a span. Attaches the current RequestContext as attributes.

    Exceptions raised inside the block are recorded on the span and the span
    is marked as ``ERROR`` before re-raising — so failed operations show up
    red in trace UIs without callers writing boilerplate.
    """
    tracer = get_tracer(tracer_name)
    span_attrs: dict[str, Any] = dict(attributes or {})
    ctx = current_context()
    if ctx.correlation_id is not None:
        span_attrs.setdefault("qx.correlation_id", str(ctx.correlation_id))
    if ctx.tenant_id is not None:
        span_attrs.setdefault("qx.tenant_id", str(ctx.tenant_id))
    if ctx.user_id is not None:
        span_attrs.setdefault("enduser.id", str(ctx.user_id))

    with tracer.start_as_current_span(name, attributes=span_attrs) as span:
        try:
            yield span
        except BaseException as exc:  # noqa: BLE001
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, description=str(exc)))
            raise


def inject_context(carrier: dict[str, str]) -> dict[str, str]:
    """Inject W3C trace context into a carrier dict (for outbound messages)."""
    inject(carrier)
    return carrier


def extract_context(carrier: dict[str, str]) -> Any:
    """Extract W3C trace context from a carrier dict (for inbound messages).

    Returns an OTel context that can be used as ``context=...`` when starting
    a span, restoring trace continuity across the message boundary.
    """
    return extract(carrier)
