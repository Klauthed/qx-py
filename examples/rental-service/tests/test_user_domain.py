"""Unit tests for the User aggregate — no infrastructure required."""

from __future__ import annotations

from rental_service.domain.user import User, UserRegistered


def test_register_user_success() -> None:
    result = User.register("ada@example.com", "Ada Lovelace")
    assert result.is_success
    user = result.value
    assert user.email == "ada@example.com"
    assert user.name == "Ada Lovelace"


def test_register_user_invalid_email() -> None:
    result = User.register("not-an-email", "Ada")
    assert result.is_failure
    assert result.error.code == "user.invalid_email"


def test_register_user_empty_name() -> None:
    result = User.register("ada@example.com", "  ")
    assert result.is_failure
    assert result.error.code == "user.invalid_name"


def test_register_user_records_domain_event() -> None:
    user = User.register("ada@example.com", "Ada").unwrap_or_raise()
    events = user.pull_events()
    assert len(events) == 1
    assert isinstance(events[0], UserRegistered)
    assert events[0].email == "ada@example.com"
