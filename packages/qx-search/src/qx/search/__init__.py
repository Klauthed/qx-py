"""Qx search layer (V2 stub).

The OpenSearch client + repository abstraction. Production deployments wire
the repository to a managed cluster; tests use the in-memory
``InMemorySearchRepository`` from ``qx-testing``.
"""

from __future__ import annotations

from qx.search.client import SearchSettings, create_search_client
from qx.search.indexing import drop_index, ensure_index
from qx.search.repository import SearchHit, SearchQuery, SearchRepository

__version__ = "0.1.0"

__all__ = [
    "SearchSettings",
    "create_search_client",
    "SearchRepository",
    "SearchQuery",
    "SearchHit",
    "ensure_index",
    "drop_index",
    "__version__",
]
