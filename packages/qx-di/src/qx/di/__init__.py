"""Qx dependency-injection container.

Public surface — import from ``qx.di``; internal layout may shift.

Quick start::

    from qx.di import Container, singleton, scan

    @singleton()
    class Clock:
        def now(self) -> datetime: ...

    @scoped(key=UserRepository)
    class PgUserRepository(UserRepository):
        def __init__(self, db: Database): ...

    container = Container()
    scan(container, "myapp.infrastructure")
    container.register_instance(Database, Database(url=...))

    async with container.scope() as s:
        repo = await container.resolve(UserRepository, scope=s)
"""

from __future__ import annotations

from qx.di.container import (
    Container,
    RegistrationError,
    ResolutionError,
)
from qx.di.decorators import (
    Injectable,
    get_metadata,
    injectable,
    scan,
    scoped,
    singleton,
    transient,
)
from qx.di.providers import (
    FactoryProvider,
    InstanceProvider,
    Lifetime,
    Provider,
)
from qx.di.scope import Scope

__version__ = "0.1.0"

__all__ = [
    "Container",
    "FactoryProvider",
    "Injectable",
    "InstanceProvider",
    "Lifetime",
    "Provider",
    "RegistrationError",
    "ResolutionError",
    "Scope",
    "__version__",
    "get_metadata",
    "injectable",
    "scan",
    "scoped",
    "singleton",
    "transient",
]
