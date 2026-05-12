"""Assertions for outbox-based event publishing.

Tests of command handlers that emit ``IntegrationEvent`` typically need to
verify two things:

1. The command succeeded (Result is success, return shape matches).
2. The right integration events were written to the outbox.

This module supplies ``OutboxAssert``: a small inspector that queries the
outbox table for events written during a given window or txn and offers
fluent matchers.

Usage::

    result = await mediator.send(CreateUserCommand(...))
    assert result.is_success

    outbox = OutboxAssert(engine)
    await outbox.assert_event_published(
        event_name="identity.user.registered",
        where={"email": "ada@example.com"},
    )
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from qx.db.outbox import OUTBOX_TABLE_NAME

__all__ = ["OutboxAssert"]


class OutboxAssert:
    def __init__(self, engine: AsyncEngine, *, table: str = OUTBOX_TABLE_NAME) -> None:
        self._engine = engine
        self._table = table

    async def list(self, event_name: str | None = None) -> list[dict[str, Any]]:
        async with self._engine.connect() as conn:
            q = f"SELECT id, event_name, event_version, payload, occurred_at FROM {self._table}"
            params: dict[str, Any] = {}
            if event_name is not None:
                q += " WHERE event_name = :n"
                params["n"] = event_name
            q += " ORDER BY occurred_at"
            rows = (await conn.execute(text(q), params)).mappings().all()
            return [dict(r) for r in rows]

    async def assert_event_published(
        self,
        event_name: str,
        *,
        where: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Assert at least one event matches; return the first match."""
        rows = await self.list(event_name=event_name)
        if not rows:
            raise AssertionError(
                f"expected at least one outbox row for {event_name!r}; found none"
            )
        if where is None:
            return rows[0]
        for row in rows:
            payload = row["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            envelope_payload = payload.get("payload", payload)
            if all(envelope_payload.get(k) == v for k, v in where.items()):
                return row
        raise AssertionError(
            f"no outbox row for {event_name!r} matched payload {where!r}; "
            f"found {len(rows)} row(s) but none matched"
        )

    async def assert_no_event(self, event_name: str) -> None:
        rows = await self.list(event_name=event_name)
        if rows:
            raise AssertionError(
                f"expected no outbox rows for {event_name!r}; found {len(rows)}"
            )

    async def clear(self) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(text(f"DELETE FROM {self._table}"))
