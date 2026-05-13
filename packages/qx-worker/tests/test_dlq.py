"""Dead Letter Queue unit tests.

Verifies:
- DeadLetterStore.persist() writes the correct row and publishes a notification.
- The worker routes last-attempt failures to the DLQ (ack) instead of naking.
- The worker naks normally when DLQ is not configured or not the last attempt.
- _is_last_attempt helper returns correct result.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock

import pytest
from qx.core import IntegrationEvent
from qx.di import Container
from qx.events import EventRegistry
from qx.worker import DeadLetterStore, WorkerRuntime
from qx.worker.dlq import _num_delivered
from qx.worker.runtime import _is_last_attempt

# ---------------------------------------------------------------------------
# Fake helpers
# ---------------------------------------------------------------------------


def _make_msg(
    headers: dict,
    payload: bytes = b"{}",
    *,
    num_delivered: int | None = None,
    subject: str = "svc.events",
) -> MagicMock:
    msg = MagicMock()
    msg.headers = headers
    msg.data = payload
    msg.subject = subject
    msg.ack = AsyncMock()
    msg.nak = AsyncMock()
    if num_delivered is not None:
        meta = MagicMock()
        meta.num_delivered = num_delivered
        msg.metadata = meta
    else:
        msg.metadata = None
    return msg


def _make_session_factory(executed_statements: list) -> Any:
    """Returns a fake async_sessionmaker that records SQL statements."""

    class _FakeSession:
        async def execute(self, stmt: Any, params: Any = None) -> None:
            executed_statements.append({"stmt": str(stmt), "params": params})

        async def commit(self) -> None:
            pass

        async def __aenter__(self) -> _FakeSession:
            return self

        async def __aexit__(self, *_: Any) -> None:
            pass

    @asynccontextmanager
    async def _factory():
        yield _FakeSession()

    return _factory


# ---------------------------------------------------------------------------
# DeadLetterStore.persist
# ---------------------------------------------------------------------------


class TestDeadLetterStore:
    @pytest.mark.asyncio
    async def test_persist_writes_row(self) -> None:
        executed: list = []
        factory = _make_session_factory(executed)
        store = DeadLetterStore(factory, js=None)

        msg = _make_msg(
            {"qx.event_name": "order.placed", "qx.event_version": "2"},
            payload=json.dumps({"event_name": "order.placed"}).encode(),
            num_delivered=5,
        )
        await store.persist(msg, error="handler exploded")

        assert len(executed) == 1
        params = executed[0]["params"]
        assert params["event_name"] == "order.placed"
        assert params["event_version"] == 2
        assert params["delivered_count"] == 5
        assert "handler exploded" in params["last_error"]

    @pytest.mark.asyncio
    async def test_persist_publishes_nats_notification(self) -> None:
        executed: list = []
        factory = _make_session_factory(executed)

        js = MagicMock()
        js.publish = AsyncMock()
        store = DeadLetterStore(factory, js=js, subject_prefix="qx_dlq")

        msg = _make_msg({"qx.event_name": "order.placed"})
        await store.persist(msg, error="boom")

        js.publish.assert_awaited_once()
        call_args = js.publish.call_args
        assert call_args[0][0] == "qx_dlq.order.placed"

    @pytest.mark.asyncio
    async def test_persist_no_nats_when_js_is_none(self) -> None:
        executed: list = []
        factory = _make_session_factory(executed)
        store = DeadLetterStore(factory, js=None)

        msg = _make_msg({"qx.event_name": "x.event"})
        await store.persist(msg)  # no error arg — should still work

        assert len(executed) == 1  # DB write happened
        # No exception raised — NATS publish skipped silently

    @pytest.mark.asyncio
    async def test_persist_nats_failure_is_non_fatal(self) -> None:
        executed: list = []
        factory = _make_session_factory(executed)

        js = MagicMock()
        js.publish = AsyncMock(side_effect=RuntimeError("NATS gone"))
        store = DeadLetterStore(factory, js=js)

        msg = _make_msg({"qx.event_name": "x.event"})
        await store.persist(msg)  # should not raise despite NATS failure

        assert len(executed) == 1

    @pytest.mark.asyncio
    async def test_persist_truncates_long_error(self) -> None:
        executed: list = []
        factory = _make_session_factory(executed)
        store = DeadLetterStore(factory, js=None)

        msg = _make_msg({"qx.event_name": "x"})
        long_error = "x" * 5000
        await store.persist(msg, error=long_error)

        params = executed[0]["params"]
        assert len(params["last_error"]) <= 2048

    @pytest.mark.asyncio
    async def test_persist_handles_malformed_payload(self) -> None:
        executed: list = []
        factory = _make_session_factory(executed)
        store = DeadLetterStore(factory, js=None)

        msg = _make_msg({"qx.event_name": "x"}, payload=b"\xff\xfe not utf8")
        await store.persist(msg)  # should not raise

        params = executed[0]["params"]
        assert "raw" in json.loads(params["payload"])


# ---------------------------------------------------------------------------
# _num_delivered helper
# ---------------------------------------------------------------------------


def test_num_delivered_from_metadata() -> None:
    msg = _make_msg({}, num_delivered=3)
    assert _num_delivered(msg) == 3


def test_num_delivered_defaults_to_1_when_no_metadata() -> None:
    msg = _make_msg({})  # metadata=None
    assert _num_delivered(msg) == 1


# ---------------------------------------------------------------------------
# _is_last_attempt helper
# ---------------------------------------------------------------------------


def _make_consumer(max_deliver: int) -> MagicMock:
    c = MagicMock()
    c._max_deliver = max_deliver
    return c


def test_is_last_attempt_true_at_max() -> None:
    msg = _make_msg({}, num_delivered=5)
    assert _is_last_attempt(msg, _make_consumer(5)) is True


def test_is_last_attempt_true_when_exceeded() -> None:
    msg = _make_msg({}, num_delivered=6)
    assert _is_last_attempt(msg, _make_consumer(5)) is True


def test_is_last_attempt_false_before_max() -> None:
    msg = _make_msg({}, num_delivered=3)
    assert _is_last_attempt(msg, _make_consumer(5)) is False


def test_is_last_attempt_false_when_no_metadata() -> None:
    msg = _make_msg({})  # metadata = None → num_delivered = 0
    assert _is_last_attempt(msg, _make_consumer(5)) is False


def test_is_last_attempt_false_when_max_deliver_zero() -> None:
    msg = _make_msg({}, num_delivered=5)
    assert _is_last_attempt(msg, _make_consumer(0)) is False


# ---------------------------------------------------------------------------
# WorkerRuntime DLQ routing
# ---------------------------------------------------------------------------


class _FailEvent(IntegrationEvent):
    event_name: ClassVar[str] = "svc.fail_event"


class _BoomHandler:
    async def handle(self, event: _FailEvent) -> None:
        raise RuntimeError("db timeout")


def _make_dlq_runtime(
    *,
    dlq: DeadLetterStore | None = None,
    max_deliver: int = 5,
) -> WorkerRuntime:
    container = Container()
    registry = EventRegistry()
    registry.register(_FailEvent)
    mediator_mock = MagicMock()
    mediator_mock.consume_integration = AsyncMock(side_effect=RuntimeError("db timeout"))

    consumer = MagicMock()
    consumer._max_deliver = max_deliver

    return WorkerRuntime(
        container=container,
        consumer=consumer,
        registry=registry,
        mediator=mediator_mock,
        dlq=dlq,
    )


@pytest.mark.asyncio
async def test_worker_routes_to_dlq_on_last_attempt() -> None:
    """On the last delivery attempt, the worker calls dlq.persist and acks."""
    persisted: list = []

    async def _fake_persist(msg: Any, *, error: str | None = None) -> None:
        persisted.append({"msg": msg, "error": error})

    dlq = MagicMock(spec=DeadLetterStore)
    dlq.persist = AsyncMock(side_effect=_fake_persist)

    runtime = _make_dlq_runtime(dlq=dlq, max_deliver=5)
    msg = _make_msg({"qx.event_name": "svc.fail_event"}, num_delivered=5)
    runtime._consumer.parse_message = MagicMock(return_value=_FailEvent())

    await runtime._handle_one(msg)

    dlq.persist.assert_awaited_once()
    msg.ack.assert_awaited_once()
    msg.nak.assert_not_called()
    assert len(persisted) == 1
    assert "db timeout" in persisted[0]["error"]


@pytest.mark.asyncio
async def test_worker_naks_when_not_last_attempt() -> None:
    """Before the last delivery, the worker naks normally even when DLQ is configured."""
    dlq = MagicMock(spec=DeadLetterStore)
    dlq.persist = AsyncMock()

    runtime = _make_dlq_runtime(dlq=dlq, max_deliver=5)
    msg = _make_msg({"qx.event_name": "svc.fail_event"}, num_delivered=2)
    runtime._consumer.parse_message = MagicMock(return_value=_FailEvent())

    await runtime._handle_one(msg)

    dlq.persist.assert_not_awaited()
    msg.nak.assert_awaited_once()
    msg.ack.assert_not_called()


@pytest.mark.asyncio
async def test_worker_naks_without_dlq_on_last_attempt() -> None:
    """When no DLQ is configured, the worker naks even on the last attempt."""
    runtime = _make_dlq_runtime(dlq=None, max_deliver=5)
    msg = _make_msg({"qx.event_name": "svc.fail_event"}, num_delivered=5)
    runtime._consumer.parse_message = MagicMock(return_value=_FailEvent())

    await runtime._handle_one(msg)

    msg.nak.assert_awaited_once()
    msg.ack.assert_not_called()


@pytest.mark.asyncio
async def test_worker_acks_even_when_dlq_persist_fails() -> None:
    """If dlq.persist raises, the worker still acks (message must not loop forever)."""
    dlq = MagicMock(spec=DeadLetterStore)
    dlq.persist = AsyncMock(side_effect=RuntimeError("DB unreachable"))

    runtime = _make_dlq_runtime(dlq=dlq, max_deliver=5)
    msg = _make_msg({"qx.event_name": "svc.fail_event"}, num_delivered=5)
    runtime._consumer.parse_message = MagicMock(return_value=_FailEvent())

    await runtime._handle_one(msg)  # must not raise

    msg.ack.assert_awaited_once()
    msg.nak.assert_not_called()
