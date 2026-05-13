"""Schema-scoped session: sets search_path to the tenant schema before yielding."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from uuid import UUID

    from qx.db.engine import SessionFactory
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["open_schema_session"]


def _schema_name(tenant_id: UUID | str, prefix: str) -> str:
    return f"{prefix}{str(tenant_id).replace('-', '')}"


@asynccontextmanager
async def open_schema_session(
    factory: SessionFactory,
    tenant_id: UUID | str,
    *,
    prefix: str = "tenant_",
) -> AsyncIterator[AsyncSession]:
    """Open a session scoped to the tenant's Postgres schema.

    Sets ``search_path`` before yielding so all unqualified table references
    resolve to the tenant schema. Resets to ``public`` and closes the session
    on exit — including on exceptions — so pooled connections are left clean.

    Usage::

        async with open_schema_session(factory, tenant_id) as session:
            results = await repo.list(session)
    """
    schema = _schema_name(tenant_id, prefix)
    session: AsyncSession = factory()
    try:
        await session.execute(text(f'SET search_path TO "{schema}", public'))
        yield session
    finally:
        await session.execute(text("SET search_path TO public"))
        await session.close()
