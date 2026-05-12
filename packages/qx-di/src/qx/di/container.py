"""The Qx DI container.

Design pillars:

1. **Type-driven**: registrations key on the abstract class or protocol. Resolution
   walks constructor annotations. Strings are accepted as keys for tagged or
   string-keyed registrations (config values, named instances), but should be
   rare in domain code.

2. **Async-aware**: factories can be coroutine functions; resolution itself is
   always ``async def`` so callers don't need to know which providers are sync
   vs async. Sync resolution is available via ``resolve_sync`` for use sites that
   can't await (test fixtures, top-level CLI bootstrap) and statically refuses
   anything that requires awaiting.

3. **Scoped via context**: ``scope()`` returns an async context manager that
   establishes a child scope, runs resolution inside it, and tears down on exit.
   Singletons live in the root container; scoped instances live in the active
   scope; transients are fresh per resolve.

4. **Override-friendly**: ``container.override(Abstract, fake_or_factory)`` swaps
   a registration for the duration of a ``with`` block. Designed for tests and
   environment switches.

5. **Discoverable**: ``container.scan(package)`` walks modules and registers any
   class decorated with ``@injectable`` / ``@singleton`` / ``@scoped`` /
   ``@transient``. The decorator just stamps the class; scan does the work.

6. **Hierarchical**: ``container.create_child(...)`` returns a new container that
   resolves first against itself, then against the parent. Used by modular
   monoliths to give each feature module a private DI namespace.

Anti-pillars (things we explicitly don't do):

- No string-keyed lookup as the default API — too easy to typo.
- No "Inject(...)" sentinel objects in defaults — Python supports it but we
  prefer explicit constructor signatures with no defaults.
- No global "current container" — request-scoped state lives in the scope,
  containers are passed explicitly. The HTTP / worker integration layers expose
  the container via DI itself.
"""

from __future__ import annotations

import asyncio
import inspect
from contextlib import asynccontextmanager, contextmanager
from typing import TYPE_CHECKING, Any, TypeVar, cast, get_type_hints, overload

from qx.di.providers import (
    FactoryProvider,
    InstanceProvider,
    Lifetime,
    Provider,
)
from qx.di.scope import Scope

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator

__all__ = ["Container", "RegistrationError", "ResolutionError"]

T = TypeVar("T")


class ResolutionError(Exception):
    """Raised when the container cannot resolve a dependency.

    Common causes: missing registration, cyclic graph, unresolvable annotation
    (e.g., a parameter with no type hint), async factory invoked through
    ``resolve_sync``.
    """


class RegistrationError(Exception):
    """Raised when a registration is invalid (duplicate, conflicting lifetimes)."""


