"""Worker runtime tests.

Mocks the NATS message + consumer surface so we can exercise the ack / nak /
drop-on-unknown branches without a live broker.
"""

from __future__ import annotations

import asyncio
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from qx.core import IntegrationEvent
from qx.cqrs import Mediator, integration_event_handler
from qx.di import Container
from qx.events import EventRegistry, EventTypeNotRegistered
from qx.worker import WorkerRuntime


class UserRegistered(IntegrationEvent):
    event_name: ClassVar[str] = "identity.user.registered"
    email: str


class _Captured:
    received: list[str] = []  # noqa: RUF012


@integration_event_handler(UserRegistered)
class _UserRegisteredHandler:
    async def handle(self, event: UserRegistered) -> None:
        _Captured.received.append(event.email)


class _BoomHandler:
    async def handle(self, event: UserRegistered) -> None:
        raise RuntimeError("downstream timeout")


# ---- Helpers ----


def _make_msg(
    headers: dict[str, str], payload: bytes, *, ack=None, nak=None
) -> MagicMock:
    msg = MagicMock()
    msg.headers = headers
    msg.data = payload
    msg.ack = ack or AsyncMock()
    msg.nak = nak or AsyncMock()
    return msg


def _make_runtime(
    container: Container,
    registry: EventRegistry,
    mediator: Mediator,
) -> WorkerRuntime:
    consumer = MagicMock()
    return WorkerRuntime(
        container=container,
        consumer=consumer,
        registry=registry,
        mediator=mediator,
        concurrency=1,
    )


# ---- Tests ----


async def test_handler_ack_on_success() -> None:
    _Captured.received.clear()
    container = Container()
    registry = EventRegistry()
    registry.register(UserRegistered)
    mediator = Mediator(container)
    mediator.register_decorated(_UserRegisteredHandler)

    runtime = _make_runtime(container, registry, mediator)
    # Wire the consumer parse_message to return a real event
    runtime._consumer.parse_message = MagicMock(
        return_value=UserRegistered(email="a@b.com")
    )
    msg = _make_msg(
        {"qx.event_name": "identity.user.registered"},
        b'{"event_name":"identity.user.registered","payload":{"email":"a@b.com"}}',
    )
    await runtime._handle_one(msg)

    msg.ack.assert_awaited_once()
    msg.nak.assert_not_called()
    assert _Captured.received == ["a@b.com"]


async def test_handler_nak_on_exception() -> None:
    container = Container()
    registry = EventRegistry()
    registry.register(UserRegistered)
    mediator = Mediator(container)
    mediator.register_integration(UserRegistered, _BoomHandler)

    runtime = _make_runtime(container, registry, mediator)
    runtime._consumer.parse_message = MagicMock(
        return_value=UserRegistered(email="a@b.com")
    )
    msg = _make_msg(
        {"qx.event_name": "identity.user.registered"},
        b"{}",
    )
    await runtime._handle_one(msg)
    msg.ack.assert_not_called()
    msg.nak.assert_awaited_once()


async def test_unknown_event_type_acks_and_drops() -> None:
    container = Container()
    registry = EventRegistry()  # empty — UserRegistered not registered
    mediator = Mediator(container)

    runtime = _make_runtime(container, registry, mediator)
    runtime._consumer.parse_message = MagicMock(
        side_effect=EventTypeNotRegistered("unknown")
    )
    msg = _make_msg(
        {"qx.event_name": "unknown.event"},
        b"{}",
    )
    await runtime._handle_one(msg)
    msg.ack.assert_awaited_once()
    msg.nak.assert_not_called()


async def test_malformed_payload_naks() -> None:
    container = Container()
    registry = EventRegistry()
    mediator = Mediator(container)

    runtime = _make_runtime(container, registry, mediator)
    runtime._consumer.parse_message = MagicMock(
        side_effect=ValueError("bad json")
    )
    msg = _make_msg({"qx.event_name": "x"}, b"junk")
    await runtime._handle_one(msg)
    msg.ack.assert_not_called()
    msg.nak.assert_awaited_once()
