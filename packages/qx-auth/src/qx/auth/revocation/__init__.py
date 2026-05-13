"""JTI revocation store backed by Redis.

Plugs into ``JwtValidator`` via the ``revocation_check`` hook::

    store = RedisRevocationStore(cache_client)
    validator = JwtValidator(settings, revocation_check=store)

When a token is revoked (logout, rotation, breach response), call
``store.revoke(jti, ttl_seconds)``. The TTL should match the token's
remaining lifetime — there is no point keeping a JTI in the deny-list
after its natural expiry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qx.cache import CacheFacade

__all__ = ["RedisRevocationStore"]

_PREFIX = "qx:auth:revoked"


class RedisRevocationStore:
    """Redis-backed JTI deny-list.

    Implements the ``RevocationCheck`` protocol — it is directly usable as the
    ``revocation_check=`` argument to ``JwtValidator``.
    """

    def __init__(self, cache: CacheFacade, *, namespace: str = _PREFIX) -> None:
        self._cache = cache
        self._ns = namespace

    def _key(self, jti: str) -> str:
        return f"{self._ns}:{jti}"

    async def revoke(self, jti: str, *, ttl_seconds: int) -> None:
        """Add ``jti`` to the deny-list for ``ttl_seconds``."""
        await self._cache.set_json(self._key(jti), {"revoked": True}, ttl=ttl_seconds)

    async def is_revoked(self, jti: str) -> bool:
        """Return ``True`` if ``jti`` is in the deny-list."""
        value = await self._cache.get_json(self._key(jti))
        return value is not None

    # RevocationCheck protocol — callable as ``await store(jti)``
    async def __call__(self, jti: str) -> bool:
        return await self.is_revoked(jti)
