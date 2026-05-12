"""Decorators for declarative DI registration and a module scanner.

Decorators **stamp** classes with metadata; they do not register on import (we
refuse import-time side effects). A scanner walks a package, finds stamped
classes, and registers them on a container.

This pattern keeps explicitness: nothing happens until you call ``scan()``.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import pkgutil
from collections.abc import Callable
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, TypeVar, cast

from qx.di.container import Container
from qx.di.providers import Lifetime

__all__ = [
    "injectable",
    "singleton",
    "scoped",
    "transient",
    "Injectable",
    "scan",
]


_T = TypeVar("_T", bound=type)
_MARKER_ATTR = "__qx_injectable__"


@dataclass(frozen=True)
class Injectable:
    """Metadata stamped on a class by an injection decorator."""

    key: type | str | None
    lifetime: Lifetime
    tags: frozenset[str] = field(default_factory=frozenset)


def _stamp(
    cls: _T,
    *,
    lifetime: Lifetime,
    key: type | str | None,
    tags: frozenset[str] | set[str] | tuple[str, ...],
) -> _T:
    tag_set = frozenset(tags) if not isinstance(tags, frozenset) else tags
    setattr(
        cls,
        _MARKER_ATTR,
        Injectable(key=key, lifetime=lifetime, tags=tag_set),
    )
    return cls


def injectable(
    key: type | str | None = None,
    *,
    lifetime: Lifetime = Lifetime.TRANSIENT,
    tags: frozenset[str] | set[str] | tuple[str, ...] = (),
) -> Callable[[_T], _T]:
    """Mark a class as DI-registerable. Default lifetime is transient."""

    def wrap(cls: _T) -> _T:
        return _stamp(cls, lifetime=lifetime, key=key, tags=tags)

    return wrap


def singleton(
    key: type | str | None = None,
    *,
    tags: frozenset[str] | set[str] | tuple[str, ...] = (),
) -> Callable[[_T], _T]:
    return injectable(key=key, lifetime=Lifetime.SINGLETON, tags=tags)


def scoped(
    key: type | str | None = None,
    *,
    tags: frozenset[str] | set[str] | tuple[str, ...] = (),
) -> Callable[[_T], _T]:
    return injectable(key=key, lifetime=Lifetime.SCOPED, tags=tags)


def transient(
    key: type | str | None = None,
    *,
    tags: frozenset[str] | set[str] | tuple[str, ...] = (),
) -> Callable[[_T], _T]:
    return injectable(key=key, lifetime=Lifetime.TRANSIENT, tags=tags)


def scan(
    container: Container,
    package: str | ModuleType,
    *,
    recursive: bool = True,
) -> int:
    """Walk a package, register every ``@injectable``-stamped class.

    Returns the number of registrations performed. The scanner is idempotent
    over a single call but re-scanning the same package replaces prior
    registrations (matching the container's register semantics).

    Subclasses or implementations bind to their declared ``key`` if given,
    otherwise to themselves. So::

        @singleton(key=UserRepository)
        class PostgresUserRepository(UserRepository): ...

    registers ``UserRepository → PostgresUserRepository``. Without ``key`` it
    registers ``PostgresUserRepository → PostgresUserRepository``.
    """
    mod = (
        importlib.import_module(package) if isinstance(package, str) else package
    )
    registered = 0
    seen: set[type] = set()

    def walk(module: ModuleType) -> None:
        nonlocal registered
        for name, obj in inspect.getmembers(module):
            if not inspect.isclass(obj):
                continue
            if obj in seen:
                continue
            seen.add(obj)
            meta: Injectable | None = getattr(obj, _MARKER_ATTR, None)
            # Only register if this class itself was stamped (avoid inherited tags).
            if meta is None or _MARKER_ATTR not in obj.__dict__:
                continue
            key = meta.key if meta.key is not None else obj
            container.register(
                key,
                obj,
                lifetime=meta.lifetime,
                tags=meta.tags,
            )
            registered += 1

    walk(mod)

    if recursive and hasattr(mod, "__path__"):
        for module_info in pkgutil.walk_packages(mod.__path__, prefix=f"{mod.__name__}."):
            try:
                submodule = importlib.import_module(module_info.name)
            except Exception:  # noqa: BLE001 — keep scanning despite individual failures
                continue
            walk(submodule)

    return registered


def get_metadata(cls: type) -> Injectable | None:
    """Read the injection metadata stamped on a class, if any."""
    return cast(Injectable | None, getattr(cls, _MARKER_ATTR, None))
