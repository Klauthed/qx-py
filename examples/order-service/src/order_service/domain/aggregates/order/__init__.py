"""Order aggregate — event-sourced.

The Order is the central aggregate for this service.  Every state change
is captured as a domain event that is persisted to the event log.  The
aggregate is never updated in place; the state is always rebuilt by
replaying the event stream from the store.

Events
------
- ``OrderPlaced``   — created in PENDING state
- ``OrderConfirmed`` — transitions PENDING → CONFIRMED
- ``OrderCancelled`` — transitions PENDING or CONFIRMED → CANCELLED

Integration events
------------------
- ``OrderPlacedIntegration`` — published to the outbox so downstream
  services (fulfilment, notifications, …) can react to new orders.
"""

from __future__ import annotations

from dataclasses import field
from typing import Any, ClassVar
from uuid import UUID, uuid4

from qx.core import DomainError, DomainEvent, IntegrationEvent, Result
from qx.eventstore import EventSourcedAggregate

__all__ = [
    "Order",
    "OrderCancelled",
    "OrderConfirmed",
    "OrderItem",
    "OrderPlaced",
    "OrderPlacedIntegration",
    "OrderStatus",
]

ORDER_AGGREGATE_TYPE = "order_service.Order"


class OrderStatus:
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Domain events (persisted to the event log)
# ---------------------------------------------------------------------------


class OrderPlaced(DomainEvent):
    event_name: ClassVar[str] = "order.placed"

    order_id: UUID
    customer_id: UUID
    items: list[dict[str, Any]]
    total_cents: int


class OrderConfirmed(DomainEvent):
    event_name: ClassVar[str] = "order.confirmed"

    order_id: UUID


class OrderCancelled(DomainEvent):
    event_name: ClassVar[str] = "order.cancelled"

    order_id: UUID
    reason: str


# ---------------------------------------------------------------------------
# Integration event (published via outbox to other services)
# ---------------------------------------------------------------------------


class OrderPlacedIntegration(IntegrationEvent):
    event_name: ClassVar[str] = "order.placed"
    event_version: ClassVar[int] = 1

    order_id: UUID
    customer_id: UUID
    total_cents: int


# ---------------------------------------------------------------------------
# Value object
# ---------------------------------------------------------------------------


class OrderItem:
    def __init__(self, sku: str, quantity: int, unit_price_cents: int) -> None:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if unit_price_cents <= 0:
            raise ValueError("unit_price_cents must be positive")
        self.sku = sku
        self.quantity = quantity
        self.unit_price_cents = unit_price_cents

    def to_dict(self) -> dict[str, Any]:
        return {
            "sku": self.sku,
            "quantity": self.quantity,
            "unit_price_cents": self.unit_price_cents,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> OrderItem:
        return cls(sku=d["sku"], quantity=d["quantity"], unit_price_cents=d["unit_price_cents"])


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------


class Order(EventSourcedAggregate[UUID]):
    """Event-sourced order aggregate."""

    customer_id: UUID = field(default_factory=uuid4)
    items: list[dict[str, Any]] = field(default_factory=list)
    total_cents: int = field(default=0)
    status: str = field(default=OrderStatus.PENDING)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def place(
        cls,
        *,
        customer_id: UUID,
        items: list[OrderItem],
        total_cents: int,
    ) -> Result[Order]:
        if not items:
            return Result.failure(
                DomainError(
                    code="order.empty_items", message="An order must have at least one item."
                ),
            )
        if total_cents <= 0:
            return Result.failure(
                DomainError(code="order.invalid_total", message="Order total must be positive."),
            )

        order_id = uuid4()
        order = cls(id=order_id)
        order.record_event(
            OrderPlaced(
                order_id=order_id,
                customer_id=customer_id,
                items=[item.to_dict() for item in items],
                total_cents=total_cents,
            ),
        )
        return Result.success(order)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def confirm(self) -> Result[None]:
        if self.status != OrderStatus.PENDING:
            return Result.failure(
                DomainError(
                    code="order.not_pending",
                    message=f"Cannot confirm an order in status '{self.status}'.",
                ),
            )
        self.record_event(OrderConfirmed(order_id=self.id))
        return Result.success(None)

    def cancel(self, reason: str) -> Result[None]:
        if self.status == OrderStatus.CANCELLED:
            return Result.failure(
                DomainError(code="order.already_cancelled", message="Order is already cancelled."),
            )
        self.record_event(OrderCancelled(order_id=self.id, reason=reason))
        return Result.success(None)

    # ------------------------------------------------------------------
    # Apply methods — the ONLY state mutators
    # ------------------------------------------------------------------

    def apply_orderplaced(self, ev: OrderPlaced) -> None:
        self.customer_id = ev.customer_id
        self.items = ev.items
        self.total_cents = ev.total_cents
        self.status = OrderStatus.PENDING

    def apply_orderconfirmed(self, ev: OrderConfirmed) -> None:
        self.status = OrderStatus.CONFIRMED

    def apply_ordercancelled(self, ev: OrderCancelled) -> None:
        self.status = OrderStatus.CANCELLED
