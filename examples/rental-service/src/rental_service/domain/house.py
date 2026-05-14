"""House aggregate."""

from __future__ import annotations

from typing import ClassVar
from uuid import UUID  # noqa: TC003

from qx.core import AggregateRoot, DomainError, DomainEvent, Identifier, Result, aggregate


class HouseListingCreated(DomainEvent):
    event_name: ClassVar[str] = "rental.house.listing_created"

    house_id: UUID
    address: str
    price_per_night_cents: int


@aggregate
class House(AggregateRoot[Identifier]):
    """A property available for rental."""

    address: str = ""
    price_per_night_cents: int = 0
    available: bool = True

    @classmethod
    def create_listing(cls, address: str, price_per_night_cents: int) -> Result[House]:
        if not address.strip():
            return Result.failure(
                DomainError(code="house.invalid_address", message="address is required")
            )
        if price_per_night_cents <= 0:
            return Result.failure(
                DomainError(code="house.invalid_price", message="price must be positive")
            )
        house = cls(
            id=Identifier.new(),
            address=address.strip(),
            price_per_night_cents=price_per_night_cents,
            available=True,
        )
        house.record_event(
            HouseListingCreated(
                house_id=house.id.value,
                address=house.address,
                price_per_night_cents=house.price_per_night_cents,
            )
        )
        return Result.success(house)
