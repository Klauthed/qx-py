"""Rental aggregate."""

from __future__ import annotations

from datetime import date
from typing import ClassVar
from uuid import UUID

from qx.core import AggregateRoot, DomainError, DomainEvent, Identifier, Result, aggregate


class RentalCreated(DomainEvent):
    event_name: ClassVar[str] = "rental.rent.created"

    rental_id: UUID
    user_id: UUID
    house_id: UUID
    total_cents: int


@aggregate
class Rental(AggregateRoot[Identifier]):
    """A booking of a house by a user."""

    user_id: UUID = UUID(int=0)
    house_id: UUID = UUID(int=0)
    check_in: date = date.min
    check_out: date = date.min
    total_cents: int = 0
    status: str = "pending"

    @classmethod
    def create(
        cls,
        user_id: UUID,
        house_id: UUID,
        check_in: date,
        check_out: date,
        price_per_night_cents: int,
    ) -> Result[Rental]:
        if check_out <= check_in:
            return Result.failure(
                DomainError(code="rental.invalid_dates", message="check_out must be after check_in")
            )
        nights = (check_out - check_in).days
        total_cents = nights * price_per_night_cents
        rental = cls(
            id=Identifier.new(),
            user_id=user_id,
            house_id=house_id,
            check_in=check_in,
            check_out=check_out,
            total_cents=total_cents,
            status="pending",
        )
        rental.record_event(
            RentalCreated(
                rental_id=rental.id.value,
                user_id=user_id,
                house_id=house_id,
                total_cents=total_cents,
            )
        )
        return Result.success(rental)
