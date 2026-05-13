"""Unit tests for events package — registry + dispatcher bridge."""

from __future__ import annotations

from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock

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


# ---------------------------------------------------------------------------
# NatsConsumer.num_pending
# ---------------------------------------------------------------------------


async def test_num_pending_returns_none_when_js_not_initialised() -> None:
    """Before any subscription, num_pending returns None (no JetStream context)."""
    from qx.events.nats import NatsConsumer  # noqa: PLC0415

    nc = MagicMock()
    registry = EventRegistry()
    consumer = NatsConsumer(
        nc,
        registry,
        stream="test-stream",
        durable_name="test-consumer",
        subject_filter="test.>",
    )
    result = await consumer.num_pending()
    assert result is None


async def test_num_pending_returns_value_from_consumer_info() -> None:
    """After JetStream is initialised, num_pending returns the server count."""
    from qx.events.nats import NatsConsumer  # noqa: PLC0415

    nc = MagicMock()
    registry = EventRegistry()
    consumer = NatsConsumer(
        nc,
        registry,
        stream="test-stream",
        durable_name="test-consumer",
        subject_filter="test.>",
    )

    fake_info = MagicMock()
    fake_info.num_pending = 42

    fake_js = MagicMock()
    fake_js.consumer_info = AsyncMock(return_value=fake_info)
    consumer._js = fake_js

    result = await consumer.num_pending()
    assert result == 42
    fake_js.consumer_info.assert_awaited_once_with("test-stream", "test-consumer")


async def test_num_pending_returns_none_on_server_error() -> None:
    """If consumer_info raises, num_pending swallows the error and returns None."""
    from qx.events.nats import NatsConsumer  # noqa: PLC0415

    nc = MagicMock()
    registry = EventRegistry()
    consumer = NatsConsumer(
        nc,
        registry,
        stream="test-stream",
        durable_name="test-consumer",
        subject_filter="test.>",
    )

    fake_js = MagicMock()
    fake_js.consumer_info = AsyncMock(side_effect=RuntimeError("server gone"))
    consumer._js = fake_js

    result = await consumer.num_pending()
    assert result is None
