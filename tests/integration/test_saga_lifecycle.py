"""Integration test: Saga state machine.

Verifies the full saga lifecycle against a real Postgres:
1. SagaManager.handle(event) → creates instance, runs handler, persists state.
2. Second event → state transition on existing instance.
3. mark_complete() → instance status set to "completed".
4. Terminal instance → further events are skipped (idempotency).
5. Handler exception → compensate() runs → status set to "compensated".
6. tick_timeouts() → timeout handler invoked for stale running instances.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from qx.core import IntegrationEvent
from qx.saga import Saga, SagaManager, SagaState, on, on_timeout
from qx.saga.table import include_saga_table
from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Domain fixtures
# ---------------------------------------------------------------------------


class OrderPlaced(IntegrationEvent):
    event_name: ClassVar[str] = "test.order_placed"
    order_id: str
    amount: int


class PaymentCharged(IntegrationEvent):
    event_name: ClassVar[str] = "test.payment_charged"
    order_id: str
    payment_id: str


class PaymentFailed(IntegrationEvent):
    event_name: ClassVar[str] = "test.payment_failed"
    order_id: str


class OrderFulfillmentState(SagaState):
    order_id: str = ""
    payment_id: str = ""
    compensated: bool = False


class OrderFulfillmentSaga(Saga[OrderFulfillmentState]):
    state_type = OrderFulfillmentState

    @on(OrderPlaced)
    async def on_order_placed(self, ev: OrderPlaced) -> None:
        self.state.order_id = ev.order_id

    @on(PaymentCharged)
    async def on_payment_charged(self, ev: PaymentCharged) -> None:
        self.state.payment_id = ev.payment_id
        self.mark_complete()

    @on(PaymentFailed)
    async def on_payment_failed(self, ev: PaymentFailed) -> None:
        self.mark_failed()

    async def compensate(self) -> None:
        self.state.compensated = True

    @on_timeout(after=timedelta(hours=1))
    async def handle_timeout(self) -> None:
        self.mark_failed()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def engine(db_url: str) -> AsyncEngine:
    meta = MetaData()
    include_saga_table(meta)
    eng = create_async_engine(db_url, echo=False, poolclass=NullPool)

    async def _setup() -> None:
        async with eng.begin() as conn:
            await conn.run_sync(meta.create_all)

    asyncio.run(_setup())
    yield eng  # type: ignore[misc]
    asyncio.run(eng.dispose())


@pytest.fixture(scope="module")
def saga_table(engine: AsyncEngine) -> Any:  # type: ignore[misc]
    meta = MetaData()
    return include_saga_table(meta)


@pytest.fixture(autouse=True)
def _clean_saga_table(db_url: str) -> None:  # type: ignore[misc]
    yield  # type: ignore[misc]
    eng = create_async_engine(db_url, echo=False, poolclass=NullPool)

    async def _truncate() -> None:
        async with eng.begin() as conn:
            await conn.execute(text("TRUNCATE TABLE qx_saga_instances RESTART IDENTITY CASCADE"))
        await eng.dispose()

    asyncio.run(_truncate())


def _session_factory(engine: AsyncEngine) -> Any:
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def _mock_mediator() -> Any:
    m = AsyncMock()
    m.send = AsyncMock(return_value=None)
    return m


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_saga_created_on_first_event(engine: AsyncEngine, saga_table: Any) -> None:
    """First event creates a new saga instance in RUNNING state."""
    manager = SagaManager(mediator=_mock_mediator(), table=saga_table)
    manager.register(OrderFulfillmentSaga, correlation_key=lambda ev: ev.order_id)

    order_id = str(uuid4())
    factory = _session_factory(engine)

    async with factory() as session:
        await manager.handle(OrderPlaced(order_id=order_id, amount=99), session=session)
        await session.commit()

    async with factory() as session:
        from qx.saga.store import SagaStore  # noqa: PLC0415

        store = SagaStore(session, saga_table)
        result = await store.load(
            f"{OrderFulfillmentSaga.__module__}.{OrderFulfillmentSaga.__qualname__}", order_id
        )
        instance = result.value
        assert instance is not None
        assert instance.status == "running"
        assert instance.state_data["order_id"] == order_id


@pytest.mark.asyncio
async def test_saga_transitions_to_completed_on_second_event(
    engine: AsyncEngine, saga_table: Any
) -> None:
    """Second event transitions the saga to COMPLETED."""
    manager = SagaManager(mediator=_mock_mediator(), table=saga_table)
    manager.register(OrderFulfillmentSaga, correlation_key=lambda ev: ev.order_id)

    order_id = str(uuid4())
    factory = _session_factory(engine)

    async with factory() as session:
        await manager.handle(OrderPlaced(order_id=order_id, amount=99), session=session)
        await session.commit()

    payment_id = str(uuid4())
    async with factory() as session:
        await manager.handle(
            PaymentCharged(order_id=order_id, payment_id=payment_id), session=session
        )
        await session.commit()

    async with factory() as session:
        from qx.saga.store import SagaStore  # noqa: PLC0415

        store = SagaStore(session, saga_table)
        result = await store.load(
            f"{OrderFulfillmentSaga.__module__}.{OrderFulfillmentSaga.__qualname__}", order_id
        )
        instance = result.value
        assert instance is not None
        assert instance.status == "completed"
        assert instance.state_data["payment_id"] == payment_id


@pytest.mark.asyncio
async def test_saga_compensated_on_handler_failure(engine: AsyncEngine, saga_table: Any) -> None:
    """PaymentFailed triggers mark_failed → compensate() → COMPENSATED."""
    manager = SagaManager(mediator=_mock_mediator(), table=saga_table)
    manager.register(OrderFulfillmentSaga, correlation_key=lambda ev: ev.order_id)

    order_id = str(uuid4())
    factory = _session_factory(engine)

    async with factory() as session:
        await manager.handle(OrderPlaced(order_id=order_id, amount=50), session=session)
        await session.commit()

    async with factory() as session:
        await manager.handle(PaymentFailed(order_id=order_id), session=session)
        await session.commit()

    async with factory() as session:
        from qx.saga.store import SagaStore  # noqa: PLC0415

        store = SagaStore(session, saga_table)
        result = await store.load(
            f"{OrderFulfillmentSaga.__module__}.{OrderFulfillmentSaga.__qualname__}", order_id
        )
        instance = result.value
        assert instance is not None
        assert instance.status == "compensated"
        assert instance.state_data["compensated"] is True


@pytest.mark.asyncio
async def test_saga_terminal_instance_is_skipped(engine: AsyncEngine, saga_table: Any) -> None:
    """Events arriving after COMPLETED are dropped (idempotency)."""
    manager = SagaManager(mediator=_mock_mediator(), table=saga_table)
    manager.register(OrderFulfillmentSaga, correlation_key=lambda ev: ev.order_id)

    order_id = str(uuid4())
    factory = _session_factory(engine)

    async with factory() as session:
        await manager.handle(OrderPlaced(order_id=order_id, amount=99), session=session)
        await session.commit()

    async with factory() as session:
        await manager.handle(
            PaymentCharged(order_id=order_id, payment_id=str(uuid4())), session=session
        )
        await session.commit()

    # Send another event — should be a no-op, no crash
    async with factory() as session:
        await manager.handle(
            PaymentCharged(order_id=order_id, payment_id=str(uuid4())), session=session
        )
        await session.commit()

    async with factory() as session:
        from qx.saga.store import SagaStore  # noqa: PLC0415

        store = SagaStore(session, saga_table)
        result = await store.load(
            f"{OrderFulfillmentSaga.__module__}.{OrderFulfillmentSaga.__qualname__}", order_id
        )
        assert result.value is not None
        assert result.value.status == "completed"


@pytest.mark.asyncio
async def test_saga_tick_timeouts_fires_timeout_handler(
    engine: AsyncEngine, saga_table: Any
) -> None:
    """tick_timeouts() invokes the @on_timeout handler for stale running sagas."""
    manager = SagaManager(mediator=_mock_mediator(), table=saga_table)
    manager.register(OrderFulfillmentSaga, correlation_key=lambda ev: ev.order_id)

    order_id = str(uuid4())
    factory = _session_factory(engine)

    async with factory() as session:
        await manager.handle(OrderPlaced(order_id=order_id, amount=5), session=session)
        await session.commit()

    # Manually back-date updated_at so the row appears timed-out
    async with factory() as session:
        await session.execute(
            text("UPDATE qx_saga_instances SET updated_at = :old WHERE correlation_key = :key"),
            {"old": datetime(2000, 1, 1, tzinfo=UTC), "key": order_id},
        )
        await session.commit()

    async with factory() as session:
        await manager.tick_timeouts(session=session)
        await session.commit()

    async with factory() as session:
        from qx.saga.store import SagaStore  # noqa: PLC0415

        store = SagaStore(session, saga_table)
        result = await store.load(
            f"{OrderFulfillmentSaga.__module__}.{OrderFulfillmentSaga.__qualname__}", order_id
        )
        instance = result.value
        assert instance is not None
        # Timeout handler calls mark_failed → compensate → compensated
        assert instance.status == "compensated"
