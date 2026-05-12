"""gRPC server factory.

Builds an async gRPC server preconfigured with framework interceptors. Service
implementations are added by callers via the normal ``add_*_to_server`` stubs::

    server = create_grpc_server(container, metrics=metrics)
    identity_pb2_grpc.add_IdentityServiceServicer_to_server(IdentityImpl(...), server)
    server.add_insecure_port("[::]:50051")
    await server.start()
    await server.wait_for_termination()
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from qx.grpc.interceptors import (
    ExceptionInterceptor,
    MetricsInterceptor,
    RequestContextInterceptor,
)

import grpc

if TYPE_CHECKING:
    from qx.di import Container

__all__ = ["create_grpc_server"]


def create_grpc_server(
    container: Container,
    *,
    metrics: Any,
    max_concurrent_rpcs: int | None = None,
    extra_interceptors: tuple[grpc.aio.ServerInterceptor, ...] = (),
) -> grpc.aio.Server:
    """Build an aio gRPC server with framework interceptors installed.

    Interceptor order (outermost first): RequestContext → Metrics → Exception
    → user-supplied. RequestContext wraps everything; Exception is innermost
    so it sees handler errors but its translations are still observable by
    Metrics.
    """
    interceptors = (
        RequestContextInterceptor(),
        MetricsInterceptor(metrics),
        ExceptionInterceptor(),
        *extra_interceptors,
    )
    return grpc.aio.server(
        interceptors=list(interceptors),
        maximum_concurrent_rpcs=max_concurrent_rpcs,
    )
