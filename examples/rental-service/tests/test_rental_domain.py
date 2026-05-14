"""Unit tests for the Rental aggregate."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from rental_service.domain.rental import Rental, RentalCreated


def test_create_rental_success() -> None:
    user_id = uuid4()
    house_id = uuid4()
    check_in = date(2026, 7, 1)
    check_out = date(2026, 7, 5)

    result = Rental.create(
        user_id=user_id,
        house_id=house_id,
        check_in=check_in,
        check_out=check_out,
        price_per_night_cents=15000,
    )
    assert result.is_success
    rental = result.value
    assert rental.total_cents == 4 * 15000
    assert rental.status == "pending"


def test_create_rental_invalid_dates() -> None:
    user_id = uuid4()
    house_id = uuid4()
    result = Rental.create(
        user_id=user_id,
        house_id=house_id,
        check_in=date(2026, 7, 5),
        check_out=date(2026, 7, 1),
        price_per_night_cents=15000,
    )
    assert result.is_failure
    assert result.error.code == "rental.invalid_dates"


def test_create_rental_records_domain_event() -> None:
    rental = Rental.create(
        user_id=uuid4(),
        house_id=uuid4(),
        check_in=date(2026, 7, 1),
        check_out=date(2026, 7, 3),
        price_per_night_cents=10000,
    ).unwrap_or_raise()
    events = rental.pull_events()
    assert len(events) == 1
    assert isinstance(events[0], RentalCreated)
    assert events[0].total_cents == 20000
