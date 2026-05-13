"""Interceptor unit tests — all four RPC shapes + deadline + JWT auth.

Tests the three core interceptors (RequestContext, Exception, Metrics) and
the new JwtAuthInterceptor using fake gRPC handlers and a mock
ServicerContext.  No real gRPC server is started.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import grpc
import pytest
from qx.core import NotFoundError, RequestContext, current_context, request_scope
from qx.grpc.interceptors import (
    ExceptionInterceptor,
    JwtAuthInterceptor,
    MetricsInterceptor,
    RequestContextInterceptor,
)

# ---------------------------------------------------------------------------
# Fake gRPC infrastructure
# ---------------------------------------------------------------------------


class _FakeContext:
    """Stand-in for grpc.aio.ServicerContext."""

    def __init__(
        self,
        *,
        metadata: tuple[tuple[str, str], ...] = (),
        time_remaining: float = 1e10,
    ) -> None:
        self._metadata = metadata
        self._time_remaining = time_remaining
        self.trailing_metadata_set: tuple | None = None
        self.aborted_code: Any = None
        self.aborted_message: str = ""
        self.aborted_status: Any = None

    def invocation_metadata(self) -> tuple[tuple[str, str], ...]:
        return self._metadata

    def set_trailing_metadata(self, metadata: tuple) -> None:
        self.trailing_metadata_set = metadata

    def time_remaining(self) -> float:
        return self._time_remaining

    async def abort(self, code: Any, message: str) -> None:
        self.aborted_code = code
        self.aborted_message = message

    async def abort_with_status(self, status: Any) -> None:
        self.aborted_status = status


class _FakeCallDetails:
    def __init__(self, method: str = "/test.Service/Method") -> None:
        self.method = method


def _unary_handler_for(result: Any) -> Any:
    """Build a fake grpc.RpcMethodHandler with a unary_unary function."""
    h = MagicMock()
    h.unary_unary = AsyncMock(return_value=result)
    h.unary_stream = None
    h.stream_unary = None
    h.stream_stream = None
    h.request_deserializer = None
    h.response_serializer = None
    return h


def _server_stream_handler_for(items: list[Any]) -> Any:
    """Build a fake handler with a unary_stream (server-streaming) function."""

    async def _fn(request: Any, context: Any) -> Any:
        for item in items:
            yield item

    h = MagicMock()
    h.unary_unary = None
    h.unary_stream = _fn
    h.stream_unary = None
    h.stream_stream = None
    h.request_deserializer = None
    h.response_serializer = None
    return h


def _client_stream_handler_for(result: Any) -> Any:
    """Build a fake handler with a stream_unary (client-streaming) function."""

    async def _fn(request_iterator: Any, context: Any) -> Any:
        return result

    h = MagicMock()
    h.unary_unary = None
    h.unary_stream = None
    h.stream_unary = _fn
    h.stream_stream = None
    h.request_deserializer = None
    h.response_serializer = None
    return h


def _bidi_stream_handler_for(items: list[Any]) -> Any:
    """Build a fake handler with a stream_stream (bidi-streaming) function."""

    async def _fn(request_iterator: Any, context: Any) -> Any:
        async for _ in request_iterator:
            pass
        for item in items:
            yield item

    h = MagicMock()
    h.unary_unary = None
    h.unary_stream = None
    h.stream_unary = None
    h.stream_stream = _fn
    h.request_deserializer = None
    h.response_serializer = None
    return h


async def _continuation(handler: Any) -> Any:
    """Fake continuation: returns the handler unchanged."""
    return handler


async def _collect_stream(handler: Any, *args: Any) -> list[Any]:
    """Call a handler with streaming response and collect all yielded items."""
    fn = handler.unary_stream or handler.stream_stream
    return [item async for item in fn(*args)]


# ---------------------------------------------------------------------------
# RequestContextInterceptor
# ---------------------------------------------------------------------------


async def test_rc_interceptor_unary_opens_context() -> None:
    handler = _unary_handler_for("ok")
    details = _FakeCallDetails()
    context = _FakeContext()

    interceptor = RequestContextInterceptor()
    # First call discarded; we rebuild with a spy below
    await interceptor.intercept_service(lambda d: _continuation(handler), details)

    captured: list[Any] = []

    original_unary = handler.unary_unary

    async def _spying_unary(req: Any, ctx: Any) -> Any:
        captured.append(current_context())
        return await original_unary(req, ctx)

    handler.unary_unary = _spying_unary
    wrapped2 = await interceptor.intercept_service(
        lambda d: _continuation(handler),
        details,
    )
    await wrapped2.unary_unary("req", context)

    assert len(captured) == 1
    assert captured[0].attributes["rpc.method"] == details.method


async def test_rc_interceptor_sets_trailing_metadata() -> None:
    handler = _unary_handler_for("ok")
    details = _FakeCallDetails()
    context = _FakeContext()

    interceptor = RequestContextInterceptor()
    wrapped = await interceptor.intercept_service(
        lambda d: _continuation(handler),
        details,
    )
    await wrapped.unary_unary("req", context)

    assert context.trailing_metadata_set is not None
    keys = {k for k, _ in context.trailing_metadata_set}
    assert "qx-correlation-id" in keys
    assert "qx-request-id" in keys


async def test_rc_interceptor_propagates_metadata_ids() -> None:
    correlation_id = str(uuid4())
    request_id = str(uuid4())
    handler = _unary_handler_for("ok")
    details = _FakeCallDetails()
    context = _FakeContext(
        metadata=(
            ("qx-correlation-id", correlation_id),
            ("qx-request-id", request_id),
        ),
    )

    captured: list[Any] = []

    async def _capture(req: Any, ctx: Any) -> Any:
        captured.append(current_context())
        return "ok"

    handler.unary_unary = _capture
    interceptor = RequestContextInterceptor()
    wrapped = await interceptor.intercept_service(
        lambda d: _continuation(handler),
        details,
    )
    await wrapped.unary_unary("req", context)

    assert str(captured[0].correlation_id) == correlation_id
    assert str(captured[0].request_id) == request_id


async def test_rc_interceptor_stores_deadline_when_finite() -> None:
    handler = _unary_handler_for("ok")
    details = _FakeCallDetails()
    context = _FakeContext(time_remaining=5.0)

    captured: list[Any] = []

    async def _capture(req: Any, ctx: Any) -> Any:
        captured.append(current_context())
        return "ok"

    handler.unary_unary = _capture
    interceptor = RequestContextInterceptor()
    wrapped = await interceptor.intercept_service(
        lambda d: _continuation(handler),
        details,
    )
    await wrapped.unary_unary("req", context)

    assert captured[0].attributes.get("rpc.deadline_seconds") == pytest.approx(5.0)


async def test_rc_interceptor_no_deadline_when_infinite() -> None:
    handler = _unary_handler_for("ok")
    details = _FakeCallDetails()
    context = _FakeContext(time_remaining=1e10)

    captured: list[Any] = []

    async def _capture(req: Any, ctx: Any) -> Any:
        captured.append(current_context())
        return "ok"

    handler.unary_unary = _capture
    interceptor = RequestContextInterceptor()
    wrapped = await interceptor.intercept_service(
        lambda d: _continuation(handler),
        details,
    )
    await wrapped.unary_unary("req", context)

    assert "rpc.deadline_seconds" not in captured[0].attributes


async def test_rc_interceptor_server_streaming_opens_context() -> None:
    items_ctx: list[Any] = []

    async def _gen(req: Any, ctx: Any) -> Any:
        items_ctx.append(current_context())
        yield "a"
        items_ctx.append(current_context())
        yield "b"

    handler = MagicMock()
    handler.unary_unary = None
    handler.unary_stream = _gen
    handler.stream_unary = None
    handler.stream_stream = None
    handler.request_deserializer = None
    handler.response_serializer = None

    details = _FakeCallDetails()
    context = _FakeContext()

    interceptor = RequestContextInterceptor()
    wrapped = await interceptor.intercept_service(
        lambda d: _continuation(handler),
        details,
    )

    results = [item async for item in wrapped.unary_stream("req", context)]

    assert results == ["a", "b"]
    # Context was open throughout the stream
    assert all(c.attributes["rpc.method"] == details.method for c in items_ctx)


async def test_rc_interceptor_bidi_streaming() -> None:
    async def _echo(req_iter: Any, ctx: Any) -> Any:
        async for msg in req_iter:
            yield msg

    handler = MagicMock()
    handler.unary_unary = None
    handler.unary_stream = None
    handler.stream_unary = None
    handler.stream_stream = _echo
    handler.request_deserializer = None
    handler.response_serializer = None

    async def _request_iter() -> Any:
        for v in ["x", "y"]:
            yield v

    details = _FakeCallDetails()
    context = _FakeContext()
    interceptor = RequestContextInterceptor()
    wrapped = await interceptor.intercept_service(
        lambda d: _continuation(handler),
        details,
    )

    results = [item async for item in wrapped.stream_stream(_request_iter(), context)]
    assert results == ["x", "y"]


# ---------------------------------------------------------------------------
# ExceptionInterceptor
# ---------------------------------------------------------------------------


async def test_exc_interceptor_passes_through_on_success() -> None:
    handler = _unary_handler_for("value")
    details = _FakeCallDetails()
    context = _FakeContext()

    interceptor = ExceptionInterceptor()
    wrapped = await interceptor.intercept_service(
        lambda d: _continuation(handler),
        details,
    )
    result = await wrapped.unary_unary("req", context)

    assert result == "value"
    assert context.aborted_code is None


async def test_exc_interceptor_converts_error_exception() -> None:
    error = NotFoundError(code="x.not_found", message="gone")

    async def _raise(req: Any, ctx: Any) -> Any:
        raise error.as_exception()

    handler = _unary_handler_for(None)
    handler.unary_unary = _raise
    details = _FakeCallDetails()
    context = _FakeContext()

    interceptor = ExceptionInterceptor()
    wrapped = await interceptor.intercept_service(
        lambda d: _continuation(handler),
        details,
    )
    await wrapped.unary_unary("req", context)

    assert context.aborted_status is not None


async def test_exc_interceptor_unknown_for_bare_exception() -> None:
    async def _raise(req: Any, ctx: Any) -> Any:
        raise RuntimeError("oops")

    handler = _unary_handler_for(None)
    handler.unary_unary = _raise
    details = _FakeCallDetails()
    context = _FakeContext()

    interceptor = ExceptionInterceptor()
    wrapped = await interceptor.intercept_service(
        lambda d: _continuation(handler),
        details,
    )
    await wrapped.unary_unary("req", context)

    assert context.aborted_code == grpc.StatusCode.UNKNOWN


async def test_exc_interceptor_reraises_cancelled_error() -> None:
    async def _raise(req: Any, ctx: Any) -> Any:
        raise asyncio.CancelledError

    handler = _unary_handler_for(None)
    handler.unary_unary = _raise
    details = _FakeCallDetails()
    context = _FakeContext()

    interceptor = ExceptionInterceptor()
    wrapped = await interceptor.intercept_service(
        lambda d: _continuation(handler),
        details,
    )
    with pytest.raises(asyncio.CancelledError):
        await wrapped.unary_unary("req", context)


async def test_exc_interceptor_streaming_error_in_generator() -> None:
    async def _gen(req: Any, ctx: Any) -> Any:
        yield "first"
        raise NotFoundError(code="x.gone", message="gone").as_exception()

    handler = MagicMock()
    handler.unary_unary = None
    handler.unary_stream = _gen
    handler.stream_unary = None
    handler.stream_stream = None
    handler.request_deserializer = None
    handler.response_serializer = None

    details = _FakeCallDetails()
    context = _FakeContext()

    interceptor = ExceptionInterceptor()
    wrapped = await interceptor.intercept_service(
        lambda d: _continuation(handler),
        details,
    )

    results = [item async for item in wrapped.unary_stream("req", context)]

    assert results == ["first"]
    assert context.aborted_status is not None


# ---------------------------------------------------------------------------
# MetricsInterceptor
# ---------------------------------------------------------------------------


async def test_metrics_interceptor_records_on_success() -> None:
    handler = _unary_handler_for("ok")
    details = _FakeCallDetails()
    context = _FakeContext()

    total_labels: list[Any] = []
    duration_labels: list[Any] = []

    class _Counter:
        def labels(self, **kwargs: Any) -> Any:
            total_labels.append(kwargs)
            return MagicMock(inc=lambda: None)

    class _Histogram:
        def labels(self, **kwargs: Any) -> Any:
            duration_labels.append(kwargs)
            return MagicMock(observe=lambda d: None)

    m = MagicMock()
    m.http_request_total = _Counter()
    m.http_request_duration = _Histogram()

    interceptor = MetricsInterceptor(m)
    wrapped = await interceptor.intercept_service(
        lambda d: _continuation(handler),
        details,
    )
    await wrapped.unary_unary("req", context)

    assert len(total_labels) == 1
    assert total_labels[0]["status"] == "success"


async def test_metrics_interceptor_records_failure() -> None:
    async def _raise(req: Any, ctx: Any) -> Any:
        raise RuntimeError("boom")

    handler = _unary_handler_for(None)
    handler.unary_unary = _raise
    details = _FakeCallDetails()
    context = _FakeContext()

    recorded: list[str] = []

    class _Counter:
        def labels(self, **kwargs: Any) -> Any:
            recorded.append(kwargs["status"])
            return MagicMock(inc=lambda: None)

    m = MagicMock()
    m.http_request_total = _Counter()
    m.http_request_duration = MagicMock(labels=lambda **k: MagicMock(observe=lambda d: None))

    interceptor = MetricsInterceptor(m)
    wrapped = await interceptor.intercept_service(
        lambda d: _continuation(handler),
        details,
    )
    with pytest.raises(RuntimeError):
        await wrapped.unary_unary("req", context)

    assert recorded == ["failure"]


async def test_metrics_interceptor_per_method_buckets_uses_custom_histogram() -> None:
    """When per_method_buckets is configured for a method, a dedicated histogram
    is lazily created and used instead of the shared http_request_duration."""
    from prometheus_client import CollectorRegistry  # noqa: PLC0415
    from qx.observability import Metrics  # noqa: PLC0415

    registry = CollectorRegistry()
    metrics = Metrics(registry=registry)
    method = "/order.OrderService/PlaceOrder"
    buckets = (0.01, 0.05, 0.1, 0.5, 1.0, 5.0)

    interceptor = MetricsInterceptor(metrics, per_method_buckets={method: buckets})

    handler = _unary_handler_for("ok")
    details = _FakeCallDetails(method=method)
    context = _FakeContext()

    wrapped = await interceptor.intercept_service(
        lambda d: _continuation(handler),
        details,
    )
    await wrapped.unary_unary("req", context)

    # The per-method histogram should now be cached on the interceptor.
    hist = interceptor._latency_histogram(method)
    assert hist is not None


async def test_metrics_interceptor_per_method_buckets_falls_back_for_other_methods() -> None:
    """Methods not listed in per_method_buckets use the shared histogram."""
    from prometheus_client import CollectorRegistry  # noqa: PLC0415
    from qx.observability import Metrics  # noqa: PLC0415

    registry = CollectorRegistry()
    metrics = Metrics(registry=registry)
    special_method = "/order.OrderService/PlaceOrder"

    interceptor = MetricsInterceptor(metrics, per_method_buckets={special_method: (0.1, 1.0)})

    # For a different method, no per-method histogram should be created.
    assert interceptor._latency_histogram("/other.Service/OtherMethod") is None


async def test_metrics_interceptor_per_method_histogram_cached() -> None:
    """_latency_histogram returns the same object on repeated calls."""
    from prometheus_client import CollectorRegistry  # noqa: PLC0415
    from qx.observability import Metrics  # noqa: PLC0415

    registry = CollectorRegistry()
    metrics = Metrics(registry=registry)
    method = "/svc.Svc/Method"

    interceptor = MetricsInterceptor(metrics, per_method_buckets={method: (0.1,)})

    handler = _unary_handler_for("ok")
    details = _FakeCallDetails(method=method)
    context = _FakeContext()

    wrapped = await interceptor.intercept_service(
        lambda d: _continuation(handler),
        details,
    )
    await wrapped.unary_unary("req", context)

    h1 = interceptor._latency_histogram(method)
    h2 = interceptor._latency_histogram(method)
    assert h1 is h2


async def test_metrics_interceptor_streaming_records_success() -> None:
    handler = _server_stream_handler_for(["a", "b", "c"])
    details = _FakeCallDetails()
    context = _FakeContext()

    recorded: list[str] = []

    class _Counter:
        def labels(self, **kwargs: Any) -> Any:
            recorded.append(kwargs["status"])
            return MagicMock(inc=lambda: None)

    m = MagicMock()
    m.http_request_total = _Counter()
    m.http_request_duration = MagicMock(labels=lambda **k: MagicMock(observe=lambda d: None))

    interceptor = MetricsInterceptor(m)
    wrapped = await interceptor.intercept_service(
        lambda d: _continuation(handler),
        details,
    )
    results = [item async for item in wrapped.unary_stream("req", context)]

    assert results == ["a", "b", "c"]
    assert recorded == ["success"]


# ---------------------------------------------------------------------------
# JwtAuthInterceptor
# ---------------------------------------------------------------------------


class _StubResult:
    def __init__(self, value: Any, *, ok: bool) -> None:
        self.value = value
        self.is_success = ok
        self.is_failure = not ok


class _StubValidator:
    def __init__(self, principal: Any) -> None:
        self._principal = principal

    async def validate(self, token: str) -> _StubResult:
        if token == "valid":
            return _StubResult(self._principal, ok=True)
        return _StubResult(None, ok=False)


async def test_jwt_interceptor_stores_principal_on_valid_token() -> None:
    principal = MagicMock()
    principal.subject = uuid4()
    validator = _StubValidator(principal)

    captured: list[Any] = []

    async def _handler(req: Any, ctx: Any) -> Any:
        captured.append(current_context().attributes.get("qx.principal"))
        return "ok"

    handler = _unary_handler_for(None)
    handler.unary_unary = _handler
    details = _FakeCallDetails()
    context = _FakeContext(metadata=(("authorization", "Bearer valid"),))

    # JwtAuthInterceptor must run inside a RequestContext (RC is outer)
    ctx = RequestContext(attributes={})
    interceptor = JwtAuthInterceptor(validator)
    wrapped = await interceptor.intercept_service(
        lambda d: _continuation(handler),
        details,
    )
    with request_scope(ctx):
        await wrapped.unary_unary("req", context)

    assert captured[0] is principal


async def test_jwt_interceptor_no_principal_on_missing_token() -> None:
    principal = MagicMock()
    validator = _StubValidator(principal)

    captured: list[Any] = []

    async def _handler(req: Any, ctx: Any) -> Any:
        captured.append(current_context().attributes.get("qx.principal"))
        return "ok"

    handler = _unary_handler_for(None)
    handler.unary_unary = _handler
    details = _FakeCallDetails()
    context = _FakeContext()  # no authorization header

    ctx = RequestContext(attributes={})
    interceptor = JwtAuthInterceptor(validator)
    wrapped = await interceptor.intercept_service(
        lambda d: _continuation(handler),
        details,
    )
    with request_scope(ctx):
        await wrapped.unary_unary("req", context)

    assert captured[0] is None


async def test_jwt_interceptor_no_principal_on_invalid_token() -> None:
    principal = MagicMock()
    validator = _StubValidator(principal)

    captured: list[Any] = []

    async def _handler(req: Any, ctx: Any) -> Any:
        captured.append(current_context().attributes.get("qx.principal"))
        return "ok"

    handler = _unary_handler_for(None)
    handler.unary_unary = _handler
    details = _FakeCallDetails()
    context = _FakeContext(metadata=(("authorization", "Bearer invalid-token"),))

    ctx = RequestContext(attributes={})
    interceptor = JwtAuthInterceptor(validator)
    wrapped = await interceptor.intercept_service(
        lambda d: _continuation(handler),
        details,
    )
    with request_scope(ctx):
        await wrapped.unary_unary("req", context)

    assert captured[0] is None


async def test_jwt_interceptor_streaming_stores_principal() -> None:
    principal = MagicMock()
    validator = _StubValidator(principal)

    captured: list[Any] = []

    async def _gen(req: Any, ctx: Any) -> Any:
        captured.append(current_context().attributes.get("qx.principal"))
        yield "item"

    handler = MagicMock()
    handler.unary_unary = None
    handler.unary_stream = _gen
    handler.stream_unary = None
    handler.stream_stream = None
    handler.request_deserializer = None
    handler.response_serializer = None

    details = _FakeCallDetails()
    context = _FakeContext(metadata=(("authorization", "Bearer valid"),))

    ctx = RequestContext(attributes={})
    interceptor = JwtAuthInterceptor(validator)
    wrapped = await interceptor.intercept_service(
        lambda d: _continuation(handler),
        details,
    )
    with request_scope(ctx):
        results = [item async for item in wrapped.unary_stream("req", context)]

    assert results == ["item"]
    assert captured[0] is principal
