"""Tests for qx-db pure logic (cursor encode/decode, outbox table shape).

The repository / UoW / outbox-relay integration tests live under
``tests/integration`` and require Docker (testcontainers). Those run in CI
against a real Postgres; here we keep the unit tests fast.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import MetaData

from qx.db.outbox import OUTBOX_TABLE_NAME, include_outbox_table
from qx.db.pagination import (
    build_cursor_page,
    decode_cursor,
    encode_cursor,
)
from qx.core.types.pagination import Sort


def test_cursor_roundtrip_strings() -> None:
    payload = {"id": str(uuid4()), "created_at": "2024-01-01T00:00:00+00:00"}
    cursor = encode_cursor(payload)
    decoded = decode_cursor(cursor)
    assert decoded == payload


def test_cursor_handles_datetime() -> None:
    payload = {"created_at": datetime(2024, 1, 1, tzinfo=UTC)}
    cursor = encode_cursor(payload)
    decoded = decode_cursor(cursor)
    assert decoded["created_at"].startswith("2024-01-01")


def test_cursor_is_opaque_text() -> None:
    cursor = encode_cursor({"id": "x"})
    # Should be safe to put in a URL — base64url, no padding.
    assert "=" not in cursor
    assert "/" not in cursor


def test_build_cursor_page_returns_trimmed_items_and_cursor() -> None:
    from types import SimpleNamespace

    rows = [SimpleNamespace(id=str(i), created_at=datetime(2024, 1, i + 1, tzinfo=UTC)) for i in range(6)]
    page = build_cursor_page(
        rows,
        limit=5,
        sort=(Sort(field="created_at", direction="asc"),),
        cursor_fields=("created_at", "id"),
    )
    assert len(page.items) == 5
    assert page.has_next is True
    assert page.next_cursor is not None


def test_build_cursor_page_no_next_when_under_limit() -> None:
    from types import SimpleNamespace

    rows = [SimpleNamespace(id=str(i)) for i in range(3)]
    page = build_cursor_page(rows, limit=5, sort=(), cursor_fields=("id",))
    assert page.has_next is False
    assert page.next_cursor is None


def test_outbox_table_registers_in_metadata() -> None:
    md = MetaData()
    table = include_outbox_table(md)
    assert OUTBOX_TABLE_NAME in md.tables
    assert "event_name" in table.c
    assert "payload" in table.c
    assert "published_at" in table.c
