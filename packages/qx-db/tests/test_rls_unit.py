"""RlsPolicyManager and open_rls_session unit tests — no real DB."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call

import pytest
from qx.db.tenancy import RlsPolicyManager, open_rls_session

TENANT_ID = "12345678-1234-5678-1234-567812345678"
TABLE = "users"
DEFAULT_POLICY = f"qx_tenant_{TABLE}"
DEFAULT_SETTING = "app.current_tenant_id"


# ---- RlsPolicyManager.enable ----


@pytest.mark.anyio
async def test_enable_runs_alter_enable_rls() -> None:
    conn = AsyncMock()
    await RlsPolicyManager().enable(conn, TABLE)
    assert any("ENABLE ROW LEVEL SECURITY" in _sql(c) for c in conn.execute.call_args_list)


@pytest.mark.anyio
async def test_enable_runs_force_row_level_security() -> None:
    conn = AsyncMock()
    await RlsPolicyManager().enable(conn, TABLE)
    assert any("FORCE ROW LEVEL SECURITY" in _sql(c) for c in conn.execute.call_args_list)


@pytest.mark.anyio
async def test_enable_drops_existing_policy_before_create() -> None:
    conn = AsyncMock()
    await RlsPolicyManager().enable(conn, TABLE)
    sqls = [_sql(c) for c in conn.execute.call_args_list]
    drop_idx = next(i for i, s in enumerate(sqls) if "DROP POLICY" in s)
    create_idx = next(i for i, s in enumerate(sqls) if "CREATE POLICY" in s)
    assert drop_idx < create_idx


@pytest.mark.anyio
async def test_enable_creates_policy_with_default_setting() -> None:
    conn = AsyncMock()
    await RlsPolicyManager().enable(conn, TABLE)
    create_sql = next(_sql(c) for c in conn.execute.call_args_list if "CREATE POLICY" in _sql(c))
    assert DEFAULT_SETTING in create_sql
    assert "current_setting" in create_sql
    assert "::uuid" in create_sql


@pytest.mark.anyio
async def test_enable_uses_custom_policy_name() -> None:
    conn = AsyncMock()
    await RlsPolicyManager().enable(conn, TABLE, policy_name="my_policy")
    assert any("my_policy" in _sql(c) for c in conn.execute.call_args_list)


@pytest.mark.anyio
async def test_enable_uses_custom_column() -> None:
    conn = AsyncMock()
    await RlsPolicyManager().enable(conn, TABLE, column="org_id")
    create_sql = next(_sql(c) for c in conn.execute.call_args_list if "CREATE POLICY" in _sql(c))
    assert "org_id" in create_sql


@pytest.mark.anyio
async def test_enable_uses_custom_setting_name() -> None:
    conn = AsyncMock()
    await RlsPolicyManager().enable(conn, TABLE, setting_name="app.org_id")
    create_sql = next(_sql(c) for c in conn.execute.call_args_list if "CREATE POLICY" in _sql(c))
    assert "app.org_id" in create_sql


# ---- RlsPolicyManager.disable ----


@pytest.mark.anyio
async def test_disable_drops_policy_and_disables_rls() -> None:
    conn = AsyncMock()
    await RlsPolicyManager().disable(conn, TABLE)
    sqls = [_sql(c) for c in conn.execute.call_args_list]
    assert any("DROP POLICY IF EXISTS" in s for s in sqls)
    assert any("DISABLE ROW LEVEL SECURITY" in s for s in sqls)


@pytest.mark.anyio
async def test_disable_uses_default_policy_name() -> None:
    conn = AsyncMock()
    await RlsPolicyManager().disable(conn, TABLE)
    assert any(DEFAULT_POLICY in _sql(c) for c in conn.execute.call_args_list)


@pytest.mark.anyio
async def test_disable_uses_custom_policy_name() -> None:
    conn = AsyncMock()
    await RlsPolicyManager().disable(conn, TABLE, policy_name="my_policy")
    assert any("my_policy" in _sql(c) for c in conn.execute.call_args_list)


# ---- open_rls_session ----


@pytest.mark.anyio
async def test_open_rls_session_calls_set_config() -> None:
    session = AsyncMock()
    factory = MagicMock(return_value=session)

    async with open_rls_session(factory, TENANT_ID) as s:
        assert s is session

    sqls = [_sql(c) for c in session.execute.call_args_list]
    assert any("set_config" in s for s in sqls)


@pytest.mark.anyio
async def test_open_rls_session_passes_tenant_id_as_param() -> None:
    session = AsyncMock()
    factory = MagicMock(return_value=session)

    async with open_rls_session(factory, TENANT_ID) as _:
        pass

    params = session.execute.call_args_list[0].args[1]
    assert params["val"] == TENANT_ID


@pytest.mark.anyio
async def test_open_rls_session_uses_default_setting_name() -> None:
    session = AsyncMock()
    factory = MagicMock(return_value=session)

    async with open_rls_session(factory, TENANT_ID) as _:
        pass

    params = session.execute.call_args_list[0].args[1]
    assert params["key"] == DEFAULT_SETTING


@pytest.mark.anyio
async def test_open_rls_session_uses_custom_setting_name() -> None:
    session = AsyncMock()
    factory = MagicMock(return_value=session)

    async with open_rls_session(factory, TENANT_ID, setting_name="app.org_id") as _:
        pass

    params = session.execute.call_args_list[0].args[1]
    assert params["key"] == "app.org_id"


@pytest.mark.anyio
async def test_open_rls_session_sets_is_local_true() -> None:
    session = AsyncMock()
    factory = MagicMock(return_value=session)

    async with open_rls_session(factory, TENANT_ID) as _:
        pass

    sql = _sql(session.execute.call_args_list[0])
    assert "true" in sql.lower()


@pytest.mark.anyio
async def test_open_rls_session_closes_on_exception() -> None:
    session = AsyncMock()
    factory = MagicMock(return_value=session)

    with pytest.raises(RuntimeError):
        async with open_rls_session(factory, TENANT_ID):
            raise RuntimeError("boom")

    session.close.assert_called_once()


# ---- helpers ----


def _sql(call_obj: call) -> str:  # type: ignore[type-arg]
    arg = call_obj.args[0] if call_obj.args else None
    return arg.text if hasattr(arg, "text") else str(arg)
