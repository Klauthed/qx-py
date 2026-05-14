"""Integration test fixtures — requires Docker (testcontainers)."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from prometheus_client import CollectorRegistry
from qx.testing.containers import postgres_container

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

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
def engine(db_url: str) -> AsyncEngine:
    from order_service.infrastructure.projections.order_summary import (  # noqa: PLC0415
        include_order_summaries_table,
    )
    from qx.db.outbox import include_outbox_table  # noqa: PLC0415
    from qx.eventstore import include_eventstore_tables  # noqa: PLC0415
    from qx.projections import include_projection_tables  # noqa: PLC0415
    from sqlalchemy import MetaData  # noqa: PLC0415
    from sqlalchemy.ext.asyncio import create_async_engine  # noqa: PLC0415
    from sqlalchemy.pool import NullPool  # noqa: PLC0415

    eng = create_async_engine(db_url, echo=False, poolclass=NullPool)

    async def _create_schema() -> None:
        meta = MetaData()
        include_eventstore_tables(meta)
        include_projection_tables(meta)
        include_order_summaries_table(meta)
        include_outbox_table(meta)
        async with eng.begin() as conn:
            await conn.run_sync(meta.create_all)

    asyncio.run(_create_schema())
    yield eng  # type: ignore[misc]
    asyncio.run(eng.dispose())


@pytest.fixture()
def client(db_url: str, engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    import order_service.main as main_mod  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from qx.observability import setup_observability as orig  # noqa: PLC0415

    fresh_registry = CollectorRegistry()

    def _patched(*args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["metrics_registry"] = fresh_registry
        return orig(*args, **kwargs)

    monkeypatch.setattr(main_mod, "setup_observability", _patched)
    with TestClient(main_mod.build_app()) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean_tables(db_url: str) -> None:  # type: ignore[misc]
    yield  # type: ignore[misc]
    from sqlalchemy import text  # noqa: PLC0415
    from sqlalchemy.ext.asyncio import create_async_engine  # noqa: PLC0415
    from sqlalchemy.pool import NullPool  # noqa: PLC0415

    async def _truncate() -> None:
        eng = create_async_engine(db_url, echo=False, poolclass=NullPool)
        try:
            async with eng.begin() as conn:
                await conn.execute(
                    text(
                        "TRUNCATE TABLE qx_aggregate_events, qx_aggregate_snapshots,"
                        " qx_projection_checkpoints, qx_order_summaries, qx_outbox_events"
                        " RESTART IDENTITY CASCADE"
                    )
                )
        finally:
            await eng.dispose()

    asyncio.run(_truncate())
