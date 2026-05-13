"""RegionConfig — pydantic settings for the current region."""

from __future__ import annotations

from typing import ClassVar

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["RegionConfig"]


class RegionConfig(BaseSettings):
    """Configuration for the current deployment region.

    Env vars: ``QX_REGION__NAME``, ``QX_REGION__URLS``.

    ``urls`` maps region name → base URL and is used by ``RegionRouter`` to
    build redirect targets::

        QX_REGION__NAME=us-east-1
        QX_REGION__URLS='{"us-east-1": "https://api.us.example.com", "eu-west-1": "https://api.eu.example.com"}'
    """

    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="QX_REGION__",
        extra="ignore",
    )

    name: str = Field(default="us-east-1", description="This region's identifier.")
    urls: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of region name → base URL for cross-region redirects.",
    )
