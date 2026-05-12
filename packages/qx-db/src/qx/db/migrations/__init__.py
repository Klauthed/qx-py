"""Alembic integration helpers.

Services own their migration directory; the framework only supplies the
boilerplate to make ``alembic`` aware of the async engine and the framework's
naming conventions.

Typical service ``alembic/env.py`` calls into here::

    from qx.db.migrations import run_async_migrations
    from myservice.persistence import metadata

    run_async_migrations(metadata)

We don't ship an opinionated ``alembic.ini`` because version locations and
script templates are service-specific. The CLI scaffold (``qx`` CLI)
generates them.
"""

from __future__ import annotations

import asyncio
from typing import Any

from alembic import context
from sqlalchemy import MetaData
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

__all__ = ["run_async_migrations"]


def run_async_migrations(
    metadata: MetaData,
    *,
    compare_type: bool = True,
    compare_server_default: bool = True,
    include_schemas: bool = False,
) -> None:
    """Run Alembic migrations against an async engine.

    Call from ``alembic/env.py``. Picks up the connection URL from the
    ``alembic.ini`` ``sqlalchemy.url`` (which services typically set from
    ``QX_DB__URL``).

    ``compare_type`` and ``compare_server_default`` are on by default so
    autogenerate catches the largest class of schema drift.
    """
    cfg = context.config
    if context.is_offline_mode():
        context.configure(
            url=cfg.get_main_option("sqlalchemy.url"),
            target_metadata=metadata,
            compare_type=compare_type,
            compare_server_default=compare_server_default,
            include_schemas=include_schemas,
            literal_binds=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    asyncio.run(_run_online(cfg, metadata, compare_type, compare_server_default, include_schemas))


async def _run_online(
    cfg: Any,
    metadata: MetaData,
    compare_type: bool,
    compare_server_default: bool,
    include_schemas: bool,
) -> None:
    section = cfg.get_section(cfg.config_ini_section) or {}
    engine = async_engine_from_config(
        section,
        prefix="sqlalchemy.",
        future=True,
    )
    async with engine.connect() as conn:
        await conn.run_sync(
            _do_migrations,
            metadata,
            compare_type,
            compare_server_default,
            include_schemas,
        )
    await engine.dispose()


def _do_migrations(
    connection: Connection,
    metadata: MetaData,
    compare_type: bool,
    compare_server_default: bool,
    include_schemas: bool,
) -> None:
    context.configure(
        connection=connection,
        target_metadata=metadata,
        compare_type=compare_type,
        compare_server_default=compare_server_default,
        include_schemas=include_schemas,
    )
    with context.begin_transaction():
        context.run_migrations()
