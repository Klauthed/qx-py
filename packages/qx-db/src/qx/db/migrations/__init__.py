"""Alembic integration helpers.

Services own their migration directory; the framework only supplies the
boilerplate to make ``alembic`` aware of the async engine and the framework's
naming conventions.

Typical service ``alembic/env.py`` calls into here::

    from qx.db.migrations import run_async_migrations
    from myservice.persistence import metadata

    run_async_migrations(metadata)

For schema-per-tenant deployments, use ``run_async_migrations_all_schemas``
instead — it discovers every tenant schema and runs the migration set in each::

    from qx.db.migrations import run_async_migrations_all_schemas

    run_async_migrations_all_schemas(metadata, schema_prefix="tenant_")

We don't ship an opinionated ``alembic.ini`` because version locations and
script templates are service-specific. The CLI scaffold (``qx`` CLI)
generates them.
"""

from __future__ import annotations

import asyncio
from typing import Any

from alembic import context
from sqlalchemy import MetaData, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

__all__ = ["run_async_migrations", "run_async_migrations_all_schemas"]


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


def run_async_migrations_all_schemas(
    metadata: MetaData,
    *,
    schema_prefix: str = "tenant_",
    compare_type: bool = True,
    compare_server_default: bool = True,
) -> list[str]:
    """Run Alembic migrations for every tenant schema matching ``schema_prefix``.

    Discovers all Postgres schemas whose names start with ``schema_prefix``,
    then runs the full migration set inside each schema by setting
    ``search_path`` before Alembic executes. Each schema gets its own
    ``alembic_version`` tracking table (stored inside the schema itself).

    Returns the list of schema names that were migrated.

    Call from ``alembic/env.py`` in services using schema-per-tenant
    isolation::

        from qx.db.migrations import run_async_migrations_all_schemas

        def run_migrations_online() -> None:
            migrated = run_async_migrations_all_schemas(
                metadata, schema_prefix="tenant_"
            )
            print(f"Migrated {len(migrated)} tenant schemas")
    """
    cfg = context.config
    return asyncio.run(_run_all_schemas(cfg, metadata, schema_prefix, compare_type, compare_server_default))


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


async def _run_all_schemas(
    cfg: Any,
    metadata: MetaData,
    schema_prefix: str,
    compare_type: bool,
    compare_server_default: bool,
) -> list[str]:
    section = cfg.get_section(cfg.config_ini_section) or {}
    engine = async_engine_from_config(section, prefix="sqlalchemy.", future=True)

    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT schema_name FROM information_schema.schemata"
                    " WHERE schema_name LIKE :pattern ORDER BY schema_name"
                ),
                {"pattern": f"{schema_prefix}%"},
            )
            schemas = [row[0] for row in result.fetchall()]

        migrated: list[str] = []
        for schema in schemas:
            async with engine.connect() as conn:
                await conn.run_sync(
                    _do_migrations_in_schema,
                    schema,
                    metadata,
                    compare_type,
                    compare_server_default,
                )
            migrated.append(schema)
    finally:
        await engine.dispose()

    return migrated


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


def _do_migrations_in_schema(
    connection: Connection,
    schema: str,
    metadata: MetaData,
    compare_type: bool,
    compare_server_default: bool,
) -> None:
    # Set search_path so DDL and the alembic_version table land in the
    # tenant schema rather than public.
    connection.execute(text(f'SET search_path TO "{schema}", public'))
    context.configure(
        connection=connection,
        target_metadata=metadata,
        compare_type=compare_type,
        compare_server_default=compare_server_default,
        include_schemas=False,
    )
    with context.begin_transaction():
        context.run_migrations()
