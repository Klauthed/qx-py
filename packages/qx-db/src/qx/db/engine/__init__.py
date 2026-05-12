"""Async engine and session factory.

Two-tier setup:

- One ``AsyncEngine`` per database, registered as a singleton.
- ``AsyncSession`` is **scoped** to a request: one session per HTTP request /
  message / job, opened by the unit-of-work and closed on scope exit.

We expose a ``DatabaseSettings`` Pydantic settings class that services pull
their DB config from. Settings live in ``qx-core`` style (``QX_DB__*``
env vars).

The engine is configured with pool_pre_ping (so DNS / TCP-stale connections
get caught) and a sane default pool size. NullPool is supported for serverless
runtimes (Lambda, Fly Machines) where a connection pool fights the platform.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, ClassVar, Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool, NullPool

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

__all__ = [
    "DatabaseSettings",
    "SessionFactory",
    "create_engine",
    "make_session_factory",
]


PoolKind = Literal["queue", "null"]


class DatabaseSettings(BaseSettings):
    """Database configuration.

    Env vars: ``QX_DB__URL``, ``QX_DB__POOL_SIZE``, etc.
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="QX_DB__",
        extra="ignore",
    )

    url: SecretStr = Field(
        default=SecretStr("postgresql+asyncpg://postgres:postgres@localhost:5432/qx")
    )
    pool_size: int = 10
    max_overflow: int = 10
    pool_timeout_seconds: float = 30.0
    pool_recycle_seconds: int = 1800  # recycle every 30 min, dodging upstream idle-cuts
    pool_pre_ping: bool = True
    pool_kind: PoolKind = "queue"
    echo: bool = False
    # Statement timeout applied via `SET LOCAL statement_timeout`; per-statement,
    # not connection-wide, so transactions can opt out via their own SET.
    statement_timeout_ms: int = 10_000


def create_engine(settings: DatabaseSettings) -> AsyncEngine:
    """Build an async engine.

    The engine is a long-lived, thread-safe object. Treat it as a singleton.
    Disposing it (``await engine.dispose()``) closes all pooled connections —
    do that at process shutdown.
    """
    if settings.pool_kind == "null":
        engine = create_async_engine(
            settings.url.get_secret_value(),
            poolclass=NullPool,
            echo=settings.echo,
            future=True,
        )
    else:
        engine = create_async_engine(
            settings.url.get_secret_value(),
            poolclass=AsyncAdaptedQueuePool,
            pool_size=settings.pool_size,
            max_overflow=settings.max_overflow,
            pool_timeout=settings.pool_timeout_seconds,
            pool_recycle=settings.pool_recycle_seconds,
            pool_pre_ping=settings.pool_pre_ping,
            echo=settings.echo,
            future=True,
        )
    return engine


SessionFactory = async_sessionmaker[AsyncSession]


def make_session_factory(engine: AsyncEngine) -> SessionFactory:
    """Build a session factory. Singleton per engine."""
    return async_sessionmaker(
        bind=engine,
        expire_on_commit=False,  # let aggregates be read after commit without re-fetch
        autoflush=False,  # we control flushes explicitly via the UoW
        class_=AsyncSession,
    )


@asynccontextmanager
async def open_session(factory: SessionFactory) -> AsyncIterator[AsyncSession]:
    """Open a session in an async context manager. Closes regardless of outcome."""
    session = factory()
    try:
        yield session
    finally:
        await session.close()
