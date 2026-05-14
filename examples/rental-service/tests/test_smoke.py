"""Smoke test — boots the app, checks health/readyz/metrics endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import rental_service.main as _main  # noqa: PLC0415
    from qx.observability import setup_observability as _orig  # noqa: PLC0415

    _registry = CollectorRegistry()

    def _patched(*args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["metrics_registry"] = _registry
        return _orig(*args, **kwargs)

    monkeypatch.setattr(_main, "setup_observability", _patched)
    return TestClient(_main.build_app())


def test_healthz(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200


def test_readyz(client: TestClient) -> None:
    r = client.get("/readyz")
    assert r.status_code == 200


def test_metrics(client: TestClient) -> None:
    r = client.get("/metrics")
    assert r.status_code == 200
    assert b"qx_command_total" in r.content
