"""Canonical error hierarchy for the Qx framework.

Every business failure produced by a handler MUST be one of these types. The
HTTP/gRPC layers map each to a transport status code; observability hooks log
the structured fields. Adding a new error category is intentional: it requires
a transport-layer mapping update.

Errors are **values**, not exceptions. They are constructed and returned via
``Result.failure``. They expose ``.as_exception()`` for the rare cases where
crossing back into exception-land is required (CLI top-level, test asserts).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

__all__ = [
    "ConfigurationError",
    "ConflictError",
    "DomainError",
    "Error",
    "ErrorException",
    "ForbiddenError",
    "InfrastructureError",
    "NotFoundError",
    "PreconditionFailedError",
    "RateLimitedError",
    "TimeoutError",
    "UnauthorizedError",
    "ValidationError",
]


class ErrorException(Exception):
    """The exception form of an ``Error``. Raised by ``Error.as_exception()``."""

    def __init__(self, error: Error) -> None:
        self.error = error
        super().__init__(f"[{error.code}] {error.message}")


@dataclass(frozen=True, slots=True)
class Error:
    """Base structured error.

    Attributes:
        code: Stable, namespaced identifier (e.g., ``"user.email_taken"``). Suitable
            for clients to switch on. Never include a human-readable message here.
        message: Human-readable description. Localizable.
        details: Structured payload (field violations, conflicting ids, etc.). Must
            be JSON-serializable. The framework will not redact these — never put
            secrets in here.
        cause: Optional underlying exception, for chained failures (DB error inside
            a repository, network error inside an adapter). Stripped from API
            responses; preserved for tracing/logging.

    The class-level ``http_status`` and ``grpc_code`` constants define default
    transport mappings; specific subclasses override them. Custom errors that
    inherit from ``Error`` directly get treated as 500.
    """

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    cause: BaseException | None = None

    # Default transport mappings. Subclasses override.
    http_status: ClassVar[int] = 500
    grpc_code: ClassVar[str] = "INTERNAL"
    # Category drives observability bucketing and decides whether the failure
    # increments error-rate SLI counters or business-event counters.
    category: ClassVar[str] = "infrastructure"

    def as_exception(self) -> ErrorException:
        exc = ErrorException(self)
        if self.cause is not None:
            exc.__cause__ = self.cause
        return exc

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API responses. Excludes ``cause``."""
        return {
            "code": self.code,
            "message": self.message,
            "details": dict(self.details),
        }


# -------- Client-attributable failures (4xx) --------


@dataclass(frozen=True, slots=True)
class ValidationError(Error):
    """Input failed schema or business-rule validation. 400.

    ``details`` should follow the convention::

        {"fields": [{"path": "email", "code": "format.email", "message": "..."}]}

    so that the HTTP layer can produce a stable problem+json payload.
    """

    http_status: ClassVar[int] = 400
    grpc_code: ClassVar[str] = "INVALID_ARGUMENT"
    category: ClassVar[str] = "client"


@dataclass(frozen=True, slots=True)
class UnauthorizedError(Error):
    """No or invalid authentication. 401."""

    http_status: ClassVar[int] = 401
    grpc_code: ClassVar[str] = "UNAUTHENTICATED"
    category: ClassVar[str] = "client"


@dataclass(frozen=True, slots=True)
class ForbiddenError(Error):
    """Authenticated but lacks authorization. 403."""

    http_status: ClassVar[int] = 403
    grpc_code: ClassVar[str] = "PERMISSION_DENIED"
    category: ClassVar[str] = "client"


@dataclass(frozen=True, slots=True)
class NotFoundError(Error):
    """Requested resource does not exist. 404."""

    http_status: ClassVar[int] = 404
    grpc_code: ClassVar[str] = "NOT_FOUND"
    category: ClassVar[str] = "client"


@dataclass(frozen=True, slots=True)
class ConflictError(Error):
    """Resource conflict: duplicate, version mismatch, etc. 409."""

    http_status: ClassVar[int] = 409
    grpc_code: ClassVar[str] = "ALREADY_EXISTS"
    category: ClassVar[str] = "client"


@dataclass(frozen=True, slots=True)
class PreconditionFailedError(Error):
    """``If-Match`` or business precondition failed. 412.

    Distinct from ``ConflictError`` in that the caller can fix it by retrying
    with up-to-date version/ETag information.
    """

    http_status: ClassVar[int] = 412
    grpc_code: ClassVar[str] = "FAILED_PRECONDITION"
    category: ClassVar[str] = "client"


@dataclass(frozen=True, slots=True)
class RateLimitedError(Error):
    """Throttled. 429. ``details`` may contain ``retry_after_seconds``."""

    http_status: ClassVar[int] = 429
    grpc_code: ClassVar[str] = "RESOURCE_EXHAUSTED"
    category: ClassVar[str] = "client"


@dataclass(frozen=True, slots=True)
class DomainError(Error):
    """Business rule violation that is not a simple input failure. 422.

    Examples: "cannot transition order from delivered to cancelled",
    "balance would go negative". These are domain invariants, not input shape
    problems — that distinction matters for API documentation and client UX.
    """

    http_status: ClassVar[int] = 422
    grpc_code: ClassVar[str] = "FAILED_PRECONDITION"
    category: ClassVar[str] = "domain"


# -------- Server-attributable failures (5xx) --------


@dataclass(frozen=True, slots=True)
class InfrastructureError(Error):
    """A downstream system failed (DB, cache, broker, third party). 500."""

    http_status: ClassVar[int] = 500
    grpc_code: ClassVar[str] = "INTERNAL"
    category: ClassVar[str] = "infrastructure"


@dataclass(frozen=True, slots=True)
class TimeoutError(Error):
    """A bounded operation exceeded its deadline. 504."""

    http_status: ClassVar[int] = 504
    grpc_code: ClassVar[str] = "DEADLINE_EXCEEDED"
    category: ClassVar[str] = "infrastructure"


@dataclass(frozen=True, slots=True)
class ConfigurationError(Error):
    """Misconfiguration discovered at runtime. 500.

    Should be rare — most misconfig should be caught at startup. Distinct from
    ``InfrastructureError`` so dashboards can separate "the world broke" from
    "we deployed wrong".
    """

    http_status: ClassVar[int] = 500
    grpc_code: ClassVar[str] = "FAILED_PRECONDITION"
    category: ClassVar[str] = "configuration"
