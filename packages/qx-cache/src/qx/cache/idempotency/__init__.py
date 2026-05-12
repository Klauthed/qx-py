"""Idempotency store.

Implements the ``Idempotency-Key`` header pattern: a client sends a unique key
with a request; if the same key + payload arrives again within the TTL, the
server replays the stored response instead of re-executing the command.

Storage shape (Redis hash, one per key)::

    qx:idem:{tenant}:{key}
        status     -> "in_progress" | "completed"
        request_fp -> sha256 of the canonical request payload
        response   -> JSON of the original response
        created_at -> ISO timestamp

The ``request_fp`` lets us distinguish two clients accidentally reusing the
same key for different payloads — we 409 in that case rather than returning
the wrong response. That's the difference between "idempotent" and "merely
de-duplicated".
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis

from qx.core import (
    ConflictError,
    InfrastructureError,
    PreconditionFailedError,
    Result,
)

__all__ = ["IdempotencyStore", "IdempotencyConflictError"]


class IdempotencyConflictError(PreconditionFailedError):
    """Same idempotency key, different payload — refuse to serve."""


class IdempotencyStore:
    """Store + retrieve idempotent operation outcomes."""

    def __init__(
        self,
        client: aioredis.Redis,
        *,
        namespace: str = "qx:idem",
        default_ttl_seconds: int = 24 * 3600,
    ) -> None:
        self._r = client
        self._ns = namespace
        self._ttl = default_ttl_seconds

    def _key(self, tenant: str, idem_key: str) -> str:
        return f"{self._ns}:{tenant}:{idem_key}"

    @staticmethod
    def fingerprint(payload: Any) -> str:
        """Stable sha256 of a request payload."""
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()

    async def begin(
        self,
        tenant: str,
        idem_key: str,
        payload: Any,
        *,
        ttl_seconds: int | None = None,
    ) -> Result[None | dict[str, Any]]:
        """Attempt to claim the key.

        Returns:
            ``Result.success(None)`` — caller may proceed (first time we see this key).
            ``Result.success(stored_response)`` — replay the stored response.
            ``Result.failure(IdempotencyConflictError)`` — same key, different
              payload; client should retry with a fresh key or fix their bug.
        """
        key = self._key(tenant, idem_key)
        fp = self.fingerprint(payload)
        ttl = ttl_seconds or self._ttl

        # We try to SETNX-style claim the slot atomically. WATCH+MULTI in Redis
        # is the canonical pattern; here we use a small Lua script for clarity.
        # 1) if no key, write status=in_progress + fp; return "claim"
        # 2) if key exists with same fp: return whatever is stored
        # 3) if key exists with different fp: return "conflict"
        script = """
        if redis.call('EXISTS', KEYS[1]) == 0 then
            redis.call('HSET', KEYS[1], 'status', 'in_progress',
                       'request_fp', ARGV[1], 'created_at', ARGV[2])
            redis.call('EXPIRE', KEYS[1], ARGV[3])
            return 'claim'
        end
        local stored_fp = redis.call('HGET', KEYS[1], 'request_fp')
        if stored_fp ~= ARGV[1] then
            return 'conflict'
        end
        local status = redis.call('HGET', KEYS[1], 'status')
        if status == 'completed' then
            return redis.call('HGET', KEYS[1], 'response')
        end
        return 'in_progress'
        """
        try:
            result = await self._r.eval(  # type: ignore[no-untyped-call]
                script,
                1,
                key,
                fp,
                datetime.now(UTC).isoformat(),
                str(ttl),
            )
        except Exception as exc:  # noqa: BLE001
            return Result.failure(
                InfrastructureError(
                    code="idempotency.store_unavailable",
                    message=f"idempotency store error: {exc}",
                    cause=exc,
                )
            )

        if result == b"claim" or result == "claim":
            return Result.success(None)
        if result == b"conflict" or result == "conflict":
            return Result.failure(
                IdempotencyConflictError(
                    code="idempotency.payload_mismatch",
                    message=(
                        "Idempotency-Key already in use with a different request payload. "
                        "Use a new key for a different request."
                    ),
                )
            )
        if result == b"in_progress" or result == "in_progress":
            return Result.failure(
                ConflictError(
                    code="idempotency.in_progress",
                    message=(
                        "A request with this Idempotency-Key is already in progress. "
                        "Retry shortly."
                    ),
                )
            )
        # Otherwise: a stored response payload
        raw = result if isinstance(result, bytes) else result.encode()
        return Result.success(json.loads(raw))

    async def complete(
        self,
        tenant: str,
        idem_key: str,
        response: Any,
        *,
        ttl_seconds: int | None = None,
    ) -> None:
        """Persist the response and mark the slot as completed."""
        key = self._key(tenant, idem_key)
        ttl = ttl_seconds or self._ttl
        await self._r.hset(  # type: ignore[no-untyped-call]
            key,
            mapping={
                "status": "completed",
                "response": json.dumps(response, default=str),
            },
        )
        await self._r.expire(key, ttl)
