"""TenantDatabaseManager, TenantEngineRouter, and open_db_session unit tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from pydantic import SecretStr
from qx.db.engine import DatabaseSettings
from qx.db.tenancy import TenantDatabaseManager, TenantEngineRouter, open_db_session
from sqlalchemy import MetaData

TENANT_ID = UUID("12345678-1234-5678-1234-567812345678")
DB_NAME = "tenant_12345678123456781234567812345678"


# ---- TenantDatabaseManager.database_name ----


def test_database_name_strips_hyphens() -> None:
    mgr = _make_manager()
    assert mgr.database_name(TENANT_ID) == DB_NAME


def test_database_name_accepts_string() -> None:
    mgr = _make_manager()
    assert mgr.database_name("12345678-1234-5678-1234-567812345678") == DB_NAME


def test_database_name_custom_prefix() -> None:
    mgr = _make_manager(prefix="db_")
    assert mgr.database_name(TENANT_ID).startswith("db_")


# ---- TenantDatabaseManager.provision ----


@pytest.mark.anyio
async def test_provision_creates_database_when_not_exists() -> None:
    conn, admin_engine = _mock_autocommit_conn(exists=False)

    with patch("qx.db.tenancy.database.create_engine") as mock_ce:
        tenant_engine = _mock_engine_begin(AsyncMock())
        mock_ce.return_value = tenant_engine
        mgr = _make_manager(admin_engine=admin_engine)
        await mgr.provision(TENANT_ID)

    sqls = _sqls(conn)
    assert any("CREATE DATABASE" in s and DB_NAME in s for s in sqls)


@pytest.mark.anyio
async def test_provision_skips_create_when_database_exists() -> None:
    conn, admin_engine = _mock_autocommit_conn(exists=True)

    with patch("qx.db.tenancy.database.create_engine") as mock_ce:
        mock_ce.return_value = _mock_engine_begin(AsyncMock())
        mgr = _make_manager(admin_engine=admin_engine)
        await mgr.provision(TENANT_ID)

    sqls = _sqls(conn)
    assert not any("CREATE DATABASE" in s for s in sqls)


@pytest.mark.anyio
async def test_provision_runs_metadata_create_all() -> None:
    _, admin_engine = _mock_autocommit_conn(exists=False)
    tenant_conn = AsyncMock()
    tenant_conn.run_sync = AsyncMock()

    with patch("qx.db.tenancy.database.create_engine") as mock_ce:
        mock_ce.return_value = _mock_engine_begin(tenant_conn)
        metadata = MagicMock()
        mgr = _make_manager(admin_engine=admin_engine, metadata=metadata)
        await mgr.provision(TENANT_ID)

    tenant_conn.run_sync.assert_called_once_with(metadata.create_all)


@pytest.mark.anyio
async def test_provision_disposes_tenant_engine_after_ddl() -> None:
    _, admin_engine = _mock_autocommit_conn(exists=False)
    tenant_engine = _mock_engine_begin(AsyncMock())
    tenant_engine.dispose = AsyncMock()

    with patch("qx.db.tenancy.database.create_engine", return_value=tenant_engine):
        await _make_manager(admin_engine=admin_engine).provision(TENANT_ID)

    tenant_engine.dispose.assert_called_once()


@pytest.mark.anyio
async def test_provision_passes_template_clause() -> None:
    conn, admin_engine = _mock_autocommit_conn(exists=False)

    with patch("qx.db.tenancy.database.create_engine") as mock_ce:
        mock_ce.return_value = _mock_engine_begin(AsyncMock())
        await _make_manager(admin_engine=admin_engine).provision(TENANT_ID, template="mytemplate")

    sqls = _sqls(conn)
    assert any("TEMPLATE mytemplate" in s for s in sqls)


# ---- TenantDatabaseManager.drop ----


@pytest.mark.anyio
async def test_drop_terminates_connections_then_drops_database() -> None:
    conn, admin_engine = _mock_autocommit_conn()
    await _make_manager(admin_engine=admin_engine).drop(TENANT_ID)

    sqls = _sqls(conn)
    terminate_idx = next(i for i, s in enumerate(sqls) if "pg_terminate_backend" in s)
    drop_idx = next(i for i, s in enumerate(sqls) if "DROP DATABASE" in s)
    assert terminate_idx < drop_idx


@pytest.mark.anyio
async def test_drop_uses_if_exists() -> None:
    conn, admin_engine = _mock_autocommit_conn()
    await _make_manager(admin_engine=admin_engine).drop(TENANT_ID)

    sqls = _sqls(conn)
    assert any("DROP DATABASE IF EXISTS" in s for s in sqls)


# ---- TenantDatabaseManager.list_databases ----


@pytest.mark.anyio
async def test_list_databases_returns_matching_names() -> None:
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=[("tenant_abc",), ("tenant_def",)])
    admin_engine = _mock_engine_connect(conn)

    dbs = await _make_manager(admin_engine=admin_engine).list_databases()

    assert dbs == ["tenant_abc", "tenant_def"]


# ---- TenantEngineRouter ----


def test_router_database_name_strips_hyphens() -> None:
    router = _make_router()
    assert router.database_name(TENANT_ID) == DB_NAME


def test_router_get_engine_creates_and_caches() -> None:
    with patch("qx.db.tenancy.database.create_engine") as mock_ce:
        mock_ce.return_value = MagicMock()
        router = _make_router()
        e1 = router.get_engine(TENANT_ID)
        e2 = router.get_engine(TENANT_ID)

    assert e1 is e2
    assert mock_ce.call_count == 1


def test_router_get_engine_uses_tenant_database_in_url() -> None:
    captured_settings = {}

    def capture(settings):
        captured_settings["url"] = settings.url.get_secret_value()
        return MagicMock()

    with patch("qx.db.tenancy.database.create_engine", side_effect=capture):
        _make_router().get_engine(TENANT_ID)

    assert DB_NAME in captured_settings["url"]


def test_router_get_session_factory_creates_and_caches() -> None:
    with (
        patch("qx.db.tenancy.database.create_engine", return_value=MagicMock()),
        patch("qx.db.tenancy.database.make_session_factory") as mock_sf,
    ):
        mock_sf.return_value = MagicMock()
        router = _make_router()
        f1 = router.get_session_factory(TENANT_ID)
        f2 = router.get_session_factory(TENANT_ID)

    assert f1 is f2
    assert mock_sf.call_count == 1


@pytest.mark.anyio
async def test_router_close_disposes_engine() -> None:
    engine = MagicMock()
    engine.dispose = AsyncMock()

    with patch("qx.db.tenancy.database.create_engine", return_value=engine):
        router = _make_router()
        router.get_engine(TENANT_ID)
        await router.close(TENANT_ID)

    engine.dispose.assert_called_once()
    assert str(TENANT_ID) not in router._engines


@pytest.mark.anyio
async def test_router_close_all_disposes_all_engines() -> None:
    engines = [MagicMock() for _ in range(3)]
    for e in engines:
        e.dispose = AsyncMock()
    ids = [UUID(f"{'0' * 8}-{'0' * 4}-{'0' * 4}-{'0' * 4}-{str(i).zfill(12)}") for i in range(3)]

    call_count = 0

    def make_engine(_):
        nonlocal call_count
        e = engines[call_count]
        call_count += 1
        return e

    with patch("qx.db.tenancy.database.create_engine", side_effect=make_engine):
        router = _make_router()
        for tid in ids:
            router.get_engine(tid)
        await router.close_all()

    for e in engines:
        e.dispose.assert_called_once()
    assert len(router._engines) == 0


# ---- open_db_session ----


@pytest.mark.anyio
async def test_open_db_session_yields_session() -> None:
    session = AsyncMock()
    factory = MagicMock(return_value=session)

    with (
        patch("qx.db.tenancy.database.create_engine", return_value=MagicMock()),
        patch("qx.db.tenancy.database.make_session_factory", return_value=factory),
    ):
        router = _make_router()
        async with open_db_session(router, TENANT_ID) as s:
            assert s is session

    session.close.assert_called_once()


@pytest.mark.anyio
async def test_open_db_session_closes_on_exception() -> None:
    session = AsyncMock()
    factory = MagicMock(return_value=session)

    with (
        patch("qx.db.tenancy.database.create_engine", return_value=MagicMock()),
        patch("qx.db.tenancy.database.make_session_factory", return_value=factory),
    ):
        router = _make_router()
        with pytest.raises(RuntimeError):
            async with open_db_session(router, TENANT_ID):
                raise RuntimeError("boom")

    session.close.assert_called_once()


# ---- helpers ----


def _make_settings() -> DatabaseSettings:
    s = DatabaseSettings()
    object.__setattr__(
        s, "url", SecretStr("postgresql+asyncpg://user:pass@localhost:5432/postgres")
    )
    return s


def _make_manager(
    admin_engine=None,
    metadata=None,
    prefix="tenant_",
):

    settings = _make_settings()
    return TenantDatabaseManager(
        admin_engine or MagicMock(),
        metadata or MetaData(),
        settings,
        prefix=prefix,
    )


def _make_router(prefix="tenant_"):
    return TenantEngineRouter(_make_settings(), prefix=prefix)


def _mock_autocommit_conn(exists: bool = False):
    conn = AsyncMock()
    # First execute call is the existence check; subsequent are DDL.
    scalar_result = MagicMock()
    scalar_result.scalar = MagicMock(return_value=1 if exists else None)
    conn.execute = AsyncMock(return_value=scalar_result)

    @asynccontextmanager
    async def _connect():
        yield conn

    engine = MagicMock()
    engine.execution_options = MagicMock(return_value=engine)
    engine.connect = MagicMock(return_value=_connect())
    engine.begin = MagicMock(return_value=_connect())
    return conn, engine


def _mock_engine_begin(conn: AsyncMock) -> MagicMock:
    @asynccontextmanager
    async def _begin():
        yield conn

    engine = MagicMock()
    engine.begin = MagicMock(return_value=_begin())
    engine.dispose = AsyncMock()
    return engine


def _mock_engine_connect(conn: AsyncMock) -> MagicMock:
    @asynccontextmanager
    async def _connect():
        yield conn

    engine = MagicMock()
    engine.connect = MagicMock(return_value=_connect())
    return engine


def _sqls(conn: AsyncMock) -> list[str]:
    result = []
    for c in conn.execute.call_args_list:
        arg = c.args[0] if c.args else None
        result.append(arg.text if hasattr(arg, "text") else str(arg))
    return result
