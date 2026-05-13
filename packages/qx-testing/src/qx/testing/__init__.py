"""Qx testing helpers."""

from __future__ import annotations

from qx.testing.assertions import OutboxAssert
from qx.testing.containers import (
    nats_container,
    postgres_container,
    redis_container,
)
from qx.testing.doubles import (
    FlagClientStub,
    InMemorySearchRepository,
    MediatorStub,
    RepositoryStub,
)
from qx.testing.fixtures import (
    container_factory,
    http_client_factory,
    mediator_factory,
)

__version__ = "0.2.0"

__all__ = [
    # Doubles
    "FlagClientStub",
    "InMemorySearchRepository",
    "MediatorStub",
    # Assertions
    "OutboxAssert",
    "RepositoryStub",
    "__version__",
    # Fixtures
    "container_factory",
    "http_client_factory",
    "mediator_factory",
    "nats_container",
    # Containers
    "postgres_container",
    "redis_container",
]
