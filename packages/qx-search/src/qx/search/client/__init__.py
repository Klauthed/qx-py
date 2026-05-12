"""OpenSearch / Elasticsearch async client.

Thin wrapper around ``opensearchpy.AsyncOpenSearch`` that pulls config from
settings and exposes a couple of convenience methods. Anything beyond the
boring 80% (aggregations, suggesters, painless scripts) goes through the raw
client.
"""

from __future__ import annotations

from typing import ClassVar

from opensearchpy import AsyncOpenSearch
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["SearchSettings", "create_search_client"]


class SearchSettings(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="QX_SEARCH__",
        extra="ignore",
    )

    url: str = Field(default="http://localhost:9200")
    username: str | None = None
    password: SecretStr | None = None
    use_ssl: bool = False
    verify_certs: bool = False
    timeout_seconds: float = 5.0
    max_retries: int = 2
    retry_on_timeout: bool = True


def create_search_client(settings: SearchSettings) -> AsyncOpenSearch:
    """Build an async OpenSearch client. Dispose with ``await client.close()``."""
    http_auth = None
    if settings.username and settings.password:
        http_auth = (settings.username, settings.password.get_secret_value())
    return AsyncOpenSearch(
        hosts=[settings.url],
        http_auth=http_auth,
        use_ssl=settings.use_ssl,
        verify_certs=settings.verify_certs,
        timeout=settings.timeout_seconds,
        max_retries=settings.max_retries,
        retry_on_timeout=settings.retry_on_timeout,
    )
