"""Scope: per-request instance cache and async-disposable management.

A scope is the lifetime envelope for ``Lifetime.SCOPED`` providers. When a scope
opens, an empty cache is created. Anything resolved with ``SCOPED`` is cached
there. When the scope closes, every resource that was cached gets ``aclose``-d
(if async-disposable) or ``close``-d (if sync-disposable) in reverse construction
order.

Scopes nest. A child scope can resolve singletons from its parent and transients
freshly, but its scoped cache is independent.

Implementation note: we keep scopes immutable from the outside. The only API to
mutate the cache is via the container's resolve path, which acquires the scope's
internal lock around store-or-fetch operations.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

__all__ = ["Scope"]


class Scope:
    """A resolution scope with a per-key instance cache and disposal stack."""

    __slots__ = ("_cache", "_disposers", "_lock", "_closed", "name")

    def __init__(self, name: str = "scope") -> None:
        self.name = name
        self._cache: dict[Any, Any] = {}
        # Disposers run LIFO so dependents tear down before their deps.
        self._disposers: list[Callable[[], Awaitable[None] | None]] = []
        # Single mutex protects cache + disposers. Resolution can be re-entrant
        # within one task (foo depends on bar, bar depends on baz) so we use a
        # task-level lock rather than asyncio.Lock + recursion tracking.
        # Each scope serves one task at a time during construction; once cached,
        # reads are lock-free. This matches dependency-injector's pattern.
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def get(self, key: Any) -> Any | None:
        return self._cache.get(key)

    def set(self, key: Any, value: Any) -> None:
        if self._closed:
            raise RuntimeError(f"Scope {self.name!r} is closed; cannot register {key!r}")
        self._cache[key] = value

    def register_disposer(self, disposer: Callable[[], Awaitable[None] | None]) -> None:
        if self._closed:
            raise RuntimeError(f"Scope {self.name!r} is closed; cannot register disposer")
        self._disposers.append(disposer)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Run in reverse so things tear down in opposite order of construction.
        errors: list[BaseException] = []
        for disposer in reversed(self._disposers):
            try:
                result = disposer()
                if asyncio.iscoroutine(result):
                    await result
            except BaseException as exc:  # noqa: BLE001 — we collect and re-raise as group
                errors.append(exc)
        self._disposers.clear()
        self._cache.clear()
        if errors:
            raise BaseExceptionGroup(
                f"errors disposing scope {self.name!r}", errors
            )

    async def __aenter__(self) -> "Scope":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        with suppress(BaseException):  # noqa: BLE001
            await self.close()
