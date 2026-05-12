"""HTTP middleware.

Three middlewares applied in this order (outermost to innermost):

1. ``ExceptionContextMiddleware`` — runs last in the stack so errors thrown
   inside other middlewares still get serialized through the envelope.
2. ``RequestContextMiddleware`` — synthesizes / extracts correlation-id,
   request-id, tenant, user, opens a ``RequestContext`` for the duration of
   the request, and propagates them to logs/traces.
3. ``MetricsMiddleware`` — increments framework counters, records latency.

All middleware respect the ``X-Correlation-Id`` header convention: if present,
we adopt it; if absent, we mint a fresh UUID. The chosen id goes back in the
response header so clients (and CDN logs, and gateway access logs) can join
on it.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID, uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from qx.core import RequestContext, request_scope

__all__ = [
    "RequestContextMiddleware",
    "MetricsMiddleware",
]


HEADER_CORRELATION = "x-correlation-id"
HEADER_REQUEST = "x-request-id"
HEADER_TENANT = "x-tenant-id"
HEADER_TRACE = "traceparent"


def _try_uuid(value: str | None) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Establish a ``RequestContext`` per HTTP request.

    Reads correlation/request ids from incoming headers; synthesizes them if
    absent. Reflects the resolved values back as response headers so clients
    can correlate.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        correlation_id = _try_uuid(request.headers.get(HEADER_CORRELATION)) or uuid4()
        request_id = _try_uuid(request.headers.get(HEADER_REQUEST)) or uuid4()
        tenant_id = _try_uuid(request.headers.get(HEADER_TENANT))
        traceparent = request.headers.get(HEADER_TRACE)

        # Trace_id from W3C traceparent is the middle field (16 bytes hex).
        trace_id: str | None = None
        if traceparent:
            parts = traceparent.split("-")
            if len(parts) == 4:
                trace_id = parts[1]

        ctx = RequestContext(
            request_id=request_id,
            correlation_id=correlation_id,
            trace_id=trace_id,
            tenant_id=tenant_id,
            attributes={
                "http.method": request.method,
                "http.path": request.url.path,
                "http.client": request.client.host if request.client else None,
            },
        )
        with request_scope(ctx):
            response = await call_next(request)
        response.headers[HEADER_CORRELATION] = str(correlation_id)
        response.headers[HEADER_REQUEST] = str(request_id)
        return response


class MetricsMiddleware(BaseHTTPMiddleware):
    """Record per-request HTTP metrics on a ``Metrics`` instance.

    Route label uses the matched FastAPI path template (``/users/{id}``) not
    the literal URL, so cardinality stays bounded. Falls back to the raw path
    if no route was matched (404 cases).
    """

    def __init__(self, app: ASGIApp, metrics: Any) -> None:
        super().__init__(app)
        self._m = metrics

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        start = time.perf_counter()
        response: Response | None = None
        try:
            response = await call_next(request)
            return response
        finally:
            duration = time.perf_counter() - start
            route = request.scope.get("route")
            route_path = getattr(route, "path", request.url.path)
            status_code = response.status_code if response is not None else 500
            try:
                self._m.http_request_total.labels(
                    method=request.method,
                    route=route_path,
                    status=str(status_code),
                ).inc()
                self._m.http_request_duration.labels(
                    method=request.method,
                    route=route_path,
                ).observe(duration)
            except Exception:  # noqa: BLE001 — metrics must never break the request
                pass
