"""TenantSchemaManager — provision, drop, and list per-tenant PostgreSQL schemas."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy import MetaData
    from sqlalchemy.ext.asyncio import AsyncEngine

__all__ = ["TenantSchemaManager"]


class TenantSchemaManager:
    """Create and manage per-tenant Postgres schemas.

    Each tenant gets a dedicated schema (``tenant_<uuid_hex>``). Tables are
    created from the supplied ``MetaData`` using ``CREATE TABLE IF NOT EXISTS``
    via ``metadata.create_all`` — idempotent and safe to call on every
    tenant sign-up.

    Usage::

        manager = TenantSchemaManager(engine, metadata)

        # On tenant sign-up
        await manager.provision(tenant_id)

        # On tenant offboarding
        await manager.drop(tenant_id)

        # Enumerate provisioned tenants
        schemas = await manager.list_schemas()
    """

    def __init__(
        self,
        engine: AsyncEngine,
        metadata: MetaData,
        *,
        prefix: str = "tenant_",
    ) -> None:
        self._engine = engine
        self._metadata = metadata
        self._prefix = prefix

    def schema_name(self, tenant_id: UUID | str) -> str:
        """Return the Postgres schema name for a tenant. Strips hyphens from UUID."""
        return f"{self._prefix}{str(tenant_id).replace('-', '')}"

    async def provision(self, tenant_id: UUID | str) -> None:
        """Create the tenant schema and all tables from metadata.

        Idempotent — safe to call if schema already exists.
        """
        schema = self.schema_name(tenant_id)
        async with self._engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
            # Restrict search_path to only the tenant schema so that
            # metadata.create_all checks for tables there and does not find
            # (and therefore skip) tables that already exist in public.
            await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            await conn.run_sync(self._metadata.create_all)

    async def drop(self, tenant_id: UUID | str) -> None:
        """Drop the tenant schema and all its objects (CASCADE).

        Irreversible — confirm with the caller before invoking.
        """
        schema = self.schema_name(tenant_id)
        async with self._engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))

    async def list_schemas(self) -> list[str]:
        """Return names of all schemas that match our prefix, in alphabetical order."""
        async with self._engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT schema_name FROM information_schema.schemata"
                    " WHERE schema_name LIKE :prefix ORDER BY schema_name"
                ),
                {"prefix": f"{self._prefix}%"},
            )
            return [row[0] for row in rows]
