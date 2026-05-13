"""Postgres Row-Level Security helpers for tenant isolation."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from uuid import UUID

    from qx.db.engine import SessionFactory
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

__all__ = ["RlsPolicyManager", "open_rls_session"]

_DEFAULT_SETTING = "app.current_tenant_id"


class RlsPolicyManager:
    """Enable and disable Postgres RLS tenant-isolation policies on tables.

    Call ``enable`` once per table during schema setup (migration, provisioning).
    It is idempotent — safe to call if the policy already exists.

    The generated policy isolates rows by matching ``column`` to the Postgres
    session GUC ``setting_name`` (default ``app.current_tenant_id``), which
    ``open_rls_session`` sets at the start of each request::

        manager = RlsPolicyManager()

        async with engine.begin() as conn:
            await manager.enable(conn, "users")
            await manager.enable(conn, "orders")

    ``FORCE ROW LEVEL SECURITY`` ensures the policy applies even to the table
    owner (typically the service user), so no row leaks under any role.

    **Security note** — ``table_name``, ``column``, and ``policy_name`` are
    interpolated directly into SQL. Pass only developer-controlled values,
    never user-supplied strings.
    """

    async def enable(
        self,
        conn: AsyncConnection,
        table_name: str,
        *,
        column: str = "tenant_id",
        policy_name: str | None = None,
        setting_name: str = _DEFAULT_SETTING,
    ) -> None:
        """Enable RLS and create (or replace) a tenant isolation policy."""
        name = policy_name or f"qx_tenant_{table_name}"
        await conn.execute(text(f'ALTER TABLE "{table_name}" ENABLE ROW LEVEL SECURITY'))
        await conn.execute(text(f'ALTER TABLE "{table_name}" FORCE ROW LEVEL SECURITY'))
        # Drop-then-create for idempotency (CREATE POLICY has no IF NOT EXISTS).
        await conn.execute(text(f'DROP POLICY IF EXISTS "{name}" ON "{table_name}"'))
        await conn.execute(
            text(
                f'CREATE POLICY "{name}" ON "{table_name}"'
                f" USING (\"{column}\" = current_setting('{setting_name}', true)::uuid)"
            )
        )

    async def disable(
        self,
        conn: AsyncConnection,
        table_name: str,
        *,
        policy_name: str | None = None,
    ) -> None:
        """Drop the tenant policy and disable RLS on the table."""
        name = policy_name or f"qx_tenant_{table_name}"
        await conn.execute(text(f'DROP POLICY IF EXISTS "{name}" ON "{table_name}"'))
        await conn.execute(text(f'ALTER TABLE "{table_name}" DISABLE ROW LEVEL SECURITY'))


@asynccontextmanager
async def open_rls_session(
    factory: SessionFactory,
    tenant_id: UUID | str,
    *,
    setting_name: str = _DEFAULT_SETTING,
) -> AsyncIterator[AsyncSession]:
    """Open a session with the RLS tenant GUC set for the current transaction.

    Uses ``set_config(..., is_local := true)`` so the setting is scoped to the
    current transaction and resets automatically on COMMIT or ROLLBACK — the
    connection returns to the pool clean with no manual reset.

    Intended for one-transaction-per-request patterns. If your UoW commits
    mid-session you must re-enter this context manager for subsequent work::

        async with open_rls_session(factory, tenant_id) as session:
            async with UnitOfWork(session, ...) as uow:
                await user_repo.add(new_user)
                await uow.commit()
    """
    session: AsyncSession = factory()
    try:
        await session.execute(
            text("SELECT set_config(:key, :val, true)"),
            {"key": setting_name, "val": str(tenant_id)},
        )
        yield session
    finally:
        await session.close()
