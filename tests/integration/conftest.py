"""Shared fixtures for cross-package integration tests.

Requires Docker. Containers start once per session.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest
from qx.testing.containers import nats_container, postgres_container

# Docker Desktop on macOS uses a non-standard socket path.
_DOCKER_DESKTOP_SOCK = Path.home() / ".docker" / "run" / "docker.sock"
if "DOCKER_HOST" not in os.environ and _DOCKER_DESKTOP_SOCK.exists():
    os.environ["DOCKER_HOST"] = f"unix://{_DOCKER_DESKTOP_SOCK}"


@pytest.fixture(scope="session")
def pg():  # type: ignore[no-untyped-def]
    with postgres_container() as container:
        yield container


@pytest.fixture(scope="session")
def db_url(pg) -> str:  # type: ignore[no-untyped-def]
    raw = pg.get_connection_url()
    url: str = re.sub(r"postgresql\+?\w*://", "postgresql+asyncpg://", raw, count=1)
    os.environ["QX_DB__URL"] = url
    yield url  # type: ignore[misc]
    del os.environ["QX_DB__URL"]


@pytest.fixture(scope="session")
def nats():  # type: ignore[no-untyped-def]
    with nats_container() as container:
        yield container


@pytest.fixture(scope="session")
def nats_url(nats) -> str:  # type: ignore[no-untyped-def]
    port = nats.get_exposed_port(4222)
    return f"nats://127.0.0.1:{port}"
