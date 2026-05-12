"""Search package smoke tests — no live cluster required."""

from __future__ import annotations

from qx.search import SearchQuery, SearchSettings


def test_query_constructs() -> None:
    q = SearchQuery(text="hello", filters={"tenant_id": "t1"}, page=2, page_size=50)
    assert q.text == "hello"
    assert q.filters["tenant_id"] == "t1"
    assert q.page == 2
    assert q.page_size == 50


def test_settings_default_url() -> None:
    s = SearchSettings()
    assert "9200" in s.url
