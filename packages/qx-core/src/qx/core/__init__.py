"""Public surface of ``qx-core``.

Import from this module rather than from internal submodules; the submodule
layout is subject to change while this surface is stable.
"""

from __future__ import annotations

from qx.core.config import (
    AppSection,
    Environment,
    QxSettings,
    LoggingSection,
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

__version__ = "0.1.0"

__all__ = [
    # Result
    "Result",
    "Success",
    "Failure",
    # Errors
    "Error",
    "ErrorException",
    "ValidationError",
    "DomainError",
    "UnauthorizedError",
    "ForbiddenError",
    "NotFoundError",
    "ConflictError",
    "PreconditionFailedError",
    "RateLimitedError",
    "InfrastructureError",
    "TimeoutError",
    "ConfigurationError",
    # Entities
    "Identifier",
    "Entity",
    "AggregateRoot",
    "ValueObject",
    "entity",
    "aggregate",
    # Domain events
    "Event",
    "DomainEvent",
    "IntegrationEvent",
    "Notification",
    # Audit
    "AuditAction",
    "AuditEntry",
    # Context
    "RequestContext",
    "current_context",
    "request_scope",
    "set_context",
    "reset_context",
    # Pagination/filter
    "OffsetPagination",
    "OffsetPage",
    "CursorPagination",
    "CursorPage",
    "Page",
    "Sort",
    "SortDirection",
    "Filter",
    "FilterOp",
    # Config
    "QxSettings",
    "AppSection",
    "LoggingSection",
    "Environment",
    # Utils
    "utcnow",
    "__version__",
]
