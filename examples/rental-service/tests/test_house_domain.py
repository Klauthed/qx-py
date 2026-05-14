"""Unit tests for the House aggregate."""

from __future__ import annotations

from rental_service.domain.house import House, HouseListingCreated


def test_create_listing_success() -> None:
    result = House.create_listing("123 Maple St", 15000)
    assert result.is_success
    house = result.value
    assert house.address == "123 Maple St"
    assert house.price_per_night_cents == 15000
    assert house.available is True


def test_create_listing_empty_address() -> None:
    result = House.create_listing("", 15000)
    assert result.is_failure
    assert result.error.code == "house.invalid_address"


def test_create_listing_zero_price() -> None:
    result = House.create_listing("123 Maple St", 0)
    assert result.is_failure
    assert result.error.code == "house.invalid_price"


def test_create_listing_records_domain_event() -> None:
    house = House.create_listing("123 Maple St", 15000).unwrap_or_raise()
    events = house.pull_events()
    assert len(events) == 1
    assert isinstance(events[0], HouseListingCreated)
    assert events[0].price_per_night_cents == 15000
