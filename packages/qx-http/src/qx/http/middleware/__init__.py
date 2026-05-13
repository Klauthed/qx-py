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

``RegionRedirectMiddleware`` is an optional add-on for multi-region deployments.
It must be added AFTER ``RequestContextMiddleware`` so the tenant context is
established before region resolution runs.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from opentelemetry import trace as otel_trace
from opentelemetry.propagate import extract as otel_extract
from qx.core import RequestContext, current_context, request_scope
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

if TYPE_CHECKING:
    from qx.regions import RegionRouter
    from starlette.requests import Request
    from starlette.types import ASGIApp

__all__ = [
    "MetricsMiddleware",
    "RegionRedirectMiddleware",
    "RequestContextMiddleware",
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

        # Extract W3C trace context from incoming headers so this span becomes
        # a child of the upstream span (gateway, CDN, client SDK, etc.).
        parent_ctx = otel_extract(dict(request.headers))

        tracer = otel_trace.get_tracer("qx.http")
        with tracer.start_as_current_span(
            "http.request",
            context=parent_ctx,
            kind=otel_trace.SpanKind.SERVER,
            attributes={
                "http.method": request.method,
                "http.target": str(request.url.path),
                "http.scheme": request.url.scheme,
                "http.host": request.url.hostname or "",
                "net.peer.ip": request.client.host if request.client else "",
            },
        ) as span:
            # Derive trace_id from the active OTel span so RequestContext
            # always reflects the real distributed trace, not a manual parse.
            span_ctx = span.get_span_context()
            trace_id = format(span_ctx.trace_id, "032x") if span_ctx and span_ctx.is_valid else None

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

            span.set_attribute("http.status_code", response.status_code)

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
            except Exception:
                pass


_WRITE_METHODS: frozenset[str] = frozenset({"DELETE", "PATCH", "POST", "PUT"})
_INFRA_PREFIXES: tuple[str, ...] = ("/healthz", "/metrics", "/readyz")


class RegionRedirectMiddleware(BaseHTTPMiddleware):
    """Redirect write requests to the tenant's home region.

    Reads the tenant_id from the active ``RequestContext`` (populated by
    ``RequestContextMiddleware``) and, if the tenant's home region differs from
    this instance's region, returns a **307 Temporary Redirect** to the
    equivalent URL on the home region.

    Read traffic (GET, HEAD, OPTIONS) is always served locally. Internal
    infrastructure paths (``/healthz``, ``/readyz``, ``/metrics``) are
    always served locally regardless of method.

    This middleware must be added **after** ``RequestContextMiddleware`` in the
    middleware stack so the tenant context is established before region
    resolution runs. With ``setup_qx_app`` pass ``region_router=`` and it is
    inserted automatically in the right position::

        app = setup_qx_app(container, settings, ..., region_router=router)

    Or add it manually (ensure ordering)::

        app.add_middleware(RegionRedirectMiddleware, router=router)
        app.add_middleware(RequestContextMiddleware)  # must be outermost
    """

    def __init__(
        self,
        app: ASGIApp,
        router: RegionRouter,
        *,
        write_methods: frozenset[str] = _WRITE_METHODS,
        skip_prefixes: tuple[str, ...] = _INFRA_PREFIXES,
    ) -> None:
        super().__init__(app)
        self._router = router
        self._write_methods = write_methods
        self._skip_prefixes = skip_prefixes

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if request.method not in self._write_methods:
            return await call_next(request)

        path = request.url.path
        if any(path.startswith(p) for p in self._skip_prefixes):
            return await call_next(request)

        try:
            ctx: RequestContext | None = current_context()
        except LookupError:
            ctx = None

        if ctx is None or ctx.tenant_id is None:
            return await call_next(request)

        redirect_url = await self._router.get_redirect_url(ctx.tenant_id, path)
        if redirect_url is None:
            return await call_next(request)

        if request.url.query:
            redirect_url = f"{redirect_url}?{request.url.query}"

        return Response(status_code=307, headers={"Location": redirect_url})
