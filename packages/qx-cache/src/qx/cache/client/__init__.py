"""Async Redis client wrapper.

We use ``redis.asyncio`` directly — no custom wrapping of the data API. This
module supplies:

- ``CacheSettings`` (Pydantic settings, ``QX_CACHE__*`` env vars).
- ``create_client(settings)`` — builds the singleton client with pooling.
- A trivial ``Cache`` facade with the half-dozen methods we actually use across
  the framework (``get_json`` / ``set_json`` / ``incr`` / ``expire``). Anyone
  needing more reaches for the underlying ``Redis`` instance.
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

import redis.asyncio as aioredis
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["Cache", "CacheSettings", "create_client"]


class CacheSettings(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="QX_CACHE__",
        extra="ignore",
    )

    url: str = Field(default="redis://localhost:6379/0")
    max_connections: int = 20
    decode_responses: bool = False  # we decode ourselves where needed
    socket_timeout_seconds: float = 2.0
    socket_connect_timeout_seconds: float = 2.0
    health_check_interval_seconds: int = 30


def create_client(settings: CacheSettings) -> aioredis.Redis:
    """Create a pooled async Redis client.

    Idempotent: the same settings produce the same pool topology. Dispose with
    ``await client.aclose()`` at shutdown.
    """
    pool = aioredis.ConnectionPool.from_url(
        settings.url,
        max_connections=settings.max_connections,
        decode_responses=settings.decode_responses,
        socket_timeout=settings.socket_timeout_seconds,
        socket_connect_timeout=settings.socket_connect_timeout_seconds,
        health_check_interval=settings.health_check_interval_seconds,
    )
    return aioredis.Redis(connection_pool=pool)


class Cache:
    """Thin facade over Redis for the framework's own use.

    Services can take this for the common ops, or pull the raw client out of
    DI (``Redis`` is registered alongside ``Cache``) when they need more.
    """

    def __init__(self, client: aioredis.Redis, *, namespace: str = "qx") -> None:
        self._r = client
        self._ns = namespace

    def key(self, *parts: str) -> str:
        return ":".join((self._ns, *parts))

    async def get_json(self, key: str) -> Any | None:
        raw = await self._r.get(key)
        if raw is None:
            return None
        return json.loads(raw)

    async def set_json(
        self,
        key: str,
        value: Any,
        *,
        ttl_seconds: int | None = None,
    ) -> None:
        payload = json.dumps(value, default=str)
        await self._r.set(key, payload, ex=ttl_seconds)

    async def delete(self, key: str) -> bool:
        return bool(await self._r.delete(key))

    async def incr(self, key: str, *, by: int = 1) -> int:
        return await self._r.incrby(key, by)  # type: ignore[no-any-return]

    async def expire(self, key: str, seconds: int) -> bool:
        return await self._r.expire(key, seconds)  # type: ignore[no-any-return]

    async def exists(self, key: str) -> bool:
        return bool(await self._r.exists(key))
