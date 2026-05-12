"""Cache unit tests — Redis-free.

The integration tests (real client, real lock contention, real Lua) run under
``tests/integration`` with a testcontainers Redis.
"""

from __future__ import annotations

from qx.cache import IdempotencyStore


def test_fingerprint_is_stable_across_key_order() -> None:
    a = IdempotencyStore.fingerprint({"a": 1, "b": 2})
    b = IdempotencyStore.fingerprint({"b": 2, "a": 1})
    assert a == b


def test_fingerprint_differs_on_value_change() -> None:
    a = IdempotencyStore.fingerprint({"a": 1})
    b = IdempotencyStore.fingerprint({"a": 2})
    assert a != b


def test_fingerprint_handles_nested_structures() -> None:
    a = IdempotencyStore.fingerprint({"users": [{"id": 1}, {"id": 2}]})
    b = IdempotencyStore.fingerprint({"users": [{"id": 1}, {"id": 2}]})
    assert a == b
