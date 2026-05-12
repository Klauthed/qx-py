"""Request-scoped context propagated via :mod:`contextvars`.

Every request, message consumption, or scheduled job runs inside a
``RequestContext`` that carries the standard correlation, tracing, and tenancy
fields. The context is set at the edge (HTTP middleware, NATS subscriber
wrapper, CLI command entrypoint) and read everywhere downstream — logs,
spans, repositories, domain events — without threading it through every
function signature.

Implementation choice: ``contextvars.ContextVar`` rather than a global. This
makes the context **task-local** under asyncio, so concurrent requests in the
same process don't see each other's data. This is non-negotiable for
correctness.

Read pattern::

    from qx.core.context import current_context
    ctx = current_context()
    log.info("processing", correlation_id=ctx.correlation_id)

Write pattern (edge code only)::

    with request_scope(correlation_id=cid, user_id=uid):
        await handler.run()
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import UUID, uuid4

__all__ = [
    "RequestContext",
    "current_context",
    "request_scope",
    "set_context",
    "reset_context",
]


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Immutable per-request context.

    All fields are optional except ``correlation_id`` and ``request_id``, which
    we always synthesize at edges if the caller didn't provide one. Making this
    frozen forces deliberate mutation via ``with_changes`` — accidental updates
    inside a handler can't silently leak to siblings.
    """

    request_id: UUID = field(default_factory=uuid4)
    correlation_id: UUID = field(default_factory=uuid4)
    trace_id: str | None = None
    span_id: str | None = None

    user_id: UUID | None = None
    tenant_id: UUID | None = None
    actor_kind: str | None = None  # "user" | "service" | "system"

    # Free-form bag for adapter-specific data (ip address, user-agent, request path).
    # Don't put large objects here; this gets copied on every context inheritance.
    attributes: dict[str, Any] = field(default_factory=dict)

    def with_changes(self, **changes: Any) -> "RequestContext":
        return replace(self, **changes)

    def child(self, **overrides: Any) -> "RequestContext":
        """Derive a child context (e.g., when fanning out to background work).

        ``correlation_id`` and ``trace_id`` carry over; ``request_id`` is fresh.
        """
        defaults: dict[str, Any] = {
            "request_id": uuid4(),
            "correlation_id": self.correlation_id,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "actor_kind": self.actor_kind,
            "attributes": dict(self.attributes),
        }
        defaults.update(overrides)
        return RequestContext(**defaults)


_EMPTY = RequestContext()
_var: ContextVar[RequestContext] = ContextVar("qx_request_context", default=_EMPTY)


def current_context() -> RequestContext:
    """Read the current context. Returns an empty default outside a scope.

    Returning a default rather than raising is intentional: utility code (logging,
    health checks) should not crash because no scope was established. Edge code
    that *requires* a context should check ``ctx is not EMPTY`` explicitly.
    """
    return _var.get()


def set_context(ctx: RequestContext) -> Token[RequestContext]:
    """Imperative setter. Prefer ``request_scope`` for structured use."""
    return _var.set(ctx)


def reset_context(token: Token[RequestContext]) -> None:
    _var.reset(token)


@contextmanager
def request_scope(
    ctx: RequestContext | None = None,
    /,
    **overrides: Any,
) -> Iterator[RequestContext]:
    """Establish a request-scoped context.

    Either pass a fully-built ``ctx``, or pass kwargs to derive a fresh context
    from the current one (which may be the empty default).
    """
    if ctx is None:
        base = current_context()
        ctx = base.with_changes(**overrides) if overrides else base.child()
    token = _var.set(ctx)
    try:
        yield ctx
    finally:
        _var.reset(token)
