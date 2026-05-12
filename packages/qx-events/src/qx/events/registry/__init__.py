"""Event type registry.

Maps ``event_name`` strings to the concrete ``IntegrationEvent`` subclass so
the consumer side can deserialize incoming messages. Without this, the
worker would receive an opaque dict and the application would have to do its
own dispatch.

Services register their event types once at bootstrap::

    registry = EventRegistry()
    registry.register(UserRegistered)
    registry.register(OrderPlaced)

    container.register_instance(EventRegistry, registry)

When the worker pulls a NATS message, the registry resolves the class:

    cls = registry.lookup("user.registered")
    event = cls.model_validate(payload)
"""

from __future__ import annotations

from qx.core import IntegrationEvent

__all__ = ["EventRegistry", "EventTypeNotRegistered"]


class EventTypeNotRegistered(Exception):
    """Raised when an incoming event_name has no matching class."""


class EventRegistry:
    """Mapping from ``event_name`` to ``IntegrationEvent`` subclass."""

    def __init__(self) -> None:
        self._by_name: dict[tuple[str, int], type[IntegrationEvent]] = {}

    def register(self, event_cls: type[IntegrationEvent]) -> None:
        if not event_cls.event_name:
            raise ValueError(
                f"{event_cls.__name__} has no event_name; cannot register"
            )
        key = (event_cls.event_name, event_cls.event_version)
        existing = self._by_name.get(key)
        if existing is not None and existing is not event_cls:
            raise ValueError(
                f"event_name {event_cls.event_name!r} v{event_cls.event_version} "
                f"already registered to {existing.__name__}"
            )
        self._by_name[key] = event_cls

    def lookup(self, event_name: str, version: int = 1) -> type[IntegrationEvent]:
        try:
            return self._by_name[(event_name, version)]
        except KeyError as e:
            raise EventTypeNotRegistered(
                f"No event registered for {event_name!r} v{version}. "
                f"Did you call EventRegistry.register({event_name})?"
            ) from e

    def __len__(self) -> int:
        return len(self._by_name)

    def known_event_names(self) -> list[str]:
        return sorted({name for name, _ in self._by_name})
