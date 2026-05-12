"""Index lifecycle helpers.

In production we expect indices to be managed declaratively (Terraform,
ECK operator manifests, etc.). These helpers exist for local development and
test setup, not for production schema management.

Two patterns supported:

- ``ensure_index(client, name, body)`` — idempotent create; no-op if exists.
- ``reindex(client, source, dest, ...)`` — async reindex with status polling.

Production deployments should treat these as glue, not policy.
"""

from __future__ import annotations

from typing import Any

from opensearchpy import AsyncOpenSearch

__all__ = ["ensure_index", "drop_index"]


async def ensure_index(
    client: AsyncOpenSearch,
    name: str,
    *,
    settings: dict[str, Any] | None = None,
    mappings: dict[str, Any] | None = None,
) -> bool:
    """Create the index if it doesn't already exist. Returns True if created."""
    if await client.indices.exists(index=name):
        return False
    body: dict[str, Any] = {}
    if settings:
        body["settings"] = settings
    if mappings:
        body["mappings"] = mappings
    await client.indices.create(index=name, body=body)
    return True


async def drop_index(client: AsyncOpenSearch, name: str) -> bool:
    """Delete the index if it exists. Returns True if deleted."""
    if not await client.indices.exists(index=name):
        return False
    await client.indices.delete(index=name)
    return True
