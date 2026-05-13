"""Advisory lock unit tests — no database required."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from qx.db.locking import advisory_key, advisory_lock, advisory_xact_lock

# ---------------------------------------------------------------------------
# advisory_key
# ---------------------------------------------------------------------------


def test_advisory_key_is_deterministic() -> None:
    assert advisory_key("my-lock") == advisory_key("my-lock")


def test_advisory_key_differs_for_different_names() -> None:
    assert advisory_key("lock-a") != advisory_key("lock-b")


def test_advisory_key_fits_in_signed_bigint() -> None:
    key = advisory_key("any-string")
    assert -(2**63) <= key < 2**63


def test_advisory_key_handles_unicode() -> None:
    key = advisory_key("lock-ÄÖÜ")
    assert isinstance(key, int)


# ---------------------------------------------------------------------------
# advisory_lock (session-level)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_advisory_lock_acquires_and_releases() -> None:
    session = AsyncMock()
    key = 12345

    async with advisory_lock(session, key):
        pass

    calls = session.execute.call_args_list
    assert len(calls) == 2
    acquire_sql = str(calls[0][0][0])
    release_sql = str(calls[1][0][0])
    assert "pg_advisory_lock" in acquire_sql
    assert "pg_advisory_unlock" in release_sql


@pytest.mark.asyncio
async def test_advisory_lock_releases_on_exception() -> None:
    session = AsyncMock()
    key = 42

    with pytest.raises(RuntimeError):
        async with advisory_lock(session, key):
            raise RuntimeError("boom inside lock")

    calls = session.execute.call_args_list
    # Both acquire and release must have been called
    assert len(calls) == 2
    assert "pg_advisory_unlock" in str(calls[1][0][0])


@pytest.mark.asyncio
async def test_advisory_lock_suppresses_release_error() -> None:
    """Release failure (e.g., session closed) does not mask the original exception."""
    session = AsyncMock()
    session.execute.side_effect = [None, RuntimeError("release failed")]

    # Should not raise even though unlock fails
    async with advisory_lock(session, 99):
        pass


# ---------------------------------------------------------------------------
# advisory_xact_lock (transaction-level)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_advisory_xact_lock_calls_xact_variant() -> None:
    session = AsyncMock()
    key = 777

    async with advisory_xact_lock(session, key):
        pass

    calls = session.execute.call_args_list
    assert len(calls) == 1
    sql = str(calls[0][0][0])
    assert "pg_advisory_xact_lock" in sql


@pytest.mark.asyncio
async def test_advisory_xact_lock_does_not_call_unlock() -> None:
    """Transaction-level lock auto-releases — no explicit unlock call."""
    session = AsyncMock()

    async with advisory_xact_lock(session, 888):
        pass

    calls = session.execute.call_args_list
    assert len(calls) == 1
    # Confirm there is no pg_advisory_unlock call
    assert all("unlock" not in str(c[0][0]) for c in calls)
