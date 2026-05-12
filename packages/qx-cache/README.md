# qx-cache

Redis client, Lua-atomic idempotency store, and distributed lock for the Qx framework.

## What lives here

- **`qx.cache.Cache`** — thin async Redis client wrapper with typed `get`/`set`/`delete`/`exists` methods and TTL support.
- **`qx.cache.CacheSettings`** — Pydantic settings for Redis connection URL.
- **`qx.cache.create_client`** — async factory that opens and validates a Redis connection.
- **`qx.cache.IdempotencyStore`** — Lua-script-based atomic check-and-set. A single round-trip to Redis either claims an idempotency key (first caller wins) or returns the cached result (all subsequent callers). Used by `IdempotencyBehavior` in the Mediator pipeline to make command handlers idempotent.
- **`qx.cache.DistributedLock`** — Redis-backed advisory lock with TTL and async context-manager interface. Uses `SET NX PX` for acquisition and Lua for safe release (only the holder can release).
- **`qx.cache.LockNotHeldError`** — raised when attempting to release a lock that has expired or was never acquired.

## Usage

### Idempotency store

```python
from qx.cache import IdempotencyStore, create_client

redis = await create_client(settings.cache.url)
store = IdempotencyStore(redis, ttl_seconds=3600)

# In a command handler or pipeline behavior:
key = f"create-order:{cmd.idempotency_key}"
cached = await store.get(key)
if cached is not None:
    return Result.success(cached)

result = await do_work(cmd)
if result.is_success:
    await store.set(key, result.value)
return result
```

### Distributed lock

```python
from qx.cache import DistributedLock

async with DistributedLock(redis, "outbox-relay-leader", ttl_seconds=30):
    await relay.run_once()
```

## Design rules

- **Lua atomicity** — `IdempotencyStore` uses a single Lua script for the check-and-set so there is no window between checking and setting, even under concurrent requests.
- **No silent failures** — `DistributedLock` raises `LockNotHeldError` if the lock has expired before you release it, so callers know their critical section may have overlapped.
- The cache layer has no dependency on `qx-db` or `qx-cqrs`. It can be used standalone.
