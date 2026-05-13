"""Unit tests for the Order aggregate — pure domain logic, no I/O."""

from __future__ import annotations

from uuid import uuid4

from order_service.domain.aggregates.order import (
    Order,
    OrderCancelled,
    OrderConfirmed,
    OrderItem,
    OrderPlaced,
    OrderStatus,
)


def _items() -> list[OrderItem]:
    return [OrderItem(sku="SKU-001", quantity=2, unit_price_cents=1000)]


def test_place_order_creates_pending_order() -> None:
    result = Order.place(customer_id=uuid4(), items=_items(), total_cents=2000)
    assert result.is_success
    order = result.value
    assert order.status == OrderStatus.PENDING
    assert order.total_cents == 2000
    assert len(order.items) == 1


def test_place_order_emits_order_placed_event() -> None:
    result = Order.place(customer_id=uuid4(), items=_items(), total_cents=2000)
    order = result.value
    events = order.pull_events()
    assert len(events) == 1
    assert isinstance(events[0], OrderPlaced)


def test_place_order_fails_with_empty_items() -> None:
    result = Order.place(customer_id=uuid4(), items=[], total_cents=2000)
    assert result.is_failure
    assert result.error.code == "order.empty_items"


def test_place_order_fails_with_zero_total() -> None:
    result = Order.place(customer_id=uuid4(), items=_items(), total_cents=0)
    assert result.is_failure
    assert result.error.code == "order.invalid_total"


def test_confirm_transitions_to_confirmed() -> None:
    order = Order.place(customer_id=uuid4(), items=_items(), total_cents=500).value
    order.pull_events()  # drain placement events
    result = order.confirm()
    assert result.is_success
    assert order.status == OrderStatus.CONFIRMED


def test_confirm_emits_order_confirmed_event() -> None:
    order = Order.place(customer_id=uuid4(), items=_items(), total_cents=500).value
    order.pull_events()
    order.confirm()
    events = order.pull_events()
    assert len(events) == 1
    assert isinstance(events[0], OrderConfirmed)


def test_confirm_already_confirmed_fails() -> None:
    order = Order.place(customer_id=uuid4(), items=_items(), total_cents=500).value
    order.pull_events()
    order.confirm()
    order.pull_events()
    result = order.confirm()
    assert result.is_failure
    assert result.error.code == "order.not_pending"


def test_cancel_transitions_to_cancelled() -> None:
    order = Order.place(customer_id=uuid4(), items=_items(), total_cents=500).value
    order.pull_events()
    result = order.cancel("changed my mind")
    assert result.is_success
    assert order.status == OrderStatus.CANCELLED


def test_cancel_emits_order_cancelled_event() -> None:
    order = Order.place(customer_id=uuid4(), items=_items(), total_cents=500).value
    order.pull_events()
    order.cancel("testing")
    events = order.pull_events()
    assert len(events) == 1
    assert isinstance(events[0], OrderCancelled)
    assert events[0].reason == "testing"


def test_cancel_already_cancelled_fails() -> None:
    order = Order.place(customer_id=uuid4(), items=_items(), total_cents=500).value
    order.pull_events()
    order.cancel("first")
    order.pull_events()
    result = order.cancel("second")
    assert result.is_failure
    assert result.error.code == "order.already_cancelled"


def test_replay_restores_confirmed_state() -> None:
    customer = uuid4()
    order = Order.place(customer_id=customer, items=_items(), total_cents=500).value
    placed_event = order.pull_events()[0]
    order.confirm()
    confirmed_event = order.pull_events()[0]

    # Build a new aggregate by replaying events
    replayed = Order(id=order.id)
    replayed._replay([placed_event, confirmed_event], version=2)

    assert replayed.status == OrderStatus.CONFIRMED
    assert replayed.customer_id == customer
    assert replayed.total_cents == 500
    assert replayed.version == 2
