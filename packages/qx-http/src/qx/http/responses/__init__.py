"""Standard API response envelope.

Every Qx HTTP endpoint returns this shape::

    {
      "success": true|false,
      "data": <payload> | null,
      "error": { "code": "...", "message": "...", "details": {...} } | null,
      "metadata": { "correlation_id": "...", "request_id": "...", ... }
    }

Why an envelope? Three reasons:

1. **Clients always know where to look** — instead of "is this success or
   failure?" depending on HTTP status (which gateways sometimes mangle), the
   payload has a definitive answer.
2. **Pagination and warnings ride in metadata** without polluting the data
   shape — important when client SDKs auto-generate types.
3. **Consistent error format across all services** — front-end can write one
   error renderer.

Status codes still carry meaning. Success envelopes use 200/201/204 normally;
failure envelopes use the error's ``http_status`` (so 4xx vs 5xx semantics
work for caching, retries, alerting).
"""

from __future__ import annotations

from typing import Any, ClassVar, TypeVar
from uuid import UUID  # noqa: TC003

from pydantic import BaseModel, ConfigDict, Field
from qx.core import current_context

__all__ = ["ApiError", "ApiMetadata", "ApiResponse", "envelope_failure", "envelope_success"]

T = TypeVar("T")


class ApiError(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ApiMetadata(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    request_id: UUID
    correlation_id: UUID
    trace_id: str | None = None
    # Pagination metadata may be injected by endpoints returning paged data.
    page: int | None = None
    page_size: int | None = None
    total: int | None = None
    next_cursor: str | None = None


class ApiResponse[T](BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(frozen=True)

    success: bool
    data: T | None = None
    error: ApiError | None = None
    metadata: ApiMetadata


def _meta(**overrides: Any) -> ApiMetadata:
    ctx = current_context()
    return ApiMetadata(
        request_id=ctx.request_id,
        correlation_id=ctx.correlation_id,
        trace_id=ctx.trace_id,
        **overrides,
    )


def envelope_success(data: Any, **metadata_overrides: Any) -> dict[str, Any]:
    return ApiResponse[Any](
        success=True,
        data=data,
        metadata=_meta(**metadata_overrides),
    ).model_dump(mode="json")


def envelope_failure(
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    **metadata_overrides: Any,
) -> dict[str, Any]:
    return ApiResponse[Any](
        success=False,
        error=ApiError(code=code, message=message, details=details or {}),
        metadata=_meta(**metadata_overrides),
    ).model_dump(mode="json")
