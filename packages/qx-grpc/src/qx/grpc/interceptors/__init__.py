"""Server-side gRPC interceptors.

Mirrors what ``qx-http`` does as middleware:

- ``RequestContextInterceptor``: synthesizes / extracts correlation ids from
  metadata, opens a ``RequestContext`` for the call duration.
- ``MetricsInterceptor``: records framework metrics per RPC.
- ``ExceptionInterceptor``: catches Qx ``Error`` exceptions and converts
  them to rich gRPC status responses; catches unexpected exceptions and
  returns ``UNKNOWN`` with a logged stacktrace.

gRPC metadata keys are lowercase by convention; we use:

- ``qx-correlation-id``
- ``qx-request-id``
- ``qx-tenant-id``
"""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import UUID, uuid4

import grpc
from grpc.aio import ServerInterceptor

from qx.core import Error, RequestContext, request_scope

from qx.grpc.errors import status_from_error

__all__ = [
    "RequestContextInterceptor",
    "MetricsInterceptor",
    "ExceptionInterceptor",
]


_log = logging.getLogger("qx.grpc")


def _try_uuid(value: str | None) -> UUID | None:
    if not value:
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _extract(metadata: tuple[tuple[str, str], ...], key: str) -> str | None:
    for k, v in metadata:
        if k.lower() == key:
            return v
    return None


class RequestContextInterceptor(ServerInterceptor):
    """Open a RequestContext spanning every incoming RPC."""

    async def intercept_service(
        self,
        continuation: Any,
        handler_call_details: Any,
    ) -> Any:
        handler = await continuation(handler_call_details)
        original = handler.unary_unary if handler else None
        if not original:
            return handler

        async def wrapped(request: Any, context: grpc.aio.ServicerContext) -> Any:
            metadata = context.invocation_metadata() or ()
            correlation_id = (
                _try_uuid(_extract(metadata, "qx-correlation-id")) or uuid4()
            )
            request_id = _try_uuid(_extract(metadata, "qx-request-id")) or uuid4()
            tenant_id = _try_uuid(_extract(metadata, "qx-tenant-id"))

            ctx = RequestContext(
                correlation_id=correlation_id,
                request_id=request_id,
                tenant_id=tenant_id,
                attributes={
                    "rpc.method": handler_call_details.method,
                },
            )
            with request_scope(ctx):
                context.set_trailing_metadata(
                    (
                        ("qx-correlation-id", str(correlation_id)),
                        ("qx-request-id", str(request_id)),
                    )
                )
                return await original(request, context)

        return grpc.aio.unary_unary_rpc_method_handler(  # type: ignore[attr-defined]
            wrapped,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )


class ExceptionInterceptor(ServerInterceptor):
    """Translate Error exceptions to gRPC status; suppress unknowns."""

    async def intercept_service(
        self,
        continuation: Any,
        handler_call_details: Any,
    ) -> Any:
        handler = await continuation(handler_call_details)
        original = handler.unary_unary if handler else None
        if not original:
            return handler

        async def wrapped(request: Any, context: grpc.aio.ServicerContext) -> Any:
            try:
                return await original(request, context)
            except Error as err:
                status = status_from_error(err)
                await context.abort_with_status(_StatusProxy(status))  # type: ignore[arg-type]
                return None  # unreachable
            except Exception as exc:  # noqa: BLE001
                _log.exception("unhandled gRPC exception")
                await context.abort(grpc.StatusCode.UNKNOWN, "internal error")
                return None

        return grpc.aio.unary_unary_rpc_method_handler(  # type: ignore[attr-defined]
            wrapped,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )


class _StatusProxy:
    """Adapter for the rich ``google.rpc.Status`` -> gRPC abort_with_status."""

    def __init__(self, status: Any) -> None:
        self.code = grpc.StatusCode.UNKNOWN
        for sc in grpc.StatusCode:
            if sc.value[0] == status.code:
                self.code = sc
                break
        self.details = status.message
        self.trailing_metadata = ()


class MetricsInterceptor(ServerInterceptor):
    """Record framework metrics per RPC."""

    def __init__(self, metrics: Any) -> None:
        self._m = metrics

    async def intercept_service(
        self,
        continuation: Any,
        handler_call_details: Any,
    ) -> Any:
        handler = await continuation(handler_call_details)
        original = handler.unary_unary if handler else None
        if not original:
            return handler

        method = handler_call_details.method
        m = self._m

        async def wrapped(request: Any, context: grpc.aio.ServicerContext) -> Any:
            start = time.perf_counter()
            outcome = "success"
            try:
                return await original(request, context)
            except Exception:
                outcome = "failure"
                raise
            finally:
                duration = time.perf_counter() - start
                # Reuse http_request_* counters for unified dashboards; method
                # holds the gRPC fully-qualified method name (slash-prefixed).
                try:
                    m.http_request_total.labels(
                        method="GRPC", route=method, status=outcome
                    ).inc()
                    m.http_request_duration.labels(
                        method="GRPC", route=method
                    ).observe(duration)
                except Exception:  # noqa: BLE001
                    pass

        return grpc.aio.unary_unary_rpc_method_handler(  # type: ignore[attr-defined]
            wrapped,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
