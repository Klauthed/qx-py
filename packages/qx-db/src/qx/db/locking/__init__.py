"""PostgreSQL advisory lock helpers.

Advisory locks are application-level mutexes stored in Postgres rather than
Redis. They are useful for low-contention critical sections where you don't
want to run a separate Redis service — e.g., the saga timeout scheduler or
a singleton background job.

Two variants are provided:

- ``advisory_lock(session, key)`` — **session-level**.  The lock is released
  explicitly by the context manager's ``__aexit__`` regardless of transaction
  outcome.  Works inside *or* outside an active transaction.

- ``advisory_xact_lock(session, key)`` — **transaction-level**.  Postgres
  releases the lock automatically when the surrounding transaction commits or
  rolls back.  Lower overhead but only valid inside an open transaction.

Both accept an ``int`` key (fits in a Postgres ``bigint``).  If you need to
derive a key from a string, use ``advisory_key(name)``::

    async with advisory_lock(session, advisory_key("saga-timeout-tick")):
        ...

Caution: advisory locks are non-reentrant from the same session (on
session-level locks).  Don't call nested ``advisory_lock`` with the same key.
"""

from __future__ import annotations

import contextlib
import hashlib
import struct
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = [
    "advisory_key",
    "advisory_lock",
    "advisory_xact_lock",
]


def advisory_key(name: str) -> int:
    """Derive a stable ``bigint`` advisory-lock key from an arbitrary string.

    Uses the first 8 bytes of SHA-256 interpreted as a signed 64-bit integer.
    Collisions are possible but astronomically unlikely for real application
    namespaces.
    """
    digest = hashlib.sha256(name.encode()).digest()[:8]
    value: int = struct.unpack(">q", digest)[0]  # big-endian signed 64-bit
    return value


@contextlib.asynccontextmanager
async def advisory_lock(
    session: AsyncSession,
    key: int,
) -> AsyncIterator[None]:
    """Acquire a **session-level** Postgres advisory lock for the duration of this block.

    The lock is released in ``finally`` regardless of whether the block raises.
    Safe to use inside or outside a transaction.

    ::

        async with advisory_lock(session, advisory_key("outbox-relay")):
            await process_batch()

    Raises:
        Any exception propagated from the database, e.g., connection errors.
    """
    await session.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            await session.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": key})


@contextlib.asynccontextmanager
async def advisory_xact_lock(
    session: AsyncSession,
    key: int,
) -> AsyncIterator[None]:
    """Acquire a **transaction-level** Postgres advisory lock.

    Released automatically when the transaction commits or rolls back —
    no explicit unlock needed (and calling unlock manually would error).
    Must be called within an open transaction.

    ::

        async with session.begin():
            async with advisory_xact_lock(session, advisory_key("saga-timeout")):
                await process_sagas()
    """
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
    yield
