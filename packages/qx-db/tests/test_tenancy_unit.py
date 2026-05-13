"""TenantSchemaManager and open_schema_session unit tests — no real DB."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from qx.db.tenancy import TenantSchemaManager, open_schema_session
from sqlalchemy import MetaData

TENANT_ID = UUID("12345678-1234-5678-1234-567812345678")
SCHEMA = "tenant_12345678123456781234567812345678"


# ---- schema_name ----


def test_schema_name_strips_hyphens() -> None:
    manager = TenantSchemaManager(MagicMock(), MetaData())
    assert manager.schema_name(TENANT_ID) == SCHEMA


def test_schema_name_accepts_string() -> None:
    manager = TenantSchemaManager(MagicMock(), MetaData())
    assert manager.schema_name("12345678-1234-5678-1234-567812345678") == SCHEMA


def test_schema_name_custom_prefix() -> None:
    manager = TenantSchemaManager(MagicMock(), MetaData(), prefix="svc_")
    assert manager.schema_name(TENANT_ID) == f"svc_{str(TENANT_ID).replace('-', '')}"


# ---- provision ----


@pytest.mark.anyio
async def test_provision_creates_schema_and_tables() -> None:
    conn = AsyncMock()
    conn.run_sync = AsyncMock()
    engine = _mock_engine_begin(conn)
    metadata = MagicMock()

    await TenantSchemaManager(engine, metadata).provision(TENANT_ID)

    sql_calls = _sql_texts(conn.execute)
    assert any("CREATE SCHEMA IF NOT EXISTS" in c and SCHEMA in c for c in sql_calls)
    assert any("SET LOCAL search_path" in c and SCHEMA in c for c in sql_calls)
    conn.run_sync.assert_called_once_with(metadata.create_all)


@pytest.mark.anyio
async def test_provision_is_idempotent_by_using_if_not_exists() -> None:
    conn = AsyncMock()
    conn.run_sync = AsyncMock()
    engine = _mock_engine_begin(conn)

    await TenantSchemaManager(engine, MetaData()).provision(TENANT_ID)

    first_call = _sql_texts(conn.execute)[0]
    assert "IF NOT EXISTS" in first_call


# ---- drop ----


@pytest.mark.anyio
async def test_drop_executes_drop_schema_cascade() -> None:
    conn = AsyncMock()
    engine = _mock_engine_begin(conn)

    await TenantSchemaManager(engine, MetaData()).drop(TENANT_ID)

    drop_call = _sql_texts(conn.execute)[0]
    assert "DROP SCHEMA IF EXISTS" in drop_call
    assert SCHEMA in drop_call
    assert "CASCADE" in drop_call


# ---- list_schemas ----


@pytest.mark.anyio
async def test_list_schemas_returns_matching_schemas() -> None:
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=[("tenant_abc",), ("tenant_def",)])
    engine = _mock_engine_connect(conn)

    schemas = await TenantSchemaManager(engine, MetaData()).list_schemas()

    assert schemas == ["tenant_abc", "tenant_def"]


@pytest.mark.anyio
async def test_list_schemas_uses_prefix_in_query() -> None:
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=[])
    engine = _mock_engine_connect(conn)

    await TenantSchemaManager(engine, MetaData(), prefix="svc_").list_schemas()

    params = conn.execute.call_args_list[0].args[1]
    assert params["prefix"] == "svc_%"


# ---- open_schema_session ----


@pytest.mark.anyio
async def test_open_schema_session_sets_and_resets_search_path() -> None:
    session = AsyncMock()
    factory = MagicMock(return_value=session)

    async with open_schema_session(factory, TENANT_ID) as s:
        assert s is session

    sql_calls = _sql_texts(session.execute)
    assert any(SCHEMA in c for c in sql_calls), "schema not set in search_path"
    assert any("SET search_path TO public" in c for c in sql_calls), "search_path not reset"
    session.close.assert_called_once()


@pytest.mark.anyio
async def test_open_schema_session_resets_on_exception() -> None:
    session = AsyncMock()
    factory = MagicMock(return_value=session)

    with pytest.raises(RuntimeError):
        async with open_schema_session(factory, TENANT_ID):
            raise RuntimeError("oops")

    sql_calls = _sql_texts(session.execute)
    assert any("SET search_path TO public" in c for c in sql_calls)
    session.close.assert_called_once()


@pytest.mark.anyio
async def test_open_schema_session_custom_prefix() -> None:
    session = AsyncMock()
    factory = MagicMock(return_value=session)

    async with open_schema_session(factory, TENANT_ID, prefix="svc_") as _:
        pass

    expected_schema = f"svc_{str(TENANT_ID).replace('-', '')}"
    sql_calls = _sql_texts(session.execute)
    assert any(expected_schema in c for c in sql_calls)


# ---- helpers ----


def _sql_texts(mock: AsyncMock) -> list[str]:
    """Extract raw SQL strings from TextClause objects in mock execute calls."""
    result = []
    for call in mock.call_args_list:
        arg = call.args[0] if call.args else None
        result.append(arg.text if hasattr(arg, "text") else str(arg))
    return result


def _mock_engine_begin(conn: AsyncMock) -> MagicMock:
    @asynccontextmanager
    async def _begin():
        yield conn

    engine = MagicMock()
    engine.begin = MagicMock(return_value=_begin())
    return engine


def _mock_engine_connect(conn: AsyncMock) -> MagicMock:
    @asynccontextmanager
    async def _connect():
        yield conn

    engine = MagicMock()
    engine.connect = MagicMock(return_value=_connect())
    return engine
