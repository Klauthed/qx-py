# qx-search

OpenSearch/Elasticsearch async client and repository abstraction for the Qx framework.

## What lives here

- **`qx.search.SearchRepository[TDoc]`** — abstract base class for document repositories. Implement `index_name` and `to_document` / `from_document` mapping; the base provides `index`, `get`, `delete`, and `search` methods.
- **`qx.search.SearchQuery`** — composable query builder: full-text query, filters, sort, pagination offset, and highlight options.
- **`qx.search.SearchHit[TDoc]`** — single search result with the mapped document, score, and raw `_source`.
- **`qx.search.SearchSettings`** — Pydantic settings for the OpenSearch/Elasticsearch endpoint URL and optional auth.
- **`qx.search.create_search_client`** — async factory that opens an `AsyncOpenSearch` connection.
- **`qx.search.ensure_index` / `drop_index`** — index lifecycle helpers; `ensure_index` creates an index with provided mapping if it doesn't exist (idempotent).

## Usage

```python
from qx.search import SearchRepository, SearchQuery, SearchSettings, create_search_client

class ProductSearchRepository(SearchRepository[ProductDoc]):
    index_name = "products"

    def to_document(self, product: Product) -> dict:
        return {"id": str(product.id), "name": product.name, "sku": product.sku}

    def from_document(self, doc: dict) -> ProductDoc:
        return ProductDoc(**doc)


# Bootstrap
client = await create_search_client(settings.search)
repo = ProductSearchRepository(client)

# Index a document
await repo.index(product)

# Search
hits = await repo.search(SearchQuery(q="widget", filters={"category": "tools"}, size=20))
```

## Design rules

- `SearchRepository` intentionally does not extend `qx.db.Repository` — search is a read projection, not the system of record. Write to the DB first, sync to search via an integration event handler.
- `ensure_index` is safe to call at startup on every deploy; it is a no-op if the index already exists with a compatible mapping.
- This package is a V2 scope item. The `InMemorySearchRepository` test double lives in `qx-testing`.
