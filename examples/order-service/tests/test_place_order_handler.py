"""Unit tests for PlaceOrderHandler — stubs out EventStore and outbox."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from order_service.application.commands.place_order import (
    OrderItemInput,
    PlaceOrderCommand,
    PlaceOrderHandler,
)
from order_service.domain.aggregates.order import OrderPlacedIntegration
from qx.core import Result
from qx.db import DefaultOutboxRecorder


class _StubSession:
    """Minimal AsyncSession stub for unit tests."""

    def __init__(self) -> None:
        self.committed = False
        self._execute_results: list[Any] = []

    async def execute(self, *a: Any, **kw: Any) -> Any:
        return MagicMock(mappings=lambda: MagicMock(first=lambda: None))

    async def commit(self) -> None:
        self.committed = True

    async def __aenter__(self) -> _StubSession:
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass

    def begin(self) -> Any:
        return _Txn()


class _Txn:
    async def __aenter__(self) -> _Txn:
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass


def _make_session_factory(session: _StubSession) -> Any:
    class _Factory:
        def __call__(self) -> _StubSession:
            return session

    return _Factory()


async def test_place_order_returns_order_id_on_success() -> None:
    session = _StubSession()
    sf = _make_session_factory(session)
    outbox = AsyncMock(spec=DefaultOutboxRecorder)
    table = MagicMock()
    snaps = MagicMock()

    handler = PlaceOrderHandler(sf, table, snaps, outbox)

    with patch("order_service.application.commands.place_order.EventStore") as mock_store_cls:
        mock_store = MagicMock()
        mock_store.append = AsyncMock(return_value=Result.success(1))
        mock_store_cls.return_value = mock_store

        result = await handler.handle(
            PlaceOrderCommand(
                customer_id=uuid4(),
                items=[OrderItemInput(sku="ABC", quantity=1, unit_price_cents=500)],
                total_cents=500,
            ),
        )

    assert result.is_success
    assert result.value.order_id is not None


async def test_place_order_records_integration_event() -> None:
    session = _StubSession()
    sf = _make_session_factory(session)
    outbox = AsyncMock(spec=DefaultOutboxRecorder)
    table = MagicMock()
    snaps = MagicMock()

    handler = PlaceOrderHandler(sf, table, snaps, outbox)

    with patch("order_service.application.commands.place_order.EventStore") as mock_store_cls:
        mock_store = MagicMock()
        mock_store.append = AsyncMock(return_value=Result.success(1))
        mock_store_cls.return_value = mock_store

        await handler.handle(
            PlaceOrderCommand(
                customer_id=uuid4(),
                items=[OrderItemInput(sku="ABC", quantity=1, unit_price_cents=500)],
                total_cents=500,
            ),
        )

    outbox.record.assert_called_once()
    events = outbox.record.call_args[0][1]
    assert len(events) == 1
    assert isinstance(events[0], OrderPlacedIntegration)


async def test_place_order_fails_on_empty_items() -> None:
    session = _StubSession()
    sf = _make_session_factory(session)
    outbox = AsyncMock(spec=DefaultOutboxRecorder)
    table = MagicMock()
    snaps = MagicMock()

    handler = PlaceOrderHandler(sf, table, snaps, outbox)

    result = await handler.handle(
        PlaceOrderCommand(
            customer_id=uuid4(),
            items=[],
            total_cents=500,
        ),
    )

    assert result.is_failure
    assert result.error.code == "order.empty_items"
    outbox.record.assert_not_called()
