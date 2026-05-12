"""Tests for Entity, AggregateRoot, ValueObject."""

from __future__ import annotations

from typing import ClassVar
from uuid import UUID, uuid4

import pytest
from qx.core import AggregateRoot, DomainEvent, ValueObject, aggregate


@aggregate
class _User(AggregateRoot[UUID]):
    email: str = ""


class _UserCreated(DomainEvent):
    event_name: ClassVar[str] = "user.created"
    email: str = ""


class _Email(ValueObject):
    value: str


def test_entities_equal_by_id_only() -> None:
    uid = uuid4()
    u1 = _User(id=uid, email="a@x")
    u2 = _User(id=uid, email="b@x")
    assert u1 == u2


def test_entities_differ_by_id() -> None:
    u1 = _User(id=uuid4())
    u2 = _User(id=uuid4())
    assert u1 != u2


def test_entity_starts_at_version_1() -> None:
    u = _User(id=uuid4())
    assert u.version == 1


def test_soft_delete_marks_fields() -> None:
    u = _User(id=uuid4())
    actor = uuid4()
    u.mark_deleted(by=actor)
    assert u.is_deleted
    assert u.deleted_by == actor
    assert u.deleted_at is not None


def test_aggregate_records_and_drains_events() -> None:
    u = _User(id=uuid4())
    u.record_event(_UserCreated(email="a@x"))
    u.record_event(_UserCreated(email="b@x"))
    assert u.has_pending_events
    events = u.pull_events()
    assert len(events) == 2
    assert not u.has_pending_events


def test_value_objects_are_equal_by_attributes() -> None:
    e1 = _Email(value="x@x")
    e2 = _Email(value="x@x")
    assert e1 == e2


def test_value_objects_are_frozen() -> None:
    e = _Email(value="x@x")
    with pytest.raises(Exception):  # noqa: B017
        e.value = "y@y"  # type: ignore[misc]


def test_value_object_with_changes_returns_copy() -> None:
    e = _Email(value="x@x")
    e2 = e.with_changes(value="y@y")
    assert e.value == "x@x"
    assert e2.value == "y@y"


def test_value_object_rejects_extra_fields() -> None:
    with pytest.raises(Exception):  # noqa: B017
        _Email(value="x@x", extra="nope")  # type: ignore[call-arg]
