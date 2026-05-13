"""Public surface of ``qx-core``.

Import from this module rather than from internal submodules; the submodule
layout is subject to change while this surface is stable.
"""

from __future__ import annotations

from qx.core.config import (
    AppSection,
    Environment,
    LoggingSection,
    QxSettings,
)
from qx.core.context import (
    RequestContext,
    current_context,
    request_scope,
    reset_context,
    set_context,
)
from qx.core.domain.audit import AuditAction, AuditEntry
from qx.core.domain.events import (
    DomainEvent,
    Event,
    IntegrationEvent,
    Notification,
)
from qx.core.entities import (
    AggregateRoot,
    Entity,
    Identifier,
    ValueObject,
    aggregate,
    entity,
    uuid4,
    uuid7,
)
from qx.core.errors import (
    ConfigurationError,
    ConflictError,
    DomainError,
    Error,
    ErrorException,
    ForbiddenError,
    InfrastructureError,
    NotFoundError,
    PreconditionFailedError,
    RateLimitedError,
    TimeoutError,
    UnauthorizedError,
    ValidationError,
)
from qx.core.result import Failure, Result, Success
from qx.core.types.pagination import (
    CursorPage,
    CursorPagination,
    Filter,
    FilterOp,
    OffsetPage,
    OffsetPagination,
    Page,
    Sort,
    SortDirection,
)
from qx.core.utils.time import utcnow

__version__ = "0.2.0"

__all__ = [
    "AggregateRoot",
    "AppSection",
    # Audit
    "AuditAction",
    "AuditEntry",
    "ConfigurationError",
    "ConflictError",
    "CursorPage",
    "CursorPagination",
    "DomainError",
    "DomainEvent",
    "Entity",
    "Environment",
    # Errors
    "Error",
    "ErrorException",
    # Domain events
    "Event",
    "Failure",
    "Filter",
    "FilterOp",
    "ForbiddenError",
    # Entities
    "Identifier",
    "InfrastructureError",
    "IntegrationEvent",
    "LoggingSection",
    "NotFoundError",
    "Notification",
    "OffsetPage",
    # Pagination/filter
    "OffsetPagination",
    "Page",
    "PreconditionFailedError",
    # Config
    "QxSettings",
    "RateLimitedError",
    # Context
    "RequestContext",
    # Result
    "Result",
    "Sort",
    "SortDirection",
    "Success",
    "TimeoutError",
    "UnauthorizedError",
    "ValidationError",
    "ValueObject",
    "__version__",
    "aggregate",
    "current_context",
    "entity",
    "request_scope",
    "reset_context",
    "set_context",
    # Utils
    "utcnow",
    "uuid4",
    "uuid7",
]
