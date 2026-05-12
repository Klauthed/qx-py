"""Layered configuration via :mod:`pydantic_settings`.

Resolution order (highest priority first):

1. Explicit constructor arguments
2. Environment variables (prefix: ``QX_``)
3. ``.env.{environment}`` file
4. ``.env.local`` file
5. ``.env`` file
6. Defaults declared on the settings class

The ``environment`` selector itself comes from the env var ``QX_ENV``
(``local`` | ``dev`` | ``staging`` | ``prod``). Default is ``local``.

Services subclass ``QxSettings`` and add their own sections. Avoid putting
service-specific knobs on the framework base — those belong to the service.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["AppSection", "Environment", "LoggingSection", "QxSettings"]

Environment = Literal["local", "dev", "staging", "prod", "test"]


def _detect_env() -> Environment:
    raw = os.environ.get("QX_ENV", "local").lower()
    if raw in {"local", "dev", "staging", "prod", "test"}:
        return raw  # type: ignore[return-value]
    return "local"


def _env_file_chain() -> tuple[str, ...]:
    """Return the chain of .env files, lowest priority first.

    pydantic-settings merges multiple env files with later ones overriding
    earlier ones. We want the most-specific file to override less-specific.
    """
    env = _detect_env()
    cwd = Path.cwd()
    candidates: list[Path] = [
        cwd / ".env",
        cwd / ".env.local",
        cwd / f".env.{env}",
    ]
    return tuple(str(p) for p in candidates if p.exists())


class AppSection(BaseSettings):
    name: str = Field(default="qx-service")
    version: str = Field(default="0.0.0")
    instance_id: str | None = Field(default=None)


class LoggingSection(BaseSettings):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    json_output: bool = True
    include_caller: bool = False


class QxSettings(BaseSettings):
    """Base settings.

    Services should subclass and add their own sub-models (database, redis,
    nats, third-party integrations). Keep secrets out of defaults — pydantic
    will load them from env at runtime.
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="QX_",
        env_nested_delimiter="__",
        env_file=_env_file_chain(),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Environment = Field(default_factory=_detect_env)
    app: AppSection = Field(default_factory=AppSection)
    logging: LoggingSection = Field(default_factory=LoggingSection)
