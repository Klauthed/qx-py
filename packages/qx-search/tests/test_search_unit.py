"""Search package unit tests — no live cluster required."""

from __future__ import annotations

import dataclasses
from unittest.mock import AsyncMock

from qx.search import OpenSearchRepository, SearchQuery, SearchSettings
from qx.testing import InMemorySearchRepository


def test_query_constructs() -> None:
    q = SearchQuery(text="hello", filters={"tenant_id": "t1"}, page=2, page_size=50)
    assert q.text == "hello"
    assert q.filters["tenant_id"] == "t1"
    assert q.page == 2
    assert q.page_size == 50


def test_settings_default_url() -> None:
    s = SearchSettings()
    assert "9200" in s.url


# ---- InMemorySearchRepository ----


@dataclasses.dataclass
class _Product:
    name: str
    category: str
    price: float


async def test_inmemory_index_and_search_by_text() -> None:
    repo: InMemorySearchRepository[_Product] = InMemorySearchRepository()
    await repo.index("p1", _Product(name="Red shoes", category="footwear", price=49.99))
    await repo.index("p2", _Product(name="Blue hat", category="headwear", price=19.99))

    result = await repo.search(SearchQuery(text="shoes"))
    assert result.is_success
    hits, total = result.value
    assert total == 1
    assert hits[0].doc.name == "Red shoes"


async def test_inmemory_filter_exact_match() -> None:
    repo: InMemorySearchRepository[_Product] = InMemorySearchRepository()
    await repo.index("p1", _Product(name="Red shoes", category="footwear", price=49.99))
    await repo.index("p2", _Product(name="Blue hat", category="headwear", price=19.99))

    result = await repo.search(SearchQuery(filters={"category": "headwear"}))
    hits, total = result.value
    assert total == 1
    assert hits[0].doc.category == "headwear"


async def test_inmemory_text_and_filter_combined() -> None:
    repo: InMemorySearchRepository[_Product] = InMemorySearchRepository()
    await repo.index("p1", _Product(name="Red shoes", category="footwear", price=49.99))
    await repo.index("p2", _Product(name="Red hat", category="headwear", price=19.99))

    result = await repo.search(SearchQuery(text="red", filters={"category": "footwear"}))
    hits, total = result.value
    assert total == 1
    assert hits[0].doc.name == "Red shoes"


async def test_inmemory_delete_is_idempotent() -> None:
    repo: InMemorySearchRepository[_Product] = InMemorySearchRepository()
    await repo.index("p1", _Product(name="Red shoes", category="footwear", price=49.99))
    await repo.delete("p1")
    await repo.delete("p1")  # second delete should not raise

    result = await repo.search(SearchQuery())
    _, total = result.value
    assert total == 0


async def test_inmemory_pagination() -> None:
    repo: InMemorySearchRepository[_Product] = InMemorySearchRepository()
    for i in range(5):
        await repo.index(f"p{i}", _Product(name=f"Item {i}", category="misc", price=float(i)))

    result = await repo.search(SearchQuery(page=2, page_size=2))
    hits, total = result.value
    assert total == 5
    assert len(hits) == 2


async def test_inmemory_preload() -> None:
    repo: InMemorySearchRepository[_Product] = InMemorySearchRepository()
    repo.preload("p1", _Product(name="Preloaded", category="test", price=0.0))

    result = await repo.search(SearchQuery(text="preloaded"))
    _, total = result.value
    assert total == 1


async def test_inmemory_sort_descending() -> None:
    repo: InMemorySearchRepository[_Product] = InMemorySearchRepository()
    await repo.index("p1", _Product(name="Apple", category="fruit", price=1.0))
    await repo.index("p2", _Product(name="Mango", category="fruit", price=3.0))
    await repo.index("p3", _Product(name="Banana", category="fruit", price=2.0))

    result = await repo.search(SearchQuery(sort=[("name", "desc")]))
    hits, _ = result.value
    names = [h.doc.name for h in hits]
    assert names == sorted(names, reverse=True)


# ---- OpenSearchRepository DSL builder ----


@dataclasses.dataclass
class _Doc:
    title: str


def test_dsl_match_all_when_no_query() -> None:
    repo = OpenSearchRepository(client=AsyncMock(), index="idx", doc_type=_Doc)
    dsl = repo._build_dsl(SearchQuery())
    assert dsl == {"query": {"match_all": {}}}


def test_dsl_text_only() -> None:
    repo = OpenSearchRepository(client=AsyncMock(), index="idx", doc_type=_Doc)
    dsl = repo._build_dsl(SearchQuery(text="hello"))
    assert dsl["query"]["bool"]["must"][0]["multi_match"]["query"] == "hello"
    assert "filter" not in dsl["query"]["bool"]


def test_dsl_filter_only() -> None:
    repo = OpenSearchRepository(client=AsyncMock(), index="idx", doc_type=_Doc)
    dsl = repo._build_dsl(SearchQuery(filters={"status": "active"}))
    assert dsl["query"]["bool"]["filter"] == [{"term": {"status": "active"}}]
    assert "must" not in dsl["query"]["bool"]


def test_dsl_sort_appended() -> None:
    repo = OpenSearchRepository(client=AsyncMock(), index="idx", doc_type=_Doc)
    dsl = repo._build_dsl(SearchQuery(sort=[("created_at", "desc")]))
    assert dsl["sort"] == [{"created_at": {"order": "desc"}}]
