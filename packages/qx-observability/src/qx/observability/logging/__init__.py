"""Structured logging via :mod:`structlog`.

Every log record gets enriched automatically with:

- The current ``RequestContext`` (correlation_id, request_id, trace_id,
  user_id, tenant_id) when one is active.
- The active OpenTelemetry span context (trace_id, span_id) when tracing is
  configured — this is what lets you click from a log line to the corresponding
  trace in Jaeger.
- Service name + version from the framework settings.

JSON output by default in non-local environments; pretty console in local.
Configure once at startup via ``configure_logging(settings)``.

Design choice: we wrap stdlib ``logging`` underneath rather than running pure
structlog. Reason: third-party libraries (SQLAlchemy, FastAPI, asyncpg) all log
via stdlib; if structlog isn't bridged, those go to stderr unformatted and you
lose half your operational signal. The wrap is cheap and gets us a single
JSON stream.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog
from structlog.types import EventDict, Processor

from qx.core import LoggingSection, current_context

__all__ = ["configure_logging", "get_logger", "context_processor"]


def context_processor(_logger: Any, _name: str, event_dict: EventDict) -> EventDict:
    """structlog processor: enrich every record with the active RequestContext.

    Only fields that are actually set get added — empty context values are
    skipped so the output stays uncluttered for, e.g., startup logs that
    happen before any request is in flight.
    """
    ctx = current_context()
    if ctx.correlation_id is not None:
        event_dict.setdefault("correlation_id", str(ctx.correlation_id))
    if ctx.request_id is not None:
        event_dict.setdefault("request_id", str(ctx.request_id))
    if ctx.trace_id:
        event_dict.setdefault("trace_id", ctx.trace_id)
    if ctx.span_id:
        event_dict.setdefault("span_id", ctx.span_id)
    if ctx.user_id is not None:
        event_dict.setdefault("user_id", str(ctx.user_id))
    if ctx.tenant_id is not None:
        event_dict.setdefault("tenant_id", str(ctx.tenant_id))
    return event_dict


def _otel_processor(_logger: Any, _name: str, event_dict: EventDict) -> EventDict:
    """Optionally enrich with OTel span context (trace_id/span_id) if a span is active.

    Lazily imported so packages without OTel installed still work.
    """
    try:
        from opentelemetry import trace as _trace
    except ImportError:
        return event_dict
    span = _trace.get_current_span()
    span_ctx = span.get_span_context()
    if span_ctx.is_valid:
        # The 32-hex / 16-hex forms match Jaeger/Tempo expectations.
        event_dict.setdefault("otel_trace_id", f"{span_ctx.trace_id:032x}")
        event_dict.setdefault("otel_span_id", f"{span_ctx.span_id:016x}")
    return event_dict


def configure_logging(
    settings: LoggingSection | None = None,
    *,
    service_name: str | None = None,
    service_version: str | None = None,
) -> None:
    """Configure structlog + stdlib logging.

    Call once during service startup, before anything else logs. Idempotent —
    re-calling reconfigures cleanly.
    """
    settings = settings or LoggingSection()

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        context_processor,
        _otel_processor,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if service_name is not None:
        shared_processors.insert(
            0,
            lambda _l, _n, d: {**d, "service": service_name},
        )
    if service_version is not None:
        shared_processors.insert(
            0,
            lambda _l, _n, d: {**d, "service_version": service_version},
        )
    if settings.include_caller:
        shared_processors.append(
            structlog.processors.CallsiteParameterAdder(
                parameters=[
                    structlog.processors.CallsiteParameter.FILENAME,
                    structlog.processors.CallsiteParameter.LINENO,
                    structlog.processors.CallsiteParameter.FUNC_NAME,
                ]
            )
        )

    # Final renderer differs by format.
    if settings.json_output:
        final: Processor = structlog.processors.JSONRenderer()
    else:
        final = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=[*shared_processors, final],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.level)
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )

    # Bridge stdlib → structlog. This catches SQLAlchemy, FastAPI, asyncpg, etc.
    # We strip stdlib's own handlers and install one that formats via structlog.
    stdlib_root = logging.getLogger()
    stdlib_root.handlers.clear()
    stdlib_root.setLevel(getattr(logging, settings.level))
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                final,
            ],
            foreign_pre_chain=shared_processors,
        )
    )
    stdlib_root.addHandler(handler)

    # Tame chatty libraries we don't want at INFO.
    for noisy in ("asyncio", "urllib3.connectionpool"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str | None = None) -> Any:
    """Return a structlog logger bound to ``name`` (or the caller's module)."""
    return structlog.get_logger(name)
