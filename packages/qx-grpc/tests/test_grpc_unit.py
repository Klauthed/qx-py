"""gRPC unit tests — error mapping + smoke."""

from __future__ import annotations

from google.rpc import code_pb2  # type: ignore[import-not-found]
from qx.core import NotFoundError, RateLimitedError
from qx.grpc.errors import status_from_error


def test_not_found_maps_to_grpc_not_found() -> None:
    err = NotFoundError(code="x.not_found", message="missing")
    status = status_from_error(err)
    assert status.code == code_pb2.NOT_FOUND


def test_rate_limited_maps_to_resource_exhausted() -> None:
    err = RateLimitedError(code="rl", message="slow down")
    status = status_from_error(err)
    assert status.code == code_pb2.RESOURCE_EXHAUSTED


def test_status_includes_error_info_detail() -> None:
    err = NotFoundError(code="user.not_found", message="x", details={"id": "42"})
    status = status_from_error(err)
    assert len(status.details) == 1
