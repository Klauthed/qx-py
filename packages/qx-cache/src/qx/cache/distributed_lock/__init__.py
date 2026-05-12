"""Distributed lock.

A simple ``SET NX EX`` lock with a token, for short-duration critical sections
(leader-election bootstrap, outbox-relay singleton enforcement). NOT a
replacement for proper coordination — for anything mission-critical, use a
real consensus protocol (etcd, ZooKeeper) or a transactional advisory lock
(``pg_advisory_lock``).

The lock acquires a UUID token and stores it as the value; release runs a Lua
script that only deletes the key if the value still matches the token. This
prevents the classic "lock expired, someone else acquired, then I release
their lock" footgun.

Lock TTL is intentionally short (default 30s). If your critical section is
longer, you have a different problem; consider whether the work belongs in
the request path at all.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import redis.asyncio as aioredis

__all__ = ["DistributedLock", "LockNotHeldError"]


_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class LockNotHeldError(Exception):
    """Raised on release when the lock was already lost (timed out, stolen)."""


class DistributedLock:
    """A single named lock instance.

    Construct via ``DistributedLock(client, "leader:outbox-relay")`` once;
    reuse to acquire/release.
    """

    def __init__(self, client: aioredis.Redis, name: str) -> None:
        self._r = client
        self._name = name
        self._token: str | None = None

    async def acquire(self, *, ttl_seconds: int = 30) -> bool:
        """Try to acquire. Returns True on success, False if already held."""
        token = str(uuid4())
        # SET key token NX EX ttl — atomic. Returns OK only if not held.
        ok = await self._r.set(self._name, token, nx=True, ex=ttl_seconds)
        if ok:
            self._token = token
            return True
        return False

    async def renew(self, *, ttl_seconds: int = 30) -> bool:
        """Extend TTL if we still hold the lock."""
        if self._token is None:
            return False
        # Lua so the check-and-extend is atomic.
        script = """
        if redis.call('GET', KEYS[1]) == ARGV[1] then
            return redis.call('EXPIRE', KEYS[1], ARGV[2])
        end
        return 0
        """
        result = await self._r.eval(script, 1, self._name, self._token, str(ttl_seconds))  # type: ignore[misc]
        return bool(result)

    async def release(self) -> None:
        """Release the lock — only deletes if the token still matches."""
        if self._token is None:
            return
        await self._r.eval(_RELEASE_SCRIPT, 1, self._name, self._token)  # type: ignore[misc]
        self._token = None

    @contextlib.asynccontextmanager
    async def acquired(self, *, ttl_seconds: int = 30) -> AsyncIterator[bool]:
        """Async context manager that yields whether we got the lock."""
        ok = await self.acquire(ttl_seconds=ttl_seconds)
        try:
            yield ok
        finally:
            if ok:
                await self.release()
