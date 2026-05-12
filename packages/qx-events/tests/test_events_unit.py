"""Unit tests for events package — registry + dispatcher bridge."""

from __future__ import annotations

from typing import ClassVar

import pytest
from qx.core import DomainEvent, IntegrationEvent
from qx.events import EventRegistry, EventTypeNotRegistered, MediatorEventDispatcher


class UserRegistered(IntegrationEvent):
    event_name: ClassVar[str] = "identity.user.registered"
    event_version: ClassVar[int] = 1
    email: str


class OrderPlaced(IntegrationEvent):
    event_name: ClassVar[str] = "commerce.order.placed"
    event_version: ClassVar[int] = 2
    total_cents: int


def test_registry_round_trip() -> None:
    r = EventRegistry()
    r.register(UserRegistered)
    r.register(OrderPlaced)
    assert r.lookup("identity.user.registered", 1) is UserRegistered
    assert r.lookup("commerce.order.placed", 2) is OrderPlaced


def test_unknown_event_raises() -> None:
    r = EventRegistry()
    with pytest.raises(EventTypeNotRegistered):
        r.lookup("missing", 1)


def test_duplicate_registration_with_same_class_is_noop() -> None:
    r = EventRegistry()
    r.register(UserRegistered)
    r.register(UserRegistered)  # idempotent


def test_duplicate_registration_with_different_class_raises() -> None:
    class OtherUserRegistered(IntegrationEvent):
        event_name: ClassVar[str] = "identity.user.registered"
        email: str

    r = EventRegistry()
    r.register(UserRegistered)
    with pytest.raises(ValueError, match="already registered"):
        r.register(OtherUserRegistered)


async def test_mediator_dispatcher_bridge_calls_publish() -> None:
    class FakeMediator:
        def __init__(self) -> None:
            self.published: list[DomainEvent] = []

        async def publish(self, ev: DomainEvent) -> None:
            self.published.append(ev)

    class Evt(DomainEvent):
        event_name: ClassVar[str] = "x.something"
        payload: str

    m = FakeMediator()
    d = MediatorEventDispatcher(m)  # type: ignore[arg-type]
    await d.publish(Evt(payload="hi"))
    assert len(m.published) == 1
