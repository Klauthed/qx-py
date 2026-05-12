"""Reusable pytest fixtures.

Services import these in their ``conftest.py`` to get a working set of
fixtures with one line::

    from qx.testing.fixtures import *  # noqa

Or pick selectively::

    from qx.testing.fixtures import container, mediator, http_client  # noqa: PLC0415

The fixtures here are **not auto-registered** — pytest discovers fixtures in
the test file's conftest scope. Importing them is intentional so we keep the
testing module side-effect-free.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qx.di import Container

if TYPE_CHECKING:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from qx.cqrs import Mediator

__all__ = ["container_factory", "http_client_factory", "mediator_factory"]


def container_factory() -> Container:
    """Build an empty container suitable for unit tests."""
    return Container()


def mediator_factory(container: Container | None = None) -> Mediator:
    """Build a mediator backed by an optional container."""
    from qx.cqrs import Mediator as _Mediator  # noqa: PLC0415

    return _Mediator(container or Container())


def http_client_factory(app: FastAPI) -> TestClient:
    """Build a FastAPI TestClient for an app."""
    from fastapi.testclient import TestClient as _TestClient  # noqa: PLC0415

    return _TestClient(app)
