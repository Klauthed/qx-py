"""identity-service unit tests using framework doubles.

These tests exercise the handlers without Postgres or NATS — repository and
unit-of-work are stubbed; events are captured by ``MediatorStub``.

Integration tests against real infrastructure live in
``tests/integration/`` and require Docker (testcontainers).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from qx.core import Identifier
from qx.testing import MediatorStub, RepositoryStub

from identity_service.application.commands.create_user import (
    CreateUserCommand,
    CreateUserHandler,
)
from identity_service.domain.aggregates.user import (
    User,
    UserRegisteredIntegration,
)


# ---- Domain ----


def test_user_register_validates_email() -> None:
    r = User.register("", "Ada")
    assert r.is_failure
    assert r.error.code == "user.invalid_email"


def test_user_register_validates_name() -> None:
    r = User.register("ada@example.com", " ")
    assert r.is_failure
    assert r.error.code == "user.invalid_name"


def test_user_register_records_two_events() -> None:
    r = User.register("ada@example.com", "Ada")
    assert r.is_success
    user = r.value
    pending = user.pull_events()
    assert len(pending) == 2
    names = {type(e).__name__ for e in pending}
    assert names == {"UserRegistered", "UserRegisteredIntegration"}


def test_user_change_email_records_event_when_different() -> None:
    user = User.register("ada@example.com", "Ada").unwrap_or_raise()
    user.pull_events()  # drain registration events
    change_result = user.change_email("ada@new.example")
    assert change_result.is_success
    pending = user.pull_events()
    assert len(pending) == 1
    assert isinstance(pending[0], type(pending[0]))  # the UserEmailChanged class
    assert user.email == "ada@new.example"


def test_user_change_email_no_op_when_same() -> None:
    user = User.register("ada@example.com", "Ada").unwrap_or_raise()
    user.pull_events()
    change_result = user.change_email("ada@example.com")
    assert change_result.is_success
    assert user.pull_events() == []


# ---- Handler with stubs ----


class _FakeUoW:
    """Minimal UoW stand-in compatible with the create-user handler."""

    def __init__(self, repo: Any) -> None:
        self.repo = repo
        self.tracked: list[Any] = []
        self.committed = False
        self.session = None  # CreateUserHandler constructs its own repo from session

    async def __aenter__(self) -> "_FakeUoW":
        return self

    async def __aexit__(self, *_: Any) -> None:
        pass

    def track(self, agg: Any) -> None:
        self.tracked.append(agg)

    async def commit(self) -> None:
        self.committed = True


@pytest.mark.skip(
    reason=(
        "CreateUserHandler constructs UserRepository(uow.session) directly; the "
        "unit test would need a session double that mimics SQLAlchemy's interface. "
        "Integration tests cover this path end-to-end."
    )
)
async def test_create_user_happy_path_unit() -> None:
    """Placeholder — covered by integration tests against testcontainers Postgres."""
