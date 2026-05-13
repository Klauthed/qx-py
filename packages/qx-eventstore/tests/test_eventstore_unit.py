"""EventStore unit tests — no database required."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from qx.core import DomainEvent
from qx.eventstore import EventSourcedAggregate, EventStore
from qx.eventstore.table import include_eventstore_tables
from sqlalchemy import MetaData

# ---- Domain fixtures ----


class MoneyDeposited(DomainEvent):
    event_name: ClassVar[str] = "account.money_deposited"
    amount: int


class MoneyWithdrawn(DomainEvent):
    event_name: ClassVar[str] = "account.money_withdrawn"
    amount: int


@dataclass
class BankAccount(EventSourcedAggregate[str]):
    balance: int = field(default=0)

    def deposit(self, amount: int) -> None:
        self.record_event(MoneyDeposited(amount=amount))

    def withdraw(self, amount: int) -> None:
        self.record_event(MoneyWithdrawn(amount=amount))

    def apply_moneydeposited(self, ev: MoneyDeposited) -> None:
        self.balance += ev.amount

    def apply_moneywithdrawn(self, ev: MoneyWithdrawn) -> None:
        self.balance -= ev.amount


# ---- EventSourcedAggregate ----


def test_record_event_buffers_and_applies() -> None:
    account = BankAccount(id="acc-1")
    account.deposit(100)

    assert account.balance == 100
    assert account.has_pending_events
    assert len(account._pending_events) == 1


def test_pull_events_drains_buffer() -> None:
    account = BankAccount(id="acc-1")
    account.deposit(50)
    account.withdraw(20)

    events = account.pull_events()

    assert len(events) == 2
    assert not account.has_pending_events


def test_replay_restores_state() -> None:
    account = BankAccount(id="acc-1")
    events = [MoneyDeposited(amount=100), MoneyWithdrawn(amount=30)]

    account._replay(events, version=2)

    assert account.balance == 70
    assert account.version == 2
    assert not account.has_pending_events


def test_from_events_constructs_aggregate() -> None:
    events = [MoneyDeposited(amount=200), MoneyWithdrawn(amount=50)]
    account = BankAccount._from_events("acc-1", events, version=2)

    assert account.balance == 150
    assert account.version == 2
    assert not account.has_pending_events


def test_unknown_event_type_is_ignored() -> None:
    class OtherEvent(DomainEvent):
        event_name: ClassVar[str] = "other"

    account = BankAccount(id="acc-1")
    account._replay([OtherEvent()], version=1)
    assert account.balance == 0  # no handler, no crash


# ---- EventStore.append ----


async def test_append_writes_rows_for_each_event() -> None:
    session = AsyncMock()
    store = _make_store(session)
    account = BankAccount(id="acc-1")
    account.deposit(100)
    account.withdraw(40)

    result = await store.append(account, aggregate_type="test.BankAccount")

    assert result.is_success
    assert result.value == 2
    assert account.version == 2
    assert not account.has_pending_events
    session.execute.assert_awaited_once()
    rows = session.execute.call_args[0][1]
    assert len(rows) == 2
    assert rows[0]["sequence"] == 1
    assert rows[1]["sequence"] == 2


async def test_append_noop_when_no_pending_events() -> None:
    session = AsyncMock()
    store = _make_store(session)
    account = BankAccount(id="acc-1")

    result = await store.append(account, aggregate_type="test.BankAccount")

    assert result.is_success
    assert result.value == 0
    session.execute.assert_not_awaited()


async def test_append_uses_base_version_as_sequence_offset() -> None:
    session = AsyncMock()
    store = _make_store(session)
    account = BankAccount(id="acc-1")
    account.version = 5
    account.deposit(10)

    await store.append(account, aggregate_type="test.BankAccount")

    rows = session.execute.call_args[0][1]
    assert rows[0]["sequence"] == 6


async def test_append_triggers_snapshot_at_threshold() -> None:
    session = AsyncMock()
    store = _make_store(session, snapshot_every=3)
    account = BankAccount(id="acc-1")
    account.version = 2
    account.deposit(10)  # sequence 3 — threshold hit

    with patch.object(store, "_save_snapshot", new=AsyncMock()) as mock_snap:
        await store.append(account, aggregate_type="test.BankAccount")

    mock_snap.assert_awaited_once()


async def test_append_no_snapshot_below_threshold() -> None:
    session = AsyncMock()
    store = _make_store(session, snapshot_every=10)
    account = BankAccount(id="acc-1")
    account.deposit(10)

    with patch.object(store, "_save_snapshot", new=AsyncMock()) as mock_snap:
        await store.append(account, aggregate_type="test.BankAccount")

    mock_snap.assert_not_awaited()


# ---- EventStore.load ----


async def test_load_returns_none_when_no_events_no_snapshot() -> None:
    session = _session_with_rows(snap_row=None, event_rows=[])
    store = _make_store(session)

    result = await store.load(BankAccount, aggregate_id="acc-1", aggregate_type="test.BankAccount")

    assert result.is_success
    assert result.value is None


async def test_load_replays_events_without_snapshot() -> None:
    event_rows = [
        _event_row(seq=1, event_cls=MoneyDeposited, payload={"amount": 100}),
        _event_row(seq=2, event_cls=MoneyWithdrawn, payload={"amount": 30}),
    ]
    session = _session_with_rows(snap_row=None, event_rows=event_rows)
    store = _make_store(session)

    result = await store.load(BankAccount, aggregate_id="acc-1", aggregate_type="test.BankAccount")

    assert result.is_success
    account = result.value
    assert account is not None
    assert account.balance == 70
    assert account.version == 2


async def test_load_uses_snapshot_and_tail_events() -> None:
    snap = {"id": "snap-uuid", "state": {"balance": 80, "id": "acc-1"}, "version": 5}
    event_rows = [_event_row(seq=6, event_cls=MoneyDeposited, payload={"amount": 20})]
    session = _session_with_rows(snap_row=snap, event_rows=event_rows)
    store = _make_store(session)

    result = await store.load(BankAccount, aggregate_id="acc-1", aggregate_type="test.BankAccount")

    assert result.is_success
    account = result.value
    assert account is not None
    assert account.balance == 100  # 80 from snapshot + 20 from tail
    assert account.version == 6


async def test_load_with_snapshot_and_no_tail_events() -> None:
    snap = {"id": "snap-uuid", "state": {"balance": 50, "id": "acc-1"}, "version": 10}
    session = _session_with_rows(snap_row=snap, event_rows=[])
    store = _make_store(session)

    result = await store.load(BankAccount, aggregate_id="acc-1", aggregate_type="test.BankAccount")

    assert result.is_success
    account = result.value
    assert account is not None
    assert account.balance == 50
    assert account.version == 10


# ---- Helpers ----


def _make_real_tables() -> tuple:
    return include_eventstore_tables(MetaData())


def _make_store(session: AsyncMock, *, snapshot_every: int = 50) -> EventStore:
    events_table, snapshots_table = _make_real_tables()
    return EventStore(session, events_table, snapshots_table, snapshot_every=snapshot_every)


def _event_row(*, seq: int, event_cls: type, payload: dict) -> dict:
    full_type = f"{event_cls.__module__}.{event_cls.__qualname__}"
    return {
        "id": uuid4(),
        "aggregate_type": "test.BankAccount",
        "aggregate_id": "acc-1",
        "sequence": seq,
        "event_type": full_type,
        "event_name": event_cls.event_name,
        "payload": payload,
    }


def _session_with_rows(
    *,
    snap_row: dict | None,
    event_rows: list[dict],
) -> AsyncMock:
    """Build a mock AsyncSession that returns snap_row then event_rows on execute calls."""
    session = AsyncMock()

    def _mapping_result(row: dict | None) -> MagicMock:
        result = MagicMock()
        result.mappings.return_value.first.return_value = row
        return result

    def _mapping_list(rows: list[dict]) -> MagicMock:
        result = MagicMock()
        result.mappings.return_value.__iter__ = MagicMock(return_value=iter(rows))
        return result

    session.execute.side_effect = [
        _mapping_result(snap_row),
        _mapping_list(event_rows),
    ]
    return session
