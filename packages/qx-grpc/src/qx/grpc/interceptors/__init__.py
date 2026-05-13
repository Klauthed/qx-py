"""Server-side gRPC interceptors — all four RPC shapes.

Mirrors what ``qx-http`` does as middleware:

- ``RequestContextInterceptor``: extracts correlation/tenant ids from
  metadata, opens a ``RequestContext`` for the call duration, and propagates
  deadline info into ``RequestContext.attributes``.
- ``ExceptionInterceptor``: catches Qx ``Error`` exceptions and converts them
  to rich gRPC status responses; catches unexpected exceptions as ``UNKNOWN``;
  lets ``asyncio.CancelledError`` propagate (client-cancelled calls).
- ``MetricsInterceptor``: records framework metrics per RPC.
- ``JwtAuthInterceptor``: validates ``Authorization: Bearer <token>`` from
  metadata and stores the resulting ``Principal`` in
  ``RequestContext.attributes["qx.principal"]``.  Lenient — missing or
  invalid tokens are silently ignored.

All interceptors wrap **all four** handler shapes:
``unary_unary``, ``unary_stream``, ``stream_unary``, ``stream_stream``.

gRPC metadata keys are lowercase by convention; we use:

- ``qx-correlation-id``
- ``qx-request-id``
- ``qx-tenant-id``
- ``authorization``
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from uuid import UUID, uuid4

from qx.core import ErrorException, RequestContext, current_context, request_scope
from qx.grpc.errors import status_from_error

import grpc
from grpc.aio import ServerInterceptor

__all__ = [
    "ExceptionInterceptor",
    "JwtAuthInterceptor",
    "MetricsInterceptor",
    "RequestContextInterceptor",
]

_log = logging.getLogger("qx.grpc")

PRINCIPAL_KEY = "qx.principal"

# ---------------------------------------------------------------------------
# Handler introspection helpers
# ---------------------------------------------------------------------------


def _shape(handler: Any) -> tuple[Any, bool, bool]:
    """Return (callable, request_streaming, response_streaming)."""
    if handler.unary_unary:
        return handler.unary_unary, False, False
    if handler.unary_stream:
        return handler.unary_stream, False, True
    if handler.stream_unary:
        return handler.stream_unary, True, False
    if handler.stream_stream:
        return handler.stream_stream, True, True
    return None, False, False


def _rebuild(handler: Any, new_fn: Any, req_stream: bool, resp_stream: bool) -> Any:
    kwargs = {
        "request_deserializer": handler.request_deserializer,
        "response_serializer": handler.response_serializer,
    }
    if not req_stream and not resp_stream:
        return grpc.unary_unary_rpc_method_handler(new_fn, **kwargs)
    if not req_stream and resp_stream:
        return grpc.unary_stream_rpc_method_handler(new_fn, **kwargs)
    if req_stream and not resp_stream:
        return grpc.stream_unary_rpc_method_handler(new_fn, **kwargs)
    return grpc.stream_stream_rpc_method_handler(new_fn, **kwargs)


# ---------------------------------------------------------------------------
# Metadata helpers
# ---------------------------------------------------------------------------


def _try_uuid(value: str | None) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _extract(metadata: Any, key: str) -> str | None:
    for k, v in metadata or ():
        if k.lower() == key:
            return v
    return None


def _build_request_ctx(context: grpc.aio.ServicerContext, method: str) -> RequestContext:
    metadata = context.invocation_metadata() or ()
    correlation_id = _try_uuid(_extract(metadata, "qx-correlation-id")) or uuid4()
    request_id = _try_uuid(_extract(metadata, "qx-request-id")) or uuid4()
    tenant_id = _try_uuid(_extract(metadata, "qx-tenant-id"))

    attrs: dict[str, Any] = {"rpc.method": method}
    remaining = context.time_remaining()
    if remaining < 1e9:
        attrs["rpc.deadline_seconds"] = remaining

    return RequestContext(
        correlation_id=correlation_id,
        request_id=request_id,
        tenant_id=tenant_id,
        attributes=attrs,
    )


# ---------------------------------------------------------------------------
# Interceptors
# ---------------------------------------------------------------------------


class RequestContextInterceptor(ServerInterceptor):  # type: ignore[misc]
    """Open a RequestContext spanning every incoming RPC (all handler types)."""

    async def intercept_service(
        self,
        continuation: Any,
        handler_call_details: Any,
    ) -> Any:
        handler = await continuation(handler_call_details)
        if not handler:
            return handler

        fn, req_stream, resp_stream = _shape(handler)
        if fn is None:
            return handler

        method = handler_call_details.method

        if not resp_stream:

            async def _unary(arg: Any, context: grpc.aio.ServicerContext) -> Any:
                ctx = _build_request_ctx(context, method)
                with request_scope(ctx):
                    context.set_trailing_metadata(
                        (
                            ("qx-correlation-id", str(ctx.correlation_id)),
                            ("qx-request-id", str(ctx.request_id)),
                        ),
                    )
                    return await fn(arg, context)

            return _rebuild(handler, _unary, req_stream, resp_stream)

        async def _streaming(arg: Any, context: grpc.aio.ServicerContext) -> Any:
            ctx = _build_request_ctx(context, method)
            with request_scope(ctx):
                context.set_trailing_metadata(
                    (
                        ("qx-correlation-id", str(ctx.correlation_id)),
                        ("qx-request-id", str(ctx.request_id)),
                    ),
                )
                async for item in fn(arg, context):
                    yield item

        return _rebuild(handler, _streaming, req_stream, resp_stream)


class ExceptionInterceptor(ServerInterceptor):  # type: ignore[misc]
    """Translate Error exceptions to gRPC status for all RPC shapes.

    ``asyncio.CancelledError`` is re-raised so the framework can properly
    close client-cancelled calls.
    """

    async def intercept_service(
        self,
        continuation: Any,
        handler_call_details: Any,
    ) -> Any:
        handler = await continuation(handler_call_details)
        if not handler:
            return handler

        fn, req_stream, resp_stream = _shape(handler)
        if fn is None:
            return handler

        if not resp_stream:

            async def _unary(arg: Any, context: grpc.aio.ServicerContext) -> Any:
                try:
                    return await fn(arg, context)
                except asyncio.CancelledError:
                    raise
                except ErrorException as exc:
                    await context.abort_with_status(_StatusProxy(status_from_error(exc.error)))
                    return None
                except Exception:
                    _log.exception("unhandled gRPC exception")
                    await context.abort(grpc.StatusCode.UNKNOWN, "internal error")
                    return None

            return _rebuild(handler, _unary, req_stream, resp_stream)

        async def _streaming(arg: Any, context: grpc.aio.ServicerContext) -> Any:
            try:
                async for item in fn(arg, context):
                    yield item
            except asyncio.CancelledError:
                raise
            except ErrorException as exc:
                await context.abort_with_status(_StatusProxy(status_from_error(exc.error)))
            except Exception:
                _log.exception("unhandled gRPC streaming exception")
                await context.abort(grpc.StatusCode.UNKNOWN, "internal error")

        return _rebuild(handler, _streaming, req_stream, resp_stream)


class _StatusProxy:
    """Adapter: google.rpc.Status → grpc abort_with_status."""

    def __init__(self, status: Any) -> None:
        self.code = grpc.StatusCode.UNKNOWN
        for sc in grpc.StatusCode:
            if sc.value[0] == status.code:
                self.code = sc
                break
        self.details = status.message
        self.trailing_metadata = ()


class MetricsInterceptor(ServerInterceptor):  # type: ignore[misc]
    """Record framework metrics per RPC (all handler shapes).

    Args:
        metrics: The framework ``Metrics`` bundle.
        per_method_buckets: Optional mapping of RPC method name (e.g.
            ``"/mypackage.MyService/MyMethod"``) to a tuple of histogram
            bucket boundaries in seconds.  Methods not present here use
            the shared ``http_request_duration`` histogram from ``metrics``.
            Buckets are applied once, on the first call for that method.

    Example::

        MetricsInterceptor(
            metrics,
            per_method_buckets={
                "/order.OrderService/PlaceOrder": (0.01, 0.05, 0.1, 0.5, 1.0, 5.0),
            },
        )
    """

    def __init__(
        self,
        metrics: Any,
        *,
        per_method_buckets: dict[str, tuple[float, ...]] | None = None,
    ) -> None:
        self._m = metrics
        self._per_method_buckets = per_method_buckets or {}
        self._method_histograms: dict[str, Any] = {}

    def _latency_histogram(self, method: str) -> Any:
        """Return (or lazily create) the latency histogram for a method."""
        if method in self._method_histograms:
            return self._method_histograms[method]

        if method in self._per_method_buckets:
            try:
                from prometheus_client import Histogram  # noqa: PLC0415

                hist = Histogram(
                    "qx_grpc_method_duration_seconds",
                    "gRPC method latency with per-method buckets.",
                    labelnames=("method",),
                    buckets=self._per_method_buckets[method],
                    registry=self._m.registry,
                )
            except Exception:
                hist = None
            self._method_histograms[method] = hist
            return hist

        return None

    async def intercept_service(
        self,
        continuation: Any,
        handler_call_details: Any,
    ) -> Any:
        handler = await continuation(handler_call_details)
        if not handler:
            return handler

        fn, req_stream, resp_stream = _shape(handler)
        if fn is None:
            return handler

        method = handler_call_details.method
        m = self._m
        interceptor = self

        if not resp_stream:

            async def _unary(arg: Any, context: grpc.aio.ServicerContext) -> Any:
                start = time.perf_counter()
                outcome = "success"
                try:
                    return await fn(arg, context)
                except Exception:
                    outcome = "failure"
                    raise
                finally:
                    _record_metrics(m, method, outcome, time.perf_counter() - start, interceptor)

            return _rebuild(handler, _unary, req_stream, resp_stream)

        async def _streaming(arg: Any, context: grpc.aio.ServicerContext) -> Any:
            start = time.perf_counter()
            outcome = "success"
            try:
                async for item in fn(arg, context):
                    yield item
            except Exception:
                outcome = "failure"
                raise
            finally:
                _record_metrics(m, method, outcome, time.perf_counter() - start, interceptor)

        return _rebuild(handler, _streaming, req_stream, resp_stream)


def _record_metrics(
    m: Any,
    method: str,
    outcome: str,
    duration: float,
    interceptor: MetricsInterceptor | None = None,
) -> None:
    try:
        m.http_request_total.labels(method="GRPC", route=method, status=outcome).inc()
        # Use per-method histogram when configured; fall back to shared histogram.
        hist = interceptor._latency_histogram(method) if interceptor else None
        if hist is not None:
            hist.labels(method=method).observe(duration)
        else:
            m.http_request_duration.labels(method="GRPC", route=method).observe(duration)
    except Exception:
        pass


class JwtAuthInterceptor(ServerInterceptor):  # type: ignore[misc]
    """Validate Bearer tokens from gRPC metadata and store the Principal.

    Lenient — missing or invalid tokens are silently ignored; enforcement
    happens via ``require_auth`` / ``AuthorizationBehavior`` downstream.

    Requires ``RequestContextInterceptor`` to be outer in the chain (so the
    ``RequestContext`` is open when this interceptor runs).

    Usage::

        server = create_grpc_server(
            container,
            metrics=metrics,
            extra_interceptors=(JwtAuthInterceptor(validator),),
        )
    """

    def __init__(
        self,
        validator: Any,
        *,
        principal_key: str = PRINCIPAL_KEY,
    ) -> None:
        self._validator = validator
        self._principal_key = principal_key

    async def intercept_service(
        self,
        continuation: Any,
        handler_call_details: Any,
    ) -> Any:
        handler = await continuation(handler_call_details)
        if not handler:
            return handler

        fn, req_stream, resp_stream = _shape(handler)
        if fn is None:
            return handler

        validator = self._validator
        principal_key = self._principal_key

        if not resp_stream:

            async def _unary(arg: Any, context: grpc.aio.ServicerContext) -> Any:
                await _inject_principal(context, validator, principal_key)
                return await fn(arg, context)

            return _rebuild(handler, _unary, req_stream, resp_stream)

        async def _streaming(arg: Any, context: grpc.aio.ServicerContext) -> Any:
            await _inject_principal(context, validator, principal_key)
            async for item in fn(arg, context):
                yield item

        return _rebuild(handler, _streaming, req_stream, resp_stream)


async def _inject_principal(
    context: grpc.aio.ServicerContext,
    validator: Any,
    principal_key: str,
) -> None:
    """Extract bearer token from metadata, validate, store in context."""
    auth = _extract(context.invocation_metadata() or (), "authorization")
    if not auth or not auth.lower().startswith("bearer "):
        return
    token = auth[7:].strip() or None
    if not token:
        return
    try:
        result = await validator.validate(token)
        if result.is_success:
            current_context().attributes[principal_key] = result.value
    except Exception:
        pass
