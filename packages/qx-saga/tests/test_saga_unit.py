"""Saga unit tests — no database required."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from qx.core import IntegrationEvent, Result
from qx.saga import Saga, SagaManager, SagaState, on, on_timeout
from qx.saga.manager import _saga_type_key
from qx.saga.store import SagaInstance, SagaStore

# ---- Domain fixtures ----


class OrderPlaced(IntegrationEvent):
    event_name: ClassVar[str] = "order.placed"
    order_id: str


class PaymentCharged(IntegrationEvent):
    event_name: ClassVar[str] = "order.payment_charged"
    order_id: str
    payment_id: str


class OrderShipped(IntegrationEvent):
    event_name: ClassVar[str] = "order.shipped"
    order_id: str


class OrderFulfillmentState(SagaState):
    order_id: str = ""
    payment_id: str = ""
    compensated: bool = False


class OrderFulfillmentSaga(Saga[OrderFulfillmentState]):
    state_type = OrderFulfillmentState

    @on(OrderPlaced)
    async def start(self, ev: OrderPlaced) -> None:
        self.state.order_id = ev.order_id
        await self.dispatch(MagicMock())

    @on(PaymentCharged)
    async def payment_done(self, ev: PaymentCharged) -> None:
        self.state.payment_id = ev.payment_id

    @on(OrderShipped)
    async def finish(self, ev: OrderShipped) -> None:
        self.mark_complete()

    @on_timeout(after=timedelta(minutes=30))
    async def handle_timeout(self) -> None:
        self.mark_failed()

    async def compensate(self) -> None:
        self.state.compensated = True


# ---- SagaMeta / decorator tests ----


def test_handlers_collected_by_meta() -> None:
    assert OrderPlaced in OrderFulfillmentSaga._handlers
    assert PaymentCharged in OrderFulfillmentSaga._handlers
    assert OrderShipped in OrderFulfillmentSaga._handlers


def test_timeout_collected_by_meta() -> None:
    assert OrderFulfillmentSaga._timeout_after == timedelta(minutes=30)
    assert OrderFulfillmentSaga._timeout_handler is not None


def test_handles_returns_true_for_registered_event() -> None:
    assert OrderFulfillmentSaga.handles(OrderPlaced)
    assert OrderFulfillmentSaga.handles(PaymentCharged)


def test_handles_returns_false_for_unknown_event() -> None:
    assert not OrderFulfillmentSaga.handles(OrderShipped.__class__)

    class UnknownEvent(IntegrationEvent):
        event_name: ClassVar[str] = "unknown"

    assert not OrderFulfillmentSaga.handles(UnknownEvent)


def test_state_type_auto_derived() -> None:
    class AutoState(SagaState):
        val: int = 0

    class AutoSaga(Saga[AutoState]):
        pass

    assert AutoSaga.state_type is AutoState


# ---- Saga handle() ----


async def test_handle_updates_state() -> None:
    mediator = AsyncMock()
    saga = _make_saga(OrderFulfillmentSaga, mediator=mediator)

    await saga.handle(OrderPlaced(order_id="o1"))

    assert saga.state.order_id == "o1"
    mediator.send.assert_awaited_once()


async def test_handle_mark_complete() -> None:
    saga = _make_saga(OrderFulfillmentSaga)
    saga.state.order_id = "o1"
    await saga.handle(OrderShipped(order_id="o1"))
    assert saga._completed is True


async def test_handle_unknown_event_is_noop() -> None:
    class OtherEvent(IntegrationEvent):
        event_name: ClassVar[str] = "other"

    saga = _make_saga(OrderFulfillmentSaga)
    await saga.handle(OtherEvent())  # should not raise
    assert saga.state.order_id == ""


async def test_compensate_called_on_mark_failed() -> None:
    saga = _make_saga(OrderFulfillmentSaga)
    saga.mark_failed()
    await saga.compensate()
    assert saga.state.compensated is True


# ---- SagaManager ----


async def test_manager_creates_instance_on_first_event() -> None:
    store = _mock_store(existing=None)
    manager, _ = _make_manager(store)

    with patch_store(manager, store):
        await manager.handle(OrderPlaced(order_id="o1"), session=AsyncMock())

    store.load.assert_awaited_once()
    store.create.assert_awaited_once()
    store.save.assert_awaited_once()


async def test_manager_skips_terminal_instances() -> None:
    instance = _make_instance(status="completed")
    store = _mock_store(existing=instance)
    manager, _ = _make_manager(store)

    with patch_store(manager, store):
        await manager.handle(PaymentCharged(order_id="o1", payment_id="p1"), session=AsyncMock())

    store.save.assert_not_awaited()


async def test_manager_marks_completed_when_saga_completes() -> None:
    instance = _make_instance(
        status="running", state={"order_id": "o1", "payment_id": "", "compensated": False}
    )
    store = _mock_store(existing=instance)
    manager, _ = _make_manager(store)

    with patch_store(manager, store):
        await manager.handle(OrderShipped(order_id="o1"), session=AsyncMock())

    store.save.assert_awaited_once()
    _, kwargs = store.save.call_args
    assert kwargs["new_status"] == "completed"


async def test_manager_compensates_on_handler_exception() -> None:
    class BoomSaga(Saga[OrderFulfillmentState]):
        state_type = OrderFulfillmentState

        @on(OrderPlaced)
        async def start(self, ev: OrderPlaced) -> None:
            raise RuntimeError("downstream failure")

        async def compensate(self) -> None:
            self.state.compensated = True

    instance = _make_instance(status="running")
    store = _mock_store(existing=instance)
    mediator = AsyncMock()
    manager = SagaManager(mediator=mediator, table=MagicMock())
    manager.register(BoomSaga, correlation_key=lambda ev: getattr(ev, "order_id", None))
    manager._table = store._table if hasattr(store, "_table") else MagicMock()

    with patch_store(manager, store):
        await manager.handle(OrderPlaced(order_id="o1"), session=AsyncMock())

    store.save.assert_awaited_once()
    _, kwargs = store.save.call_args
    assert kwargs["new_status"] == "compensated"


async def test_manager_skips_event_when_key_fn_returns_none() -> None:
    store = _mock_store(existing=None)
    manager, _ = _make_manager(store, key_fn=lambda ev: None)

    await manager.handle(OrderPlaced(order_id="o1"), session=AsyncMock())

    store.load.assert_not_awaited()


# ---- Helpers ----


def _make_saga(
    cls: type[OrderFulfillmentSaga],
    *,
    mediator: AsyncMock | None = None,
    state: dict | None = None,
) -> OrderFulfillmentSaga:
    m = mediator or AsyncMock()
    saga = object.__new__(cls)
    saga.state = cls.state_type.model_validate(state or {})
    saga._mediator = m
    saga._completed = False
    saga._failed = False
    return saga


def _make_instance(*, status: str = "running", state: dict | None = None) -> SagaInstance:
    return SagaInstance(
        id=uuid4(),
        saga_type=_saga_type_key(OrderFulfillmentSaga),
        correlation_key="o1",
        status=status,
        state_data=state or {"order_id": "", "payment_id": "", "compensated": False},
        version=0,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _mock_store(*, existing: SagaInstance | None) -> AsyncMock:
    store = AsyncMock(spec=SagaStore)
    store.load.return_value = Result.success(existing)
    store.create.return_value = Result.success(_make_instance() if existing is None else existing)
    store.save.return_value = Result.success(None)
    return store


def _make_manager(
    store: AsyncMock,
    *,
    key_fn: object = None,
) -> tuple[SagaManager, AsyncMock]:
    mediator = AsyncMock()
    manager = SagaManager(mediator=mediator, table=MagicMock())
    manager.register(
        OrderFulfillmentSaga,
        correlation_key=key_fn or (lambda ev: getattr(ev, "order_id", None)),
    )
    return manager, mediator


# ---- compensate() retry ----


async def test_compensate_retries_on_transient_failure() -> None:
    """compensate() is retried up to max_attempts on exception."""
    calls: list[int] = []

    class RetrySaga(Saga[OrderFulfillmentState]):
        state_type = OrderFulfillmentState

        @on(OrderPlaced)
        async def start(self, ev: OrderPlaced) -> None:
            raise RuntimeError("transient failure")

        async def compensate(self) -> None:
            calls.append(len(calls))
            if len(calls) < 3:
                raise RuntimeError("compensate also failed")
            self.state.compensated = True

    instance = _make_instance(status="running")
    store = _mock_store(existing=instance)
    mediator = AsyncMock()
    manager = SagaManager(
        mediator=mediator,
        table=MagicMock(),
        compensate_max_attempts=3,
        compensate_base_delay_seconds=0.0,
    )
    manager.register(RetrySaga, correlation_key=lambda ev: getattr(ev, "order_id", None))

    with patch_store(manager, store):
        await manager.handle(OrderPlaced(order_id="o1"), session=AsyncMock())

    assert len(calls) == 3
    store.save.assert_awaited_once()
    _, kwargs = store.save.call_args
    assert kwargs["new_status"] == "compensated"


async def test_compensate_retry_exhausted_still_marks_compensated() -> None:
    """Even if all compensate() attempts fail, the saga is marked compensated."""

    class AlwaysFailSaga(Saga[OrderFulfillmentState]):
        state_type = OrderFulfillmentState

        @on(OrderPlaced)
        async def start(self, ev: OrderPlaced) -> None:
            raise RuntimeError("handler failure")

        async def compensate(self) -> None:
            raise RuntimeError("compensation also explodes")

    instance = _make_instance(status="running")
    store = _mock_store(existing=instance)
    mediator = AsyncMock()
    manager = SagaManager(
        mediator=mediator,
        table=MagicMock(),
        compensate_max_attempts=2,
        compensate_base_delay_seconds=0.0,
    )
    manager.register(AlwaysFailSaga, correlation_key=lambda ev: getattr(ev, "order_id", None))

    with patch_store(manager, store):
        await manager.handle(OrderPlaced(order_id="o1"), session=AsyncMock())

    store.save.assert_awaited_once()
    _, kwargs = store.save.call_args
    assert kwargs["new_status"] == "compensated"


# ---- timeout concurrency lock ----


async def test_tick_timeouts_skips_instance_when_lock_not_acquired() -> None:
    """When the distributed lock is already held, the instance is skipped."""
    from contextlib import asynccontextmanager  # noqa: PLC0415

    @asynccontextmanager
    async def _not_held():
        yield False  # lock not acquired

    lock = MagicMock()
    lock.acquired = MagicMock(return_value=_not_held())

    def _lock_factory(key: str) -> object:
        return lock

    instance = _make_instance(
        status="running", state={"order_id": "", "payment_id": "", "compensated": False}
    )
    store = _mock_store(existing=instance)
    store.load_timed_out = AsyncMock(return_value=[instance])

    mediator = AsyncMock()
    manager = SagaManager(mediator=mediator, table=MagicMock(), lock_factory=_lock_factory)
    manager.register(
        OrderFulfillmentSaga,
        correlation_key=lambda ev: getattr(ev, "order_id", None),
    )

    with patch_store(manager, store), patch("qx.saga.manager.SagaStore") as mock_cls:
        mock_cls.return_value = store
        await manager.tick_timeouts(session=AsyncMock())

    store.save.assert_not_awaited()


async def test_tick_timeouts_processes_instance_when_lock_acquired() -> None:
    """When the lock is acquired, the timeout handler runs and state is saved."""
    from contextlib import asynccontextmanager  # noqa: PLC0415

    @asynccontextmanager
    async def _held():
        yield True  # lock acquired

    lock = MagicMock()
    lock.acquired = MagicMock(return_value=_held())

    def _lock_factory(key: str) -> object:
        return lock

    instance = _make_instance(
        status="running",
        state={"order_id": "o1", "payment_id": "", "compensated": False},
    )
    store = _mock_store(existing=instance)
    store.load_timed_out = AsyncMock(return_value=[instance])

    mediator = AsyncMock()
    manager = SagaManager(mediator=mediator, table=MagicMock(), lock_factory=_lock_factory)
    manager.register(
        OrderFulfillmentSaga,
        correlation_key=lambda ev: getattr(ev, "order_id", None),
    )

    with patch("qx.saga.manager.SagaStore") as mock_cls:
        mock_cls.return_value = store
        await manager.tick_timeouts(session=AsyncMock())

    store.save.assert_awaited_once()


async def test_tick_timeouts_no_lock_factory_processes_directly() -> None:
    """Without a lock_factory, instances are processed without locking."""
    instance = _make_instance(
        status="running",
        state={"order_id": "o1", "payment_id": "", "compensated": False},
    )
    store = _mock_store(existing=instance)
    store.load_timed_out = AsyncMock(return_value=[instance])

    mediator = AsyncMock()
    manager = SagaManager(mediator=mediator, table=MagicMock())  # no lock_factory
    manager.register(
        OrderFulfillmentSaga,
        correlation_key=lambda ev: getattr(ev, "order_id", None),
    )

    with patch("qx.saga.manager.SagaStore") as mock_cls:
        mock_cls.return_value = store
        await manager.tick_timeouts(session=AsyncMock())

    store.save.assert_awaited_once()


def patch_store(manager: SagaManager, store: AsyncMock) -> object:
    """Context manager that injects a mock store into manager._dispatch_to."""

    @contextmanager
    def _patch():  # type: ignore[return]
        original = manager._dispatch_to

        async def _patched(**kwargs):  # type: ignore[return]
            kwargs["store"] = store
            return await original(**kwargs)

        with patch.object(manager, "_dispatch_to", side_effect=_patched):
            yield

    return _patch()
