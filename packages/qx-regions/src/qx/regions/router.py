"""RegionRouter — redirect writes to the tenant's home region."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from qx.regions.config import RegionConfig
    from qx.regions.resolver import RegionResolver

__all__ = ["RegionRouter"]


class RegionRouter:
    """Decides whether a request must be redirected to the tenant's home region.

    Use in ASGI middleware to redirect non-home-region writes::

        router = RegionRouter(resolver, config)

        redirect_url = await router.get_redirect_url(tenant_id, request.url.path)
        if redirect_url:
            return RedirectResponse(redirect_url, status_code=307)

    Returns ``None`` when the current region is the tenant's home region (serve
    locally) or when no URL is configured for the home region.
    """

    def __init__(self, resolver: RegionResolver, config: RegionConfig) -> None:
        self._resolver = resolver
        self._config = config

    async def get_redirect_url(
        self,
        tenant_id: UUID | str,
        path: str,
    ) -> str | None:
        """Return the redirect URL if the request belongs to another region.

        Returns ``None`` if this region is the home region or if no URL is
        registered for the home region (fail-open to avoid blocking requests).
        """
        home_region = await self._resolver.resolve(tenant_id)
        if home_region == self._config.name:
            return None
        base_url = self._config.urls.get(home_region)
        if base_url is None:
            return None
        return f"{base_url.rstrip('/')}{path}"

    def is_home_region(self, region: str) -> bool:
        """Return True if ``region`` is the current region."""
        return region == self._config.name
