"""Provider primitives for the DI container.

A *provider* is the unit of registration: it knows how to produce a value when
asked, and what its lifetime is. The container stores providers; *instances* are
the values providers produce on demand.

Three lifetimes — see ``Lifetime`` docs. The provider abstraction is identical
across them; only the caching/scoping policy differs and that's a container
concern.
"""

from __future__ import annotations

import enum
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

T = TypeVar("T")

__all__ = ["Lifetime", "Provider", "FactoryProvider", "InstanceProvider"]


class Lifetime(enum.Enum):
    """Lifetime of a resolved dependency.

    SINGLETON
        One instance per container. Created on first resolution, shared
        across all subsequent resolutions in this container (and child
        scopes). Use for stateless services, configuration, clients to
        external systems.

    SCOPED
        One instance per scope. Use for request-scoped state: unit-of-work,
        request context wrappers, per-request caches. A new scope is created
        per HTTP request, NATS message, CLI command.

    TRANSIENT
        A fresh instance every resolution. Use for stateful values that
        must not be shared (mutable builders, ad-hoc per-call objects).
        Be cautious — transients still get their dependencies resolved by
        the container, and a transient that depends on a scoped object
        captures *that* scope, which is fine but easy to misunderstand.
    """

    SINGLETON = "singleton"
    SCOPED = "scoped"
    TRANSIENT = "transient"


@dataclass
class Provider(Generic[T]):
    """Base provider: descriptor of how to produce a ``T``.

    The container owns provider lookup by key (usually the abstract type/protocol).
    The provider itself owns *how* to produce. This separation lets us swap
    factories at test time via ``container.override(Abstract, FakeImpl)``.
    """

    key: type[T] | str
    lifetime: Lifetime
    # Tags let the container fetch providers by metadata, e.g., "all event handlers"
    tags: frozenset[str] = field(default_factory=frozenset)

    async def provide(self, container: "Container") -> T:  # type: ignore[name-defined,empty-body]  # noqa: F821
        raise NotImplementedError

    @property
    def is_async_provider(self) -> bool:
        """Whether ``provide`` needs to be awaited.

        Lets the container short-circuit on pure sync graphs without paying
        the event-loop hop.
        """
        return True  # subclasses override


@dataclass
class FactoryProvider(Provider[T]):
    """Provider backed by a callable. The container introspects the callable
    signature and resolves each parameter from itself recursively.

    The factory may be sync or async, and may declare ``Container`` as a parameter
    to receive the container itself (for advanced cases — generally avoid).
    """

    factory: Callable[..., T] | Callable[..., Awaitable[T]] = field(default=lambda: None)  # type: ignore[assignment]

    async def provide(self, container: "Container") -> T:  # noqa: F821
        # The actual resolution is performed by Container._call_factory because
        # it needs access to the scope to resolve dependencies. The provider
        # only carries the factory + lifetime metadata.
        return await container._call_factory(self.factory, self.key)  # type: ignore[attr-defined]

    @property
    def is_async_provider(self) -> bool:
        return inspect.iscoroutinefunction(self.factory)


@dataclass
class InstanceProvider(Provider[T]):
    """Pre-built singleton. The container returns this exact object every time.

    Useful for configuration objects, third-party clients that are constructed
    before the container is built, and test doubles supplied via ``override``.
    """

    instance: T = field(default=None)  # type: ignore[assignment]

    async def provide(self, container: "Container") -> T:  # noqa: ARG002, F821
        return self.instance

    @property
    def is_async_provider(self) -> bool:
        return False
