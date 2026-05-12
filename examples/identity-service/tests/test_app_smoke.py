"""Smoke test: the example app boots, registers its handlers, and exposes probes.

Doesn't need real infrastructure — the engine connects lazily, so probes work
without a live DB.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Use a private CollectorRegistry so repeated build_app() calls don't
    # collide on the default global registry. Also use a stub engine so we
    # don't actually try to connect to Postgres.
    from qx.observability import setup_observability as orig_setup_observability

    fresh_registry = CollectorRegistry()

    def patched_setup_observability(*args, **kwargs):
        kwargs["metrics_registry"] = fresh_registry
        return orig_setup_observability(*args, **kwargs)

    import identity_service.main as main_mod
    monkeypatch.setattr(main_mod, "setup_observability", patched_setup_observability)

    return TestClient(main_mod.build_app())


def test_healthz_responds(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200


def test_readyz_responds(client: TestClient) -> None:
    r = client.get("/readyz")
    assert r.status_code == 200


def test_metrics_endpoint(client: TestClient) -> None:
    r = client.get("/metrics")
    assert r.status_code == 200
    assert b"qx_command_total" in r.content


def test_openapi_lists_user_routes(client: TestClient) -> None:
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    assert "/v1/users" in paths
    assert "/v1/users/{user_id}" in paths
    assert "/v1/users/{user_id}/email" in paths


def test_validation_envelope_for_bad_body(client: TestClient) -> None:
    """Pydantic body validation should produce the framework envelope, not raw FastAPI errors."""
    r = client.post("/v1/users", json={"email": ""})  # missing name
    assert r.status_code == 400
    body = r.json()
    assert body["success"] is False
    assert body["error"]["code"] == "request.validation_failed"
