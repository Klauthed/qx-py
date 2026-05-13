"""OutboxRelay sharding unit tests — no real DB or broker."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from qx.events import OutboxRelay

# ---- construction / validation ----


def test_default_is_single_shard() -> None:
    relay = OutboxRelay(_mock_engine(), MagicMock())
    assert relay._shard_id == 0
    assert relay._shard_count == 1


def test_valid_shard_configuration() -> None:
    relay = OutboxRelay(_mock_engine(), MagicMock(), shard_id=2, shard_count=3)
    assert relay._shard_id == 2
    assert relay._shard_count == 3


def test_shard_count_zero_raises() -> None:
    with pytest.raises(ValueError, match="shard_count must be >= 1"):
        OutboxRelay(_mock_engine(), MagicMock(), shard_count=0)


def test_shard_id_equal_to_shard_count_raises() -> None:
    with pytest.raises(ValueError, match="shard_id must be in"):
        OutboxRelay(_mock_engine(), MagicMock(), shard_id=3, shard_count=3)


def test_shard_id_negative_raises() -> None:
    with pytest.raises(ValueError, match="shard_id must be in"):
        OutboxRelay(_mock_engine(), MagicMock(), shard_id=-1, shard_count=3)


# ---- shard clause generation ----


def test_single_shard_produces_no_clause() -> None:
    relay = OutboxRelay(_mock_engine(), MagicMock(), shard_id=0, shard_count=1)
    assert relay._shard_clause() == ""


def test_multi_shard_produces_hashtext_clause() -> None:
    relay = OutboxRelay(_mock_engine(), MagicMock(), shard_id=1, shard_count=4)
    clause = relay._shard_clause()
    assert "HASHTEXT" in clause
    assert "shard_count" in clause
    assert "shard_id" in clause


# ---- batch query SQL ----


@pytest.mark.anyio
async def test_single_shard_batch_query_has_no_hashtext() -> None:
    conn, engine = _mock_conn_engine()
    relay = OutboxRelay(engine, MagicMock(), shard_id=0, shard_count=1)

    await relay._process_one_batch()

    sql = _first_select_sql(conn)
    assert "HASHTEXT" not in sql


@pytest.mark.anyio
async def test_multi_shard_batch_query_includes_hashtext_filter() -> None:
    conn, engine = _mock_conn_engine()
    relay = OutboxRelay(engine, MagicMock(), shard_id=2, shard_count=5)

    await relay._process_one_batch()

    sql = _first_select_sql(conn)
    assert "HASHTEXT" in sql
    assert "shard_count" in sql
    assert "shard_id" in sql


@pytest.mark.anyio
async def test_batch_query_params_include_shard_values() -> None:
    conn, engine = _mock_conn_engine()
    relay = OutboxRelay(engine, MagicMock(), shard_id=1, shard_count=3)

    await relay._process_one_batch()

    params = conn.execute.call_args_list[0].args[1]
    assert params["shard_count"] == 3
    assert params["shard_id"] == 1


@pytest.mark.anyio
async def test_batch_query_always_has_skip_locked() -> None:
    conn, engine = _mock_conn_engine()
    relay = OutboxRelay(engine, MagicMock(), shard_id=0, shard_count=4)

    await relay._process_one_batch()

    sql = _first_select_sql(conn)
    assert "FOR UPDATE SKIP LOCKED" in sql


@pytest.mark.anyio
async def test_returns_zero_when_no_rows() -> None:
    _, engine = _mock_conn_engine()
    relay = OutboxRelay(engine, MagicMock())

    result = await relay._process_one_batch()

    assert result == 0


# ---- helpers ----


def _mock_engine() -> MagicMock:
    conn = AsyncMock()
    mock_result = MagicMock()
    mock_result.mappings.return_value = []
    conn.execute = AsyncMock(return_value=mock_result)

    @asynccontextmanager
    async def _begin():
        yield conn

    engine = MagicMock()
    engine.begin = MagicMock(return_value=_begin())
    return engine


def _mock_conn_engine() -> tuple[AsyncMock, MagicMock]:
    conn = AsyncMock()
    mock_result = MagicMock()
    mock_result.mappings.return_value = []
    conn.execute = AsyncMock(return_value=mock_result)

    @asynccontextmanager
    async def _begin():
        yield conn

    engine = MagicMock()
    engine.begin = MagicMock(return_value=_begin())
    return conn, engine


def _first_select_sql(conn: AsyncMock) -> str:
    arg = conn.execute.call_args_list[0].args[0]
    return arg.text if hasattr(arg, "text") else str(arg)
