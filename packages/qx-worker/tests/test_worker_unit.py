"""Worker runtime tests.

Mocks the NATS message + consumer surface so we can exercise the ack / nak /
drop-on-unknown branches without a live broker.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

from qx.core import IntegrationEvent
from qx.cqrs import Mediator, integration_event_handler
from qx.di import Container
from qx.events import EventRegistry, EventTypeNotRegistered
from qx.worker import PermanentWorkerError, TransientWorkerError, WorkerHealth, WorkerRuntime


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


def _make_msg(headers: dict[str, str], payload: bytes, *, ack=None, nak=None) -> MagicMock:
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
    runtime._consumer.parse_message = MagicMock(return_value=UserRegistered(email="a@b.com"))
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
    runtime._consumer.parse_message = MagicMock(return_value=UserRegistered(email="a@b.com"))
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
    runtime._consumer.parse_message = MagicMock(side_effect=EventTypeNotRegistered("unknown"))
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
    runtime._consumer.parse_message = MagicMock(side_effect=ValueError("bad json"))
    msg = _make_msg({"qx.event_name": "x"}, b"junk")
    await runtime._handle_one(msg)
    msg.ack.assert_not_called()
    msg.nak.assert_awaited_once()


# ---- PermanentWorkerError ----


class _PermanentHandler:
    async def handle(self, event: UserRegistered) -> None:
        raise PermanentWorkerError("schema v0 unsupported")


async def test_permanent_error_acks_and_drops() -> None:
    container = Container()
    registry = EventRegistry()
    registry.register(UserRegistered)
    mediator = Mediator(container)
    mediator.register_integration(UserRegistered, _PermanentHandler)

    runtime = _make_runtime(container, registry, mediator)
    runtime._consumer.parse_message = MagicMock(return_value=UserRegistered(email="x@y.com"))
    msg = _make_msg({"qx.event_name": "identity.user.registered"}, b"{}")
    await runtime._handle_one(msg)

    msg.ack.assert_awaited_once()
    msg.nak.assert_not_called()


class _TransientHandler:
    async def handle(self, event: UserRegistered) -> None:
        raise TransientWorkerError("db unavailable")


async def test_transient_error_naks() -> None:
    container = Container()
    registry = EventRegistry()
    registry.register(UserRegistered)
    mediator = Mediator(container)
    mediator.register_integration(UserRegistered, _TransientHandler)

    runtime = _make_runtime(container, registry, mediator)
    runtime._consumer.parse_message = MagicMock(return_value=UserRegistered(email="x@y.com"))
    msg = _make_msg({"qx.event_name": "identity.user.registered"}, b"{}")
    await runtime._handle_one(msg)

    msg.nak.assert_awaited_once()
    msg.ack.assert_not_called()


# ---- WorkerHealth ----


def test_health_not_healthy_before_start() -> None:
    health = WorkerHealth()
    assert not health.is_healthy()


def test_health_healthy_after_start() -> None:
    health = WorkerHealth()
    health.mark_started()
    assert health.is_healthy()


def test_health_not_healthy_after_stop() -> None:
    health = WorkerHealth()
    health.mark_started()
    health.mark_stopped()
    assert not health.is_healthy()


def test_health_stalled_when_no_activity_exceeds_threshold() -> None:
    health = WorkerHealth(stall_threshold_seconds=10.0)
    health.mark_started()
    stale = datetime.now(UTC) - timedelta(seconds=20)
    health._last_activity_at = stale
    assert not health.is_healthy()


def test_health_not_stalled_within_threshold() -> None:
    health = WorkerHealth(stall_threshold_seconds=30.0)
    health.mark_started()
    health.mark_activity()
    assert health.is_healthy()


def test_health_activity_resets_stall_clock() -> None:
    health = WorkerHealth(stall_threshold_seconds=10.0)
    health.mark_started()
    health._last_activity_at = datetime.now(UTC) - timedelta(seconds=20)
    assert not health.is_healthy()
    health.mark_activity()
    assert health.is_healthy()


# ---- Graceful shutdown ----


async def test_run_marks_started_and_stopped() -> None:
    """run() updates WorkerHealth lifecycle markers."""
    container = Container()
    registry = EventRegistry()
    mediator = Mediator(container)
    health = WorkerHealth()

    consumer = MagicMock()

    async def _fetch_timeout(n, timeout):  # noqa: ASYNC109
        # yield to the event loop before raising so _stop_soon() can run
        await asyncio.sleep(0)
        raise TimeoutError

    @asynccontextmanager
    async def _messages():
        yield MagicMock(fetch=_fetch_timeout)

    consumer.messages = _messages
    runtime = WorkerRuntime(
        container=container,
        consumer=consumer,
        registry=registry,
        mediator=mediator,
        concurrency=1,
        health=health,
    )

    async def _stop_soon():
        await asyncio.sleep(0.05)
        await runtime.stop()

    await asyncio.gather(runtime.run(), _stop_soon())

    assert not health.is_healthy()  # stopped


async def test_run_drains_inflight_before_exit() -> None:
    """Messages already dispatched when stop() is called are awaited."""
    container = Container()
    registry = EventRegistry()
    registry.register(UserRegistered)
    mediator = Mediator(container)
    mediator.register_decorated(_UserRegisteredHandler)

    slow_msg = _make_msg({"qx.event_name": "identity.user.registered"}, b"{}")

    completed: list[bool] = []

    async def _slow_handle(msg):
        await asyncio.sleep(0.05)
        completed.append(True)
        await msg.ack()

    consumer = MagicMock()
    fetch_calls = 0

    async def _fetch(n, timeout):  # noqa: ASYNC109
        nonlocal fetch_calls
        fetch_calls += 1
        if fetch_calls == 1:
            return [slow_msg]
        await asyncio.sleep(0)  # yield before raising so stop() can proceed
        raise TimeoutError

    @asynccontextmanager
    async def _messages():
        yield MagicMock(fetch=_fetch)

    consumer.messages = _messages
    consumer.parse_message = MagicMock(return_value=UserRegistered(email="drain@test.com"))

    runtime = WorkerRuntime(
        container=container,
        consumer=consumer,
        registry=registry,
        mediator=mediator,
        concurrency=2,
    )

    with patch.object(runtime, "_handle_one", side_effect=_slow_handle):
        async def _stop_soon():
            await asyncio.sleep(0.02)  # stop before slow_msg finishes
            await runtime.stop()

        await asyncio.gather(runtime.run(), _stop_soon())

    assert completed == [True], "in-flight message must complete before run() returns"
