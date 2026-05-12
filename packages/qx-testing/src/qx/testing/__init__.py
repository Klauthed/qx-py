"""Qx testing helpers."""

from __future__ import annotations

from qx.testing.assertions import OutboxAssert
from qx.testing.containers import (
    nats_container,
    postgres_container,
    redis_container,
)
from qx.testing.doubles import MediatorStub, RepositoryStub
from qx.testing.fixtures import (
    container_factory,
    http_client_factory,
    mediator_factory,
)

__version__ = "0.1.0"

__all__ = [
    # Containers
    "postgres_container",
    "redis_container",
    "nats_container",
    # Doubles
    "MediatorStub",
    "RepositoryStub",
    # Assertions
    "OutboxAssert",
    # Fixtures
    "container_factory",
    "mediator_factory",
    "http_client_factory",
    "__version__",
]
