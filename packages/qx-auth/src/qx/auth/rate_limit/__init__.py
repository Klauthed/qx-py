"""Token-bucket rate limiter backed by Redis.

A token bucket holds N tokens; consumers take a token to perform an action.
Tokens refill at rate R per second up to capacity N. If the bucket is empty,
the action is rejected with ``Result.failure(RateLimitedError(...))``.

Implementation: Lua script that atomically computes the current bucket
contents from a stored ``(last_refill_at, last_count)`` pair, decides whether
to take a token, and writes back. The Lua script avoids the read-then-write
race that plagues naive implementations.

Buckets are namespaced by ``(scope, key)`` — e.g., ``("login", user_id)`` or
``("api", api_key)``. Same scope, same refill rate; different rates need
different bucket names.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from qx.core import RateLimitedError, Result

if TYPE_CHECKING:
    import redis.asyncio as aioredis

__all__ = ["TokenBucket", "TokenBucketResult"]


_LUA_TAKE = """
-- KEYS[1] = bucket key
-- ARGV[1] = capacity, ARGV[2] = refill_per_second, ARGV[3] = now (float seconds)
-- ARGV[4] = take (int), ARGV[5] = ttl (int)
local capacity = tonumber(ARGV[1])
local refill = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local take = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])

local data = redis.call('HMGET', KEYS[1], 'count', 'last')
local count = tonumber(data[1])
local last = tonumber(data[2])

if count == nil then
    count = capacity
    last = now
end

local elapsed = now - last
if elapsed > 0 then
    count = math.min(capacity, count + (elapsed * refill))
end

local allowed = 0
if count >= take then
    count = count - take
    allowed = 1
end

redis.call('HMSET', KEYS[1], 'count', count, 'last', now)
redis.call('EXPIRE', KEYS[1], ttl)
return {allowed, count}
"""


@dataclass(frozen=True)
class TokenBucketResult:
    allowed: bool
    remaining: float


class TokenBucket:
    """Token-bucket rate limiter for one (scope, key) pair."""

    def __init__(
        self,
        client: aioredis.Redis,
        *,
        scope: str,
        capacity: int,
        refill_per_second: float,
        namespace: str = "qx:rl",
    ) -> None:
        self._r = client
        self._scope = scope
        self._capacity = capacity
        self._refill = refill_per_second
        self._ns = namespace

    def _key(self, key: str) -> str:
        return f"{self._ns}:{self._scope}:{key}"

    async def take(self, key: str, *, tokens: int = 1) -> TokenBucketResult:
        # TTL should be long enough that an idle key stays around until it
        # would fully refill — otherwise we'd lose state and grant new
        # capacity. ``capacity / refill_per_second`` * 2 is a safe default.
        ttl = max(int((self._capacity / max(self._refill, 0.001)) * 2), 60)
        result = await self._r.eval(  # type: ignore[misc]
            _LUA_TAKE,
            1,
            self._key(key),
            str(self._capacity),
            str(self._refill),
            str(time.time()),
            str(tokens),
            str(ttl),
        )
        allowed = bool(result[0])
        remaining = float(result[1])
        return TokenBucketResult(allowed=allowed, remaining=remaining)

    async def check(self, key: str, *, tokens: int = 1) -> Result[None]:
        """Take a token and convert the outcome to a ``Result``."""
        outcome = await self.take(key, tokens=tokens)
        if outcome.allowed:
            return Result.success(None)
        return Result.failure(
            RateLimitedError(
                code="rate_limit.exceeded",
                message=f"rate limit exceeded for {self._scope}:{key}",
                details={
                    "scope": self._scope,
                    "remaining": outcome.remaining,
                    "capacity": self._capacity,
                    "refill_per_second": self._refill,
                },
            )
        )
