"""Projection unit tests — no database required."""

from __future__ import annotations

from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from qx.core import DomainEvent
from qx.eventstore.table import include_eventstore_tables
from qx.projections import Projection, ProjectionRunner
from qx.projections.table import include_projection_tables
from sqlalchemy import MetaData

# ---- Domain fixtures ----


class MoneyDeposited(DomainEvent):
    event_name: ClassVar[str] = "account.money_deposited"
    amount: int


class MoneyWithdrawn(DomainEvent):
    event_name: ClassVar[str] = "account.money_withdrawn"
    amount: int


class BalanceProjection(Projection):
    name = "account_balance"

    def __init__(self) -> None:
        self.ledger: dict[str, int] = {}
        self.reset_called = False

    async def on_moneydeposited(self, ev: MoneyDeposited) -> None:
        self.ledger["balance"] = self.ledger.get("balance", 0) + ev.amount

    async def on_moneywithdrawn(self, ev: MoneyWithdrawn) -> None:
        self.ledger["balance"] = self.ledger.get("balance", 0) - ev.amount

    async def reset(self) -> None:
        self.ledger.clear()
        self.reset_called = True


# ---- Projection.apply dispatch ----


async def test_apply_dispatches_to_handler() -> None:
    proj = BalanceProjection()
    await proj.apply(MoneyDeposited(amount=100), aggregate_id="acc-1")
    assert proj.ledger["balance"] == 100


async def test_apply_accumulates_multiple_events() -> None:
    proj = BalanceProjection()
    await proj.apply(MoneyDeposited(amount=100), aggregate_id="acc-1")
    await proj.apply(MoneyWithdrawn(amount=30), aggregate_id="acc-1")
    assert proj.ledger["balance"] == 70


async def test_apply_unknown_event_is_noop() -> None:
    class OtherEvent(DomainEvent):
        event_name: ClassVar[str] = "other"

    proj = BalanceProjection()
    await proj.apply(OtherEvent(), aggregate_id="acc-1")
    assert proj.ledger == {}


async def test_reset_clears_state() -> None:
    proj = BalanceProjection()
    await proj.apply(MoneyDeposited(amount=50), aggregate_id="acc-1")
    await proj.reset()
    assert proj.ledger == {}
    assert proj.reset_called


# ---- ProjectionRunner.run_once ----


async def test_run_once_applies_new_events() -> None:
    proj = BalanceProjection()
    event_rows = [
        _event_row(seq=1, event_cls=MoneyDeposited, payload={"amount": 100}),
        _event_row(seq=2, event_cls=MoneyWithdrawn, payload={"amount": 40}),
    ]
    session = _session_no_checkpoint(event_rows=event_rows)
    runner = _make_runner(session)
    runner.register(proj)

    count = await runner.run_once(session)

    assert count == 2
    assert proj.ledger["balance"] == 60


async def test_run_once_returns_zero_when_no_new_events() -> None:
    proj = BalanceProjection()
    session = _session_no_checkpoint(event_rows=[])
    runner = _make_runner(session)
    runner.register(proj)

    count = await runner.run_once(session)

    assert count == 0


async def test_run_once_updates_checkpoint() -> None:
    proj = BalanceProjection()
    event_rows = [_event_row(seq=5, event_cls=MoneyDeposited, payload={"amount": 10})]
    session = _session_no_checkpoint(event_rows=event_rows)
    runner = _make_runner(session)
    runner.register(proj)

    await runner.run_once(session)

    assert session.execute.await_count >= 2  # get_checkpoint + event query + set_checkpoint


async def test_run_once_uses_existing_checkpoint() -> None:
    proj = BalanceProjection()
    event_rows = [_event_row(seq=11, event_cls=MoneyDeposited, payload={"amount": 50})]
    # checkpoint at 10 — only events after seq 10 returned
    session = _session_with_checkpoint(checkpoint=10, event_rows=event_rows)
    runner = _make_runner(session)
    runner.register(proj)

    count = await runner.run_once(session)

    assert count == 1
    assert proj.ledger["balance"] == 50


# ---- ProjectionRunner.rebuild ----


async def test_rebuild_resets_projection_and_replays_all() -> None:
    proj = BalanceProjection()
    proj.ledger["balance"] = 999  # stale state
    event_rows = [
        _event_row(seq=1, event_cls=MoneyDeposited, payload={"amount": 200}),
    ]
    session = _session_no_checkpoint(event_rows=event_rows)
    runner = _make_runner(session)
    runner.register(proj)

    count = await runner.rebuild(session, projection_name="account_balance")

    assert proj.reset_called
    assert count == 1
    assert proj.ledger["balance"] == 200


async def test_rebuild_raises_for_unknown_projection() -> None:
    session = AsyncMock()
    runner = _make_runner(session)

    try:
        await runner.rebuild(session, projection_name="does_not_exist")
        raise AssertionError("expected KeyError")
    except KeyError:
        pass


# ---- Helpers ----


def _make_tables() -> tuple:
    events_t, _ = include_eventstore_tables(MetaData())
    checkpoints_t = include_projection_tables(MetaData())
    return checkpoints_t, events_t


def _make_runner(session: AsyncMock) -> ProjectionRunner:
    checkpoints_t, events_t = _make_tables()
    return ProjectionRunner(checkpoints_t, events_t)


def _event_row(*, seq: int, event_cls: type, payload: dict) -> dict:
    return {
        "id": uuid4(),
        "aggregate_type": "test.BankAccount",
        "aggregate_id": "acc-1",
        "sequence": seq,
        "event_type": f"{event_cls.__module__}.{event_cls.__qualname__}",
        "event_name": event_cls.event_name,
        "payload": payload,
    }


def _mapping_result(row: dict | None) -> MagicMock:
    result = MagicMock()
    result.mappings.return_value.first.return_value = row
    return result


def _mapping_list(rows: list[dict]) -> MagicMock:
    result = MagicMock()
    result.mappings.return_value.__iter__ = MagicMock(return_value=iter(rows))
    return result


def _session_no_checkpoint(*, event_rows: list[dict]) -> AsyncMock:
    """Session with no existing checkpoint (sequence=0) and given event rows."""
    session = AsyncMock()
    session.execute.side_effect = [
        _mapping_result(None),  # get_checkpoint → no row
        _mapping_list(event_rows),  # event query
        AsyncMock(),  # set_checkpoint upsert
    ]
    return session


def _session_with_checkpoint(*, checkpoint: int, event_rows: list[dict]) -> AsyncMock:
    """Session with an existing checkpoint row and given event rows."""
    session = AsyncMock()
    checkpoint_row = {"projection_name": "account_balance", "last_sequence": checkpoint}
    session.execute.side_effect = [
        _mapping_result(checkpoint_row),  # get_checkpoint
        _mapping_list(event_rows),  # event query
        AsyncMock(),  # set_checkpoint upsert
    ]
    return session
