"""Testcontainers helpers for integration tests.

Provides session-scoped containers for Postgres / Redis / NATS that integration
tests share. Containers spin up once per pytest session, run all tests in
parallel against the same instances (each test owning its own schema /
namespace / subject), and tear down cleanly at the end.

Usage in a service's ``conftest.py``::

    from qx.testing.containers import postgres_container, redis_container  # noqa: PLC0415

    @pytest.fixture(scope="session")
    def postgres():
        with postgres_container() as pg:
            yield pg

These helpers don't enforce a session lifetime — the caller decides. We
recommend session scope because container startup dominates test run time.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = [
    "postgres_container",
    "redis_container",
]


@contextmanager
def postgres_container(
    *,
    image: str = "postgres:16-alpine",
    dbname: str = "qx_test",
    username: str = "postgres",
    password: str = "postgres",
) -> Iterator[Any]:
    """Spin up a Postgres container. Yields the container instance.

    ``container.get_connection_url()`` returns a ``postgresql://...`` URL
    suitable for SQLAlchemy. For asyncpg, swap the driver part::

        url = container.get_connection_url().replace("postgresql://", "postgresql+asyncpg://")
    """
    from testcontainers.postgres import PostgresContainer  # noqa: PLC0415

    container = PostgresContainer(
        image=image,
        dbname=dbname,
        username=username,
        password=password,
    )
    container.start()
    try:
        yield container
    finally:
        container.stop()


@contextmanager
def redis_container(*, image: str = "redis:7-alpine") -> Iterator[Any]:
    from testcontainers.redis import RedisContainer  # noqa: PLC0415

    container = RedisContainer(image=image)
    container.start()
    try:
        yield container
    finally:
        container.stop()


@contextmanager
def nats_container(*, image: str = "nats:2.10-alpine") -> Iterator[Any]:
    """Generic-container NATS — testcontainers has no specialized class for it.

    Starts with JetStream enabled (``-js``) so integration tests of the outbox
    relay / consumer work out of the box.
    """
    from testcontainers.core.container import DockerContainer  # noqa: PLC0415
    from testcontainers.core.waiting_utils import wait_for_logs  # noqa: PLC0415

    container = DockerContainer(image=image)
    container.with_command("-js")
    container.with_exposed_ports(4222, 8222)
    container.start()
    wait_for_logs(container, "Server is ready", timeout=30)
    try:
        yield container
    finally:
        container.stop()
