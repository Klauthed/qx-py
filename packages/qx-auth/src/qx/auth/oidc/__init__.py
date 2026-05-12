"""OIDC discovery client.

For services that need full OIDC discovery (not just JWT validation against a
known JWKS URI), this fetches the issuer's well-known config and exposes
endpoints with sensible TTL caching.

Used by:

- ``JwtValidator`` when no ``jwks_uri`` is configured — fall back to discovery.
- Services that initiate authorization code flows (rare server-to-server, but
  used by admin tooling and console BFFs).

Out of scope: the actual auth-code flow, PKCE state management, token storage.
Qx's hosted UI handles browser-flow auth; this module is just the
plumbing for backend services to validate the resulting tokens.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from qx.core import InfrastructureError, Result

if TYPE_CHECKING:
    import httpx

__all__ = ["OidcConfiguration", "OidcDiscovery"]


@dataclass(frozen=True)
class OidcConfiguration:
    issuer: str
    authorization_endpoint: str | None
    token_endpoint: str | None
    userinfo_endpoint: str | None
    jwks_uri: str
    end_session_endpoint: str | None
    introspection_endpoint: str | None
    raw: dict[str, Any]


class OidcDiscovery:
    """Fetches and caches OIDC ``.well-known`` configuration."""

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        cache_ttl_seconds: int = 3600,
    ) -> None:
        self._http = http_client
        self._ttl = cache_ttl_seconds
        self._cache: dict[str, tuple[float, OidcConfiguration]] = {}
        self._lock = asyncio.Lock()

    async def fetch(self, issuer: str) -> Result[OidcConfiguration]:
        now = time.monotonic()
        cached = self._cache.get(issuer)
        if cached is not None and (now - cached[0]) < self._ttl:
            return Result.success(cached[1])

        async with self._lock:
            # Double-check inside the lock.
            cached = self._cache.get(issuer)
            if cached is not None and (now - cached[0]) < self._ttl:
                return Result.success(cached[1])
            url = f"{issuer.rstrip('/')}/.well-known/openid-configuration"
            try:
                resp = await self._http.get(url, timeout=5.0)
                resp.raise_for_status()
            except Exception as exc:
                return Result.failure(
                    InfrastructureError(
                        code="oidc.discovery_failed",
                        message=f"could not fetch {url}: {exc}",
                        cause=exc,
                    )
                )
            data = resp.json()
            try:
                config = OidcConfiguration(
                    issuer=data["issuer"],
                    authorization_endpoint=data.get("authorization_endpoint"),
                    token_endpoint=data.get("token_endpoint"),
                    userinfo_endpoint=data.get("userinfo_endpoint"),
                    jwks_uri=data["jwks_uri"],
                    end_session_endpoint=data.get("end_session_endpoint"),
                    introspection_endpoint=data.get("introspection_endpoint"),
                    raw=data,
                )
            except KeyError as exc:
                return Result.failure(
                    InfrastructureError(
                        code="oidc.malformed_discovery",
                        message=f"required discovery field missing: {exc}",
                        cause=exc,
                    )
                )
            self._cache[issuer] = (now, config)
            return Result.success(config)