class Container:
    """The DI container.

    Construction is cheap; you usually have one root container per service plus
    a child scope per request. Containers are not thread-safe across event loops
    but are safe under asyncio concurrent tasks within a single loop.
    """

    def __init__(self, *, parent: Container | None = None, name: str = "root") -> None:
        self._parent = parent
        self.name = name
        self._providers: dict[Any, Provider[Any]] = {}
        # Singleton instances live on the root container; child containers
        # delegate up. The root scope is implicit (lifetime = process).
        self._singletons: dict[Any, Any] = {}
        self._singleton_disposers: list[Callable[[], Awaitable[None] | None]] = []
        self._singleton_lock = asyncio.Lock()
        self._overrides: dict[Any, Provider[Any]] = {}
        # Track in-flight resolutions per task for cycle detection.
        self._resolving: dict[int, set[Any]] = {}

    # ============================================================
    # Registration
    # ============================================================

    @overload
    def register(
        self,
        key: type[T],
        impl: type[T] | Callable[..., T] | Callable[..., Awaitable[T]],
        *,
        lifetime: Lifetime = Lifetime.TRANSIENT,
        tags: frozenset[str] | set[str] | tuple[str, ...] = (),
    ) -> Container: ...
    @overload
    def register(
        self,
        key: str,
        impl: Any,
        *,
        lifetime: Lifetime = Lifetime.TRANSIENT,
        tags: frozenset[str] | set[str] | tuple[str, ...] = (),
    ) -> Container: ...
    def register(
        self,
        key: Any,
        impl: Any,
        *,
        lifetime: Lifetime = Lifetime.TRANSIENT,
        tags: frozenset[str] | set[str] | tuple[str, ...] = (),
    ) -> Container:
        """Register a key → implementation binding.

        ``impl`` may be:
          - a class (the container will call its constructor)
          - a factory function (sync or async)
          - a pre-built instance (only valid with ``Lifetime.SINGLETON``)

        Re-registering a key replaces the previous registration. This is
        intentional for module loading order; if you want to detect collisions,
        use ``register_strict``.
        """
        provider = self._make_provider(key, impl, lifetime, tags)
        self._providers[key] = provider
        return self

    def register_singleton(
        self,
        key: type[T] | str,
        impl: Any,
        *,
        tags: frozenset[str] | set[str] | tuple[str, ...] = (),
    ) -> Container:
        return self.register(key, impl, lifetime=Lifetime.SINGLETON, tags=tags)

    def register_scoped(
        self,
        key: type[T] | str,
        impl: Any,
        *,
        tags: frozenset[str] | set[str] | tuple[str, ...] = (),
    ) -> Container:
        return self.register(key, impl, lifetime=Lifetime.SCOPED, tags=tags)

    def register_transient(
        self,
        key: type[T] | str,
        impl: Any,
        *,
        tags: frozenset[str] | set[str] | tuple[str, ...] = (),
    ) -> Container:
        return self.register(key, impl, lifetime=Lifetime.TRANSIENT, tags=tags)

    def register_instance(self, key: type[T] | str, instance: T) -> Container:
        """Register a pre-built singleton."""
        provider: Provider[T] = InstanceProvider(
            key=key,
            lifetime=Lifetime.SINGLETON,
            instance=instance,
        )
        self._providers[key] = provider
        # Pre-cache so resolution is a dict lookup.
        self._singletons[key] = instance
        return self

    def _make_provider(
        self,
        key: Any,
        impl: Any,
        lifetime: Lifetime,
        tags: frozenset[str] | set[str] | tuple[str, ...],
    ) -> Provider[Any]:
        tag_set = frozenset(tags) if not isinstance(tags, frozenset) else tags
        # A class or callable becomes a FactoryProvider; an arbitrary object
        # becomes an InstanceProvider iff singleton.
        if inspect.isclass(impl) or callable(impl):
            return FactoryProvider(
                key=key,
                lifetime=lifetime,
                tags=tag_set,
                factory=impl,
            )
        if lifetime is not Lifetime.SINGLETON:
            raise RegistrationError(
                f"Bare value for {key!r} requires SINGLETON lifetime (got {lifetime})"
            )
        return InstanceProvider(
            key=key,
            lifetime=lifetime,
            tags=tag_set,
            instance=impl,
        )

    # ============================================================
    # Resolution
    # ============================================================

    async def resolve(self, key: type[T] | str, *, scope: Scope | None = None) -> T:
        """Resolve a key. Walks the graph asynchronously."""
        return cast("T", await self._resolve(key, scope))

    def resolve_sync(self, key: type[T] | str, *, scope: Scope | None = None) -> T:
        """Resolve synchronously. Raises if any provider in the graph is async.

        Useful for test fixtures and CLI bootstrap. Don't use in request paths —
        that's what ``resolve`` is for, and it's already async-cheap.
        """
        # Pre-walk: refuse early if anything in the graph is async.
        self._check_sync_resolvable(key, set())

        # Drive the coroutine until completion synchronously. Anything that
        # suspends signals an unexpected await (e.g., a contended async lock)
        # and we refuse — keeping behavior predictable.
        coro = self._resolve(key, scope)
        try:
            coro.send(None)
        except StopIteration as e:
            return cast("T", e.value)
        except BaseException:
            coro.close()
            raise
        else:
            coro.close()
            raise ResolutionError(
                f"resolve_sync cannot resolve {key!r}: graph contains async providers"
            )

    def _check_sync_resolvable(self, key: Any, visited: set[Any]) -> None:
        if key in visited:
            return
        visited.add(key)
        provider, _owner = self._find_provider_with_owner(key)
        if provider is None:
            if inspect.isclass(key):
                # Auto-resolve as transient — walk its constructor.
                hint_target = key.__init__
                try:
                    hints = get_type_hints(hint_target)
                except Exception:
                    return
                for dep in hints.values():
                    if dep is Container or dep is Scope:
                        continue
                    self._check_sync_resolvable(dep, visited)
                return
            return  # missing-key error will surface later from the actual resolve
        if provider.is_async_provider:
            raise ResolutionError(
                f"resolve_sync cannot resolve {key!r}: provider {provider!r} is async"
            )
        # Walk constructor deps if it's a FactoryProvider with a class factory.
        factory = getattr(provider, "factory", None)
        if factory is None:
            return
        hint_target = factory.__init__ if inspect.isclass(factory) else factory
        try:
            hints = get_type_hints(hint_target)
        except Exception:
            return
        for dep in hints.values():
            if dep is Container or dep is Scope:
                continue
            if dep is type(None):
                continue
            self._check_sync_resolvable(dep, visited)

    async def _resolve(self, key: Any, scope: Scope | None) -> Any:
        # Cycle detection per task.
        task_id = id(asyncio.current_task())
        in_flight = self._resolving.setdefault(task_id, set())
        if key in in_flight:
            raise ResolutionError(
                f"Cyclic dependency detected on {key!r}: chain = {list(in_flight)}"
            )

        provider, owner = self._find_provider_with_owner(key)
        if provider is None:
            # If the key is a concrete class with a resolvable constructor, we
            # can auto-resolve without explicit registration. This is the
            # "convention over configuration" escape hatch — useful but should
            # be used sparingly; explicit registration is clearer.
            if inspect.isclass(key):
                provider = FactoryProvider(
                    key=key,
                    lifetime=Lifetime.TRANSIENT,
                    factory=key,
                )
                owner = self
            else:
                raise ResolutionError(
                    f"No provider registered for {key!r} and key is not a constructible class."
                )

        in_flight.add(key)
        try:
            return await self._produce(provider, scope, owner=owner or self)
        finally:
            in_flight.discard(key)
            if not in_flight:
                self._resolving.pop(task_id, None)

    def _find_provider(self, key: Any) -> Provider[Any] | None:
        provider, _ = self._find_provider_with_owner(key)
        return provider

    def _find_provider_with_owner(self, key: Any) -> tuple[Provider[Any] | None, Container | None]:
        # Override takes precedence (for tests).
        if key in self._overrides:
            return self._overrides[key], self
        if key in self._providers:
            return self._providers[key], self
        if self._parent is not None:
            return self._parent._find_provider_with_owner(key)
        return None, None

    async def _produce(
        self,
        provider: Provider[Any],
        scope: Scope | None,
        *,
        owner: Container,
    ) -> Any:
        key = provider.key
        lifetime = provider.lifetime

        if lifetime is Lifetime.SINGLETON:
            # Singletons are cached on the container that *owns* the provider so
            # child overrides genuinely shadow parent singletons.
            cached = owner._singletons.get(key)
            if cached is not None:
                return cached
            async with owner._singleton_lock:
                cached = owner._singletons.get(key)
                if cached is not None:
                    return cached
                value = await provider.provide(self)
                owner._singletons[key] = value
                owner._register_singleton_disposer(value)
                return value

        if lifetime is Lifetime.SCOPED:
            if scope is None:
                raise ResolutionError(
                    f"{key!r} is scoped but no scope is active. "
                    f"Use `async with container.scope() as s: await container.resolve(..., scope=s)`."
                )
            cached = scope.get(key)
            if cached is not None:
                return cached
            value = await provider.provide(self)
            scope.set(key, value)
            self._maybe_register_scope_disposer(scope, value)
            return value

        # TRANSIENT — propagate scope so transient factories can resolve SCOPED deps.
        return await provider.provide(self, scope=scope)

    @staticmethod
    def _maybe_register_scope_disposer(scope: Scope, value: Any) -> None:
        if hasattr(value, "aclose"):
            scope.register_disposer(value.aclose)
        elif hasattr(value, "close") and callable(value.close):
            scope.register_disposer(value.close)

    def _register_singleton_disposer(self, value: Any) -> None:
        if hasattr(value, "aclose"):
            self._singleton_disposers.append(value.aclose)
        elif hasattr(value, "close") and callable(value.close):
            self._singleton_disposers.append(value.close)

    def _root(self) -> Container:
        node = self
        while node._parent is not None:
            node = node._parent
        return node

    # ============================================================
    # Factory invocation
    # ============================================================

    async def _call_factory(self, factory: Any, key: Any, scope: Scope | None = None) -> Any:  # noqa: PLR0912
        """Resolve constructor parameters from the container and invoke."""
        try:
            sig = inspect.signature(factory)
        except (TypeError, ValueError) as e:
            raise ResolutionError(
                f"Cannot introspect signature of {factory!r} for {key!r}: {e}"
            ) from e

        # For a class, type hints live on __init__, not on the class itself.
        # For a function, get_type_hints on the function returns its hints directly.
        hint_target: Any
        hint_target = factory.__init__ if inspect.isclass(factory) else factory
        try:
            hints = get_type_hints(hint_target)
        except Exception:
            hints = {}

        kwargs: dict[str, Any] = {}
        for param_name, param in sig.parameters.items():
            if param_name == "self":
                continue
            if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                continue
            annot = hints.get(param_name, param.annotation)
            if annot is inspect.Parameter.empty:
                if param.default is inspect.Parameter.empty:
                    raise ResolutionError(
                        f"Parameter {param_name!r} of {factory!r} has no type annotation "
                        f"and no default. Annotate it or provide a default."
                    )
                continue  # Let the default kick in.
            # Container itself is injectable so factories can do advanced things.
            if annot is Container:
                kwargs[param_name] = self
                continue
            # Scope is injectable; hand the active scope to factories that need it.
            if annot is Scope:
                if scope is not None:
                    kwargs[param_name] = scope
                continue
            try:
                kwargs[param_name] = await self._resolve(annot, scope=scope)
            except ResolutionError:
                if param.default is inspect.Parameter.empty:
                    raise
                # Default present — let it apply.

        result = factory(**kwargs)
        if inspect.isawaitable(result):
            result = await result
        return result

    # ============================================================
    # Scope management
    # ============================================================

    @asynccontextmanager
    async def scope(self, name: str = "request") -> AsyncIterator[Scope]:
        """Open a new resolution scope.

        Use one per request, message, or job. Scoped providers resolved inside
        get cached in this scope and disposed on exit.
        """
        async with Scope(name=name) as s:
            yield s

    # ============================================================
    # Overrides / test seams
    # ============================================================

    @contextmanager
    def override(
        self, key: Any, impl: Any, *, lifetime: Lifetime = Lifetime.SINGLETON
    ) -> Iterator[None]:
        """Temporarily replace a registration. Restores on exit.

        Designed for tests::

            with container.override(EmailSender, FakeEmailSender()):
                ...

        Singleton cache for the key is invalidated on entry and on exit to avoid
        stale references.
        """
        previous = self._overrides.get(key)
        prev_singleton = self._singletons.pop(key, None)
        try:
            provider = self._make_provider(key, impl, lifetime, ())
            self._overrides[key] = provider
            yield
        finally:
            if previous is None:
                self._overrides.pop(key, None)
            else:
                self._overrides[key] = previous
            # Restore prior singleton if there was one.
            if prev_singleton is not None:
                self._singletons[key] = prev_singleton
            else:
                self._singletons.pop(key, None)

    # ============================================================
    # Hierarchy
    # ============================================================

    def create_child(self, name: str = "child") -> Container:
        """Create a child container. Child registrations shadow parent's for resolution."""
        return Container(parent=self, name=name)

    # ============================================================
    # Tag queries
    # ============================================================

    def providers_with_tag(self, tag: str) -> list[Provider[Any]]:
        """Return all providers (in this container + parents) carrying ``tag``."""
        out: list[Provider[Any]] = [p for p in self._providers.values() if tag in p.tags]
        if self._parent is not None:
            out.extend(self._parent.providers_with_tag(tag))
        return out

    # ============================================================
    # Teardown
    # ============================================================

    async def aclose(self) -> None:
        """Dispose of singleton resources. Idempotent."""
        errors: list[BaseException] = []
        for disposer in reversed(self._singleton_disposers):
            try:
                result = disposer()
                if asyncio.iscoroutine(result):
                    await result
            except BaseException as exc:
                errors.append(exc)
        self._singleton_disposers.clear()
        self._singletons.clear()
        if errors:
            raise BaseExceptionGroup("errors disposing singletons", errors)
