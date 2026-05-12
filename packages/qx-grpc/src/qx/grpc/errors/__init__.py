"""Map Qx ``Error`` instances to gRPC status codes.

The framework's ``Error`` types carry both ``http_status`` and ``grpc_code``
class variables. This module is the gRPC counterpart of
``qx-http.exceptions``.

The gRPC ``status.proto`` (``google.rpc.Status``) carries:

- code (the StatusCode integer)
- message (human-readable)
- details: an Any-list, where we encode the error code + details map as a
  ``google.rpc.ErrorInfo`` so clients can pattern-match on Qx codes.

Wire compatibility note: clients in any language can read ``ErrorInfo`` and
treat the ``reason`` field as the framework error code, matching what the
HTTP envelope's ``error.code`` field carries. Consistent across transports.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from google.protobuf import any_pb2
from google.rpc import code_pb2, error_details_pb2, status_pb2
from grpc_status import rpc_status

if TYPE_CHECKING:
    from qx.core import Error

    import grpc

__all__ = ["abort_with_error", "status_from_error"]


def status_from_error(error: Error) -> status_pb2.Status:
    """Build a rich Status from a Qx Error.

    Includes an ``ErrorInfo`` detail so clients get the framework error code,
    domain ("qx"), and the error's ``details`` map.
    """
    info = error_details_pb2.ErrorInfo(
        reason=error.code,
        domain="qx",
        metadata={k: str(v) for k, v in error.details.items()},
    )
    detail_any = any_pb2.Any()
    detail_any.Pack(info)
    # Error.grpc_code is the StatusCode *name* ("NOT_FOUND"); convert to int.
    grpc_code_int = _GRPC_CODE_BY_NAME.get(error.grpc_code, code_pb2.UNKNOWN)
    return status_pb2.Status(
        code=grpc_code_int,
        message=error.message,
        details=[detail_any],
    )


_GRPC_CODE_BY_NAME: dict[str, int] = {
    name: getattr(code_pb2, name)
    for name in (
        "OK",
        "CANCELLED",
        "UNKNOWN",
        "INVALID_ARGUMENT",
        "DEADLINE_EXCEEDED",
        "NOT_FOUND",
        "ALREADY_EXISTS",
        "PERMISSION_DENIED",
        "RESOURCE_EXHAUSTED",
        "FAILED_PRECONDITION",
        "ABORTED",
        "OUT_OF_RANGE",
        "UNIMPLEMENTED",
        "INTERNAL",
        "UNAVAILABLE",
        "DATA_LOSS",
        "UNAUTHENTICATED",
    )
    if hasattr(code_pb2, name)
}


def abort_with_error(context: grpc.aio.ServicerContext, error: Error) -> None:
    """Synchronously abort a gRPC call with the framework-standard status.

    The aio servicer expects ``await context.abort_with_status(...)`` for full
    detail support. ``ServicerContext.abort`` would only carry code+message.
    """
    raise rpc_status.to_status(
        status_from_error(error)
    )  # synchronous: turns into trailing metadata
