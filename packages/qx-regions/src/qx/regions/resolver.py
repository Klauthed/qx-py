"""RegionResolver — map tenant_id to its home region."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from qx.core import NotFoundError
from qx.db.engine import open_session
from sqlalchemy import select

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy import Table
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = ["DbRegionResolver", "RegionResolver", "StaticRegionResolver"]


@runtime_checkable
class RegionResolver(Protocol):
    """Returns the home (write) region for a tenant."""

    async def resolve(self, tenant_id: UUID | str) -> str:
        """Resolve the home region for ``tenant_id``.

        Raises ``NotFoundError`` if the tenant has no registered home region
        and no default is configured.
        """
        ...


class StaticRegionResolver:
    """Resolves regions from an in-memory mapping.

    Suitable for tests and single-region deployments where all tenants share
    the same region::

        resolver = StaticRegionResolver(
            {"tenant-a": "eu-west-1", "tenant-b": "us-east-1"},
            default="us-east-1",
        )
    """

    def __init__(
        self,
        mapping: dict[str, str],
        *,
        default: str | None = None,
    ) -> None:
        self._mapping = {str(k): v for k, v in mapping.items()}
        self._default = default

    async def resolve(self, tenant_id: UUID | str) -> str:
        region = self._mapping.get(str(tenant_id))
        if region is not None:
            return region
        if self._default is not None:
            return self._default
        raise NotFoundError(
            code="region.not_found",
            message=f"No home region for tenant {tenant_id}",
            details={"tenant_id": str(tenant_id)},
        ).as_exception()


class DbRegionResolver:
    """Resolves tenant home regions from the ``qx_tenant_regions`` table.

    Pass a session factory and the table object returned by
    ``include_region_tables``::

        tenant_regions, _ = include_region_tables(metadata)
        resolver = DbRegionResolver(session_factory, tenant_regions, default="us-east-1")
    """

    def __init__(
        self,
        session_factory: AsyncSession,
        table: Table,
        *,
        default: str | None = None,
    ) -> None:
        self._factory = session_factory
        self._table = table
        self._default = default

    async def resolve(self, tenant_id: UUID | str) -> str:
        async with open_session(self._factory) as session:  # type: ignore[arg-type]
            row = (
                (
                    await session.execute(
                        select(self._table.c.home_region).where(
                            self._table.c.tenant_id == str(tenant_id)
                        )
                    )
                )
                .mappings()
                .first()
            )

        if row is not None:
            return str(row["home_region"])
        if self._default is not None:
            return self._default
        raise NotFoundError(
            code="region.not_found",
            message=f"No home region registered for tenant {tenant_id}",
            details={"tenant_id": str(tenant_id)},
        ).as_exception()
