"""Integration tests for the /v1/users HTTP endpoints.

Runs against a real Postgres container (testcontainers). Each test gets a
clean table state via the autouse ``_clean_tables`` fixture in conftest.py.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest
    from fastapi.testclient import TestClient
    from qx.testing.assertions import OutboxAssert
    from sqlalchemy.ext.asyncio import AsyncEngine


# ── helpers ──────────────────────────────────────────────────────────────────


def create_user(client: TestClient, email: str = "ada@example.com", name: str = "Ada") -> dict:
    r = client.post("/v1/users", json={"email": email, "name": name})
    assert r.status_code == 201, r.json()
    return r.json()["data"]


# ── create ────────────────────────────────────────────────────────────────────


def test_create_user_returns_201(client: TestClient) -> None:
    r = client.post("/v1/users", json={"email": "ada@example.com", "name": "Ada Lovelace"})
    assert r.status_code == 201
    body = r.json()
    assert body["success"] is True
    data = body["data"]
    assert data["email"] == "ada@example.com"
    assert data["name"] == "Ada Lovelace"
    assert "id" in data


def test_create_user_duplicate_email_returns_409(client: TestClient) -> None:
    client.post("/v1/users", json={"email": "ada@example.com", "name": "Ada"})
    r = client.post("/v1/users", json={"email": "ada@example.com", "name": "Ada Again"})
    assert r.status_code == 409
    body = r.json()
    assert body["success"] is False
    assert body["error"]["code"] == "user.duplicate_email"


def test_create_user_invalid_email_returns_422(client: TestClient) -> None:
    r = client.post("/v1/users", json={"email": "not-an-email", "name": "Ada"})
    assert r.status_code == 422
    body = r.json()
    assert body["success"] is False
    assert body["error"]["code"] == "user.invalid_email"


def test_create_user_missing_name_returns_400(client: TestClient) -> None:
    r = client.post("/v1/users", json={"email": "ada@example.com"})
    # FastAPI returns 422 for missing required fields before even hitting the handler
    assert r.status_code in (400, 422)


# ── get ───────────────────────────────────────────────────────────────────────


def test_get_user_returns_200(client: TestClient) -> None:
    data = create_user(client)
    user_id = data["id"]

    r = client.get(f"/v1/users/{user_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["data"]["id"] == user_id
    assert body["data"]["email"] == "ada@example.com"
    assert body["data"]["is_active"] is True


def test_get_user_not_found_returns_404(client: TestClient) -> None:
    r = client.get("/v1/users/00000000-0000-0000-0000-000000000001")
    assert r.status_code == 404
    assert r.json()["success"] is False


# ── list ──────────────────────────────────────────────────────────────────────


def test_list_users_empty(client: TestClient) -> None:
    r = client.get("/v1/users")
    assert r.status_code == 200
    body = r.json()
    assert body["data"] == []
    assert body["metadata"]["total"] == 0


def test_list_users_returns_all(client: TestClient) -> None:
    create_user(client, "a@example.com", "A")
    create_user(client, "b@example.com", "B")
    create_user(client, "c@example.com", "C")

    r = client.get("/v1/users?page=1&page_size=10")
    assert r.status_code == 200
    body = r.json()
    assert len(body["data"]) == 3
    assert body["metadata"]["total"] == 3


def test_list_users_pagination(client: TestClient) -> None:
    for i in range(5):
        create_user(client, f"user{i}@example.com", f"User{i}")

    r1 = client.get("/v1/users?page=1&page_size=3")
    assert r1.status_code == 200
    assert len(r1.json()["data"]) == 3
    assert r1.json()["metadata"]["total"] == 5

    r2 = client.get("/v1/users?page=2&page_size=3")
    assert r2.status_code == 200
    assert len(r2.json()["data"]) == 2

    # No overlap between pages
    ids_p1 = {u["id"] for u in r1.json()["data"]}
    ids_p2 = {u["id"] for u in r2.json()["data"]}
    assert ids_p1.isdisjoint(ids_p2)


# ── change email ──────────────────────────────────────────────────────────────


def test_change_email_returns_204(client: TestClient) -> None:
    data = create_user(client)
    user_id = data["id"]

    r = client.patch(f"/v1/users/{user_id}/email", json={"new_email": "new@example.com"})
    assert r.status_code == 204


def test_change_email_visible_on_get(client: TestClient) -> None:
    data = create_user(client)
    user_id = data["id"]

    client.patch(f"/v1/users/{user_id}/email", json={"new_email": "updated@example.com"})

    r = client.get(f"/v1/users/{user_id}")
    assert r.status_code == 200
    assert r.json()["data"]["email"] == "updated@example.com"


def test_change_email_same_value_is_noop(client: TestClient) -> None:
    data = create_user(client, "ada@example.com")
    user_id = data["id"]

    r = client.patch(f"/v1/users/{user_id}/email", json={"new_email": "ada@example.com"})
    assert r.status_code == 204


# ── outbox ────────────────────────────────────────────────────────────────────


def test_create_user_writes_outbox_event(client: TestClient, outbox: OutboxAssert) -> None:
    create_user(client, "ada@example.com", "Ada")

    async def _check() -> None:
        await outbox.assert_event_published(
            "identity.user.registered",
            where={"email": "ada@example.com"},
        )

    asyncio.run(_check())


def test_change_email_writes_outbox_event(client: TestClient, outbox: OutboxAssert) -> None:
    data = create_user(client, "before@example.com")
    user_id = data["id"]
    client.patch(f"/v1/users/{user_id}/email", json={"new_email": "after@example.com"})

    async def _check() -> None:
        await outbox.assert_event_published(
            "identity.user.email_changed",
            where={"new_email": "after@example.com"},
        )

    asyncio.run(_check())


# ── profile ───────────────────────────────────────────────────────────────────


def test_get_user_profile_returns_core_fields(client: TestClient) -> None:
    """Default (flag off): profile returns id/email/name but omits is_active."""
    data = create_user(client)
    user_id = data["id"]

    r = client.get(f"/v1/users/{user_id}/profile")
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["data"]["id"] == user_id
    assert body["data"]["email"] == "ada@example.com"
    assert body["data"]["name"] == "Ada"
    assert "is_active" not in body["data"]


def test_get_user_profile_not_found_returns_404(client: TestClient) -> None:
    r = client.get("/v1/users/00000000-0000-0000-0000-000000000002/profile")
    assert r.status_code == 404
    assert r.json()["success"] is False


def test_get_user_profile_with_enhanced_flag_includes_is_active(
    db_url: str,
    engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag on: profile response includes is_active."""
    import identity_service.main as main_mod  # noqa: PLC0415
    from prometheus_client import CollectorRegistry  # noqa: PLC0415
    from qx.flags import InMemoryFlag  # noqa: PLC0415
    from qx.flags import InMemoryProvider as _OrigProvider  # noqa: PLC0415
    from qx.observability import setup_observability as _orig_obs  # noqa: PLC0415

    fresh_registry = CollectorRegistry()

    def _patched_obs(*args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["metrics_registry"] = fresh_registry
        return _orig_obs(*args, **kwargs)

    def _enhanced_provider(_flags=None):  # type: ignore[no-untyped-def]
        return _OrigProvider(
            {
                "identity.enhanced-profile": InMemoryFlag("on", {"on": True, "off": False}),
            }
        )

    monkeypatch.setattr(main_mod, "setup_observability", _patched_obs)
    monkeypatch.setattr(main_mod, "InMemoryProvider", _enhanced_provider)

    from fastapi.testclient import TestClient  # noqa: PLC0415

    with TestClient(main_mod.build_app()) as enhanced_client:
        data = create_user(enhanced_client)
        user_id = data["id"]

        r = enhanced_client.get(f"/v1/users/{user_id}/profile")
        assert r.status_code == 200
        body = r.json()
        assert body["success"] is True
        assert body["data"]["is_active"] is True
