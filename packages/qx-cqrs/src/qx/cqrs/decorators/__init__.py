"""Decorators for handler registration.

These stamp metadata on the class. Actual registration happens when you call
``mediator.register_decorated(module_or_class)`` — keeping the decorator pure
and avoiding import-time side effects (which complicate testability).

Lifetime defaults to TRANSIENT for handlers because most are stateless and a
fresh instance per dispatch is cheap. Override with ``lifetime=`` if your
handler holds expensive caches that you want shared.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, TypeVar

from qx.core import DomainEvent, IntegrationEvent, Notification
from qx.di import Lifetime, injectable

from qx.cqrs.messages import Command, Query

__all__ = [
    "command_handler",
    "query_handler",
    "event_handler",
    "integration_event_handler",
    "notification_handler",
    "requires",
    "HandlerKind",
    "HANDLER_MARKER",
    "POLICY_MARKER",
]

HandlerKind = Literal["command", "query", "event", "integration", "notification"]

HANDLER_MARKER = "__qx_handler__"
POLICY_MARKER = "__qx_policies__"


@dataclass(frozen=True)
class _HandlerMeta:
    kind: HandlerKind
    target: type


_T = TypeVar("_T", bound=type)


def _stamp(target: type, kind: HandlerKind, lifetime: Lifetime) -> Callable[[_T], _T]:
    def wrap(cls: _T) -> _T:
        setattr(cls, HANDLER_MARKER, _HandlerMeta(kind=kind, target=target))
        # Also stamp DI metadata so the same class gets DI-registered when scanned.
        return injectable(lifetime=lifetime)(cls)

    return wrap


def command_handler(
    command_type: type[Command],
    *,
    lifetime: Lifetime = Lifetime.TRANSIENT,
) -> Callable[[_T], _T]:
    """Register the decorated class as a command handler for ``command_type``."""
    return _stamp(command_type, "command", lifetime)


def query_handler(
    query_type: type[Query],
    *,
    lifetime: Lifetime = Lifetime.TRANSIENT,
) -> Callable[[_T], _T]:
    return _stamp(query_type, "query", lifetime)


def event_handler(
    event_type: type[DomainEvent],
    *,
    lifetime: Lifetime = Lifetime.TRANSIENT,
) -> Callable[[_T], _T]:
    return _stamp(event_type, "event", lifetime)


def integration_event_handler(
    event_type: type[IntegrationEvent],
    *,
    lifetime: Lifetime = Lifetime.TRANSIENT,
) -> Callable[[_T], _T]:
    return _stamp(event_type, "integration", lifetime)


def notification_handler(
    notification_type: type[Notification],
    *,
    lifetime: Lifetime = Lifetime.TRANSIENT,
) -> Callable[[_T], _T]:
    return _stamp(notification_type, "notification", lifetime)


def requires(*policies: Any) -> Callable[[_T], _T]:
    """Attach authorization policies to a Command or Query class.

    ``AuthorizationBehavior`` reads these at dispatch time and evaluates them
    against the current principal before invoking the handler.

    Example::

        @requires(require_permission("user.write"))
        class DeleteUserCommand(Command[None]):
            user_id: UUID
    """

    def wrap(cls: _T) -> _T:
        existing: list[Any] = list(getattr(cls, POLICY_MARKER, []))
        setattr(cls, POLICY_MARKER, existing + list(policies))
        return cls

    return wrap
