"""Database-per-tenant: provision/drop databases and route sessions."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from pydantic import SecretStr
from qx.db.engine import DatabaseSettings, create_engine, make_session_factory
from sqlalchemy import text
from sqlalchemy.engine import make_url

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from uuid import UUID

    from qx.db.engine import SessionFactory
    from sqlalchemy import MetaData
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

__all__ = ["TenantDatabaseManager", "TenantEngineRouter", "open_db_session"]


class TenantDatabaseManager:
    """Create, drop, and list per-tenant Postgres databases.

    Requires an ``admin_engine`` connected to a maintenance database (e.g.
    ``postgres``) that has ``CREATE DATABASE`` / ``DROP DATABASE`` privileges.
    The ``metadata`` supplies the table DDL run after database creation.

    Usage::

        manager = TenantDatabaseManager(admin_engine, metadata, settings)

        # On tenant sign-up
        await manager.provision(tenant_id)

        # On tenant offboarding
        await manager.drop(tenant_id)

    **Security note** — ``database_name`` is interpolated directly into SQL.
    Only tenant IDs from trusted sources (your own system) should be passed.
    """

    def __init__(
        self,
        admin_engine: AsyncEngine,
        metadata: MetaData,
        settings: DatabaseSettings,
        *,
        prefix: str = "tenant_",
    ) -> None:
        self._admin_engine = admin_engine
        self._metadata = metadata
        self._settings = settings
        self._prefix = prefix

    def database_name(self, tenant_id: UUID | str) -> str:
        """Return the Postgres database name for a tenant. Strips hyphens from UUID."""
        return f"{self._prefix}{str(tenant_id).replace('-', '')}"

    def _tenant_settings(self, tenant_id: UUID | str) -> DatabaseSettings:
        db_name = self.database_name(tenant_id)
        base_url = make_url(self._settings.url.get_secret_value())
        tenant_url = str(base_url.set(database=db_name))
        return self._settings.model_copy(update={"url": SecretStr(tenant_url)})

    async def provision(
        self,
        tenant_id: UUID | str,
        *,
        template: str | None = None,
    ) -> None:
        """Create the tenant database and run DDL from metadata.

        Idempotent — skips ``CREATE DATABASE`` if the database already exists.
        ``template`` is passed as the Postgres ``TEMPLATE`` clause when provided.
        """
        db_name = self.database_name(tenant_id)
        # CREATE DATABASE cannot run inside a transaction; use AUTOCOMMIT.
        async with self._admin_engine.execution_options(
            isolation_level="AUTOCOMMIT"
        ).connect() as conn:
            exists = (
                await conn.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :db"),
                    {"db": db_name},
                )
            ).scalar()
            if not exists:
                template_clause = f" TEMPLATE {template}" if template else ""
                await conn.execute(text(f'CREATE DATABASE "{db_name}"{template_clause}'))

        # Connect to the new database and run table DDL.
        engine = create_engine(self._tenant_settings(tenant_id))
        try:
            async with engine.begin() as conn:
                await conn.run_sync(self._metadata.create_all)
        finally:
            await engine.dispose()

    async def drop(self, tenant_id: UUID | str) -> None:
        """Terminate connections to the tenant database and drop it.

        Irreversible — confirm before calling.
        """
        db_name = self.database_name(tenant_id)
        async with self._admin_engine.execution_options(
            isolation_level="AUTOCOMMIT"
        ).connect() as conn:
            await conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid)"
                    " FROM pg_stat_activity"
                    " WHERE datname = :db AND pid != pg_backend_pid()"
                ),
                {"db": db_name},
            )
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))

    async def list_databases(self) -> list[str]:
        """Return names of all databases that match our prefix, alphabetically."""
        async with self._admin_engine.connect() as conn:
            rows = await conn.execute(
                text("SELECT datname FROM pg_database WHERE datname LIKE :prefix ORDER BY datname"),
                {"prefix": f"{self._prefix}%"},
            )
            return [row[0] for row in rows]


class TenantEngineRouter:
    """Caches one AsyncEngine (and session factory) per tenant database.

    Build one router at startup and share it across requests::

        router = TenantEngineRouter(settings)

        async with open_db_session(router, tenant_id) as session:
            users = await user_repo.list(session)

    Call ``await router.close_all()`` at shutdown to cleanly drain all pools.
    """

    def __init__(
        self,
        settings: DatabaseSettings,
        *,
        prefix: str = "tenant_",
    ) -> None:
        self._settings = settings
        self._prefix = prefix
        self._engines: dict[str, AsyncEngine] = {}
        self._factories: dict[str, SessionFactory] = {}

    def database_name(self, tenant_id: UUID | str) -> str:
        """Return the Postgres database name for a tenant. Strips hyphens from UUID."""
        return f"{self._prefix}{str(tenant_id).replace('-', '')}"

    def get_engine(self, tenant_id: UUID | str) -> AsyncEngine:
        """Return (creating if needed) the cached engine for a tenant."""
        key = str(tenant_id)
        if key not in self._engines:
            db_name = self.database_name(tenant_id)
            base_url = make_url(self._settings.url.get_secret_value())
            tenant_settings = self._settings.model_copy(
                update={"url": SecretStr(str(base_url.set(database=db_name)))}
            )
            self._engines[key] = create_engine(tenant_settings)
        return self._engines[key]

    def get_session_factory(self, tenant_id: UUID | str) -> SessionFactory:
        """Return (creating if needed) the cached session factory for a tenant."""
        key = str(tenant_id)
        if key not in self._factories:
            self._factories[key] = make_session_factory(self.get_engine(tenant_id))
        return self._factories[key]

    async def close(self, tenant_id: UUID | str) -> None:
        """Dispose the engine and evict the tenant from the cache."""
        key = str(tenant_id)
        self._factories.pop(key, None)
        engine = self._engines.pop(key, None)
        if engine is not None:
            await engine.dispose()

    async def close_all(self) -> None:
        """Dispose all cached engines. Call at process shutdown."""
        self._factories.clear()
        engines = list(self._engines.values())
        self._engines.clear()
        for engine in engines:
            await engine.dispose()


@asynccontextmanager
async def open_db_session(
    router: TenantEngineRouter,
    tenant_id: UUID | str,
) -> AsyncIterator[AsyncSession]:
    """Open a session on the tenant's dedicated database.

    Resolves the engine from the router cache and opens a fresh session.
    The session is closed on exit regardless of outcome::

        async with open_db_session(router, tenant_id) as session:
            await repo.add(entity, session)
    """
    factory = router.get_session_factory(tenant_id)
    session: AsyncSession = factory()
    try:
        yield session
    finally:
        await session.close()
