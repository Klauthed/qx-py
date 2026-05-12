"""Cursor pagination helpers.

We use **keyset pagination**: the cursor encodes the sort key + tiebreaker
(id) of the last row in the previous page. The next query becomes ``WHERE
(sort_key, id) > (last_sort_key, last_id) ORDER BY sort_key, id LIMIT N``.

Cursors are base64'd JSON internally. Clients treat them as opaque tokens —
they should never parse them. This lets us evolve the cursor format (add
fields, change sort key) without breaking clients.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from qx.core import CursorPage, CursorPagination
from qx.core.types.pagination import Sort

__all__ = ["encode_cursor", "decode_cursor", "build_cursor_page"]


def encode_cursor(values: dict[str, Any]) -> str:
    raw = json.dumps(values, default=_serialize, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    pad = "=" * (-len(cursor) % 4)
    raw = base64.urlsafe_b64decode(cursor + pad).decode()
    return json.loads(raw)


def _serialize(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    return str(o)


def build_cursor_page(
    items: Sequence[Any],
    *,
    limit: int,
    sort: Sequence[Sort],
    cursor_fields: Sequence[str],
) -> CursorPage[Any]:
    """Build a ``CursorPage`` from query results.

    The caller fetches ``limit + 1`` rows; if ``len(items) > limit`` we trim
    and emit a cursor pointing at the last yielded row. This is the textbook
    "fetch one extra to know if there's a next page" trick.
    """
    has_next = len(items) > limit
    yielded = list(items[:limit])
    next_cursor: str | None = None
    if has_next and yielded:
        last = yielded[-1]
        cursor_payload = {f: getattr(last, f, None) for f in cursor_fields}
        next_cursor = encode_cursor(cursor_payload)
    return CursorPage(items=yielded, next_cursor=next_cursor, has_next=has_next)
