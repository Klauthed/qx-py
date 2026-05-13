"""Search package unit tests — no live cluster required."""

from __future__ import annotations

import dataclasses
from unittest.mock import AsyncMock

from qx.search import BulkIndexResult, OpenSearchRepository, SearchQuery, SearchSettings
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


# ---- bulk_index (InMemorySearchRepository) ----


async def test_bulk_index_indexes_all_docs() -> None:
    repo: InMemorySearchRepository[_Product] = InMemorySearchRepository()
    docs = [
        (f"p{i}", _Product(name=f"Item {i}", category="misc", price=float(i))) for i in range(5)
    ]

    result = await repo.bulk_index(docs)

    assert result.is_success
    r: BulkIndexResult = result.value
    assert r.indexed == 5
    assert r.failed == 0
    assert r.errors == []

    _, total = (await repo.search(SearchQuery())).value
    assert total == 5


async def test_bulk_index_overwrites_existing_doc() -> None:
    repo: InMemorySearchRepository[_Product] = InMemorySearchRepository()
    await repo.index("p1", _Product(name="Old", category="a", price=1.0))

    await repo.bulk_index([("p1", _Product(name="New", category="a", price=2.0))])

    hits, _ = (await repo.search(SearchQuery(text="new"))).value
    assert len(hits) == 1
    assert hits[0].doc.name == "New"


async def test_bulk_index_empty_sequence_is_noop() -> None:
    repo: InMemorySearchRepository[_Product] = InMemorySearchRepository()
    result = await repo.bulk_index([])
    assert result.is_success
    assert result.value.indexed == 0


# ---- scroll (InMemorySearchRepository) ----


async def test_scroll_yields_all_docs_in_batches() -> None:
    repo: InMemorySearchRepository[_Product] = InMemorySearchRepository()
    for i in range(7):
        await repo.index(f"p{i}", _Product(name=f"Item {i}", category="misc", price=float(i)))

    batches = []
    async for batch in repo.scroll(SearchQuery(), batch_size=3):
        batches.append(batch)

    assert len(batches) == 3  # 3 + 3 + 1
    assert sum(len(b) for b in batches) == 7


async def test_scroll_applies_filters() -> None:
    repo: InMemorySearchRepository[_Product] = InMemorySearchRepository()
    await repo.index("a", _Product(name="Alpha", category="x", price=1.0))
    await repo.index("b", _Product(name="Beta", category="y", price=2.0))
    await repo.index("c", _Product(name="Gamma", category="x", price=3.0))

    collected = []
    async for batch in repo.scroll(SearchQuery(filters={"category": "x"}), batch_size=10):
        collected.extend(batch)

    assert len(collected) == 2
    assert all(h.doc.category == "x" for h in collected)


async def test_scroll_empty_index_yields_nothing() -> None:
    repo: InMemorySearchRepository[_Product] = InMemorySearchRepository()
    batches = [b async for b in repo.scroll(SearchQuery())]
    assert batches == []


# ---- bulk_index (OpenSearchRepository DSL / mock) ----


async def test_os_bulk_index_success() -> None:
    client = AsyncMock()
    client.bulk = AsyncMock(return_value={"errors": False, "items": []})
    repo = OpenSearchRepository(client=client, index="idx", doc_type=_Doc)

    result = await repo.bulk_index([("d1", _Doc(title="Hello")), ("d2", _Doc(title="World"))])

    assert result.is_success
    assert result.value.indexed == 2
    assert result.value.failed == 0
    client.bulk.assert_called_once()


async def test_os_bulk_index_partial_failure() -> None:
    client = AsyncMock()
    client.bulk = AsyncMock(
        return_value={
            "errors": True,
            "items": [
                {"index": {"_id": "d1", "status": 201}},
                {"index": {"_id": "d2", "status": 400, "error": {"reason": "bad mapping"}}},
            ],
        }
    )
    repo = OpenSearchRepository(client=client, index="idx", doc_type=_Doc)

    result = await repo.bulk_index([("d1", _Doc(title="ok")), ("d2", _Doc(title="bad"))])

    assert result.is_success
    r = result.value
    assert r.indexed == 1
    assert r.failed == 1
    assert r.errors[0].doc_id == "d2"
    assert "bad mapping" in r.errors[0].error


async def test_os_bulk_index_respects_batch_size() -> None:
    client = AsyncMock()
    client.bulk = AsyncMock(return_value={"errors": False, "items": []})
    repo = OpenSearchRepository(client=client, index="idx", doc_type=_Doc)

    docs = [(f"d{i}", _Doc(title=f"Doc {i}")) for i in range(5)]
    await repo.bulk_index(docs, batch_size=2)

    # 5 docs at batch_size=2 → 3 calls (2+2+1)
    assert client.bulk.call_count == 3


# ---- scroll (OpenSearchRepository mock) ----


def _scroll_response(hits_data: list[dict], scroll_id: str = "sid") -> dict:
    return {
        "_scroll_id": scroll_id,
        "hits": {
            "hits": [{"_source": h, "_score": 1.0} for h in hits_data],
            "total": {"value": len(hits_data)},
        },
    }


async def test_os_scroll_yields_all_batches() -> None:
    client = AsyncMock()
    client.search = AsyncMock(return_value=_scroll_response([{"title": "A"}, {"title": "B"}]))
    client.scroll = AsyncMock(
        side_effect=[
            _scroll_response([{"title": "C"}]),
            _scroll_response([]),
        ]
    )
    client.clear_scroll = AsyncMock()
    repo = OpenSearchRepository(client=client, index="idx", doc_type=_Doc)

    collected = []
    async for batch in repo.scroll(SearchQuery(), batch_size=2):
        collected.extend(batch)

    assert len(collected) == 3
    assert [h.doc.title for h in collected] == ["A", "B", "C"]
    client.clear_scroll.assert_called_once_with(scroll_id="sid")


async def test_os_scroll_clears_scroll_on_explicit_close() -> None:
    client = AsyncMock()
    client.search = AsyncMock(
        return_value=_scroll_response([{"title": "A"}, {"title": "B"}, {"title": "C"}])
    )
    client.clear_scroll = AsyncMock()
    repo = OpenSearchRepository(client=client, index="idx", doc_type=_Doc)

    gen = repo.scroll(SearchQuery(), batch_size=10)
    async for _batch in gen:
        break
    await gen.aclose()  # explicit close guarantees synchronous finally execution

    client.clear_scroll.assert_called_once_with(scroll_id="sid")
