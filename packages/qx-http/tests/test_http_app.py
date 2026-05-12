"""HTTP integration tests using FastAPI TestClient.

Exercises the full middleware + exception-handler + envelope pipeline against
a tiny in-memory app, so we know the wiring works without spinning up
infrastructure.
"""

from __future__ import annotations

from typing import ClassVar

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from qx.core import (
    QxSettings,
    NotFoundError,
    Result,
    ValidationError,
)
from qx.cqrs import Command, Mediator, command_handler
from qx.di import Container
from qx.http import (
    Inject,
    envelope_failure,
    envelope_success,
    setup_qx_app,
    unwrap,
)
from qx.observability import HealthRegistry, Metrics
from prometheus_client import CollectorRegistry


# ---- Test domain ----


class GreetCommand(Command[str]):
    name: str


@command_handler(GreetCommand)
class GreetHandler:
    async def handle(self, cmd: GreetCommand) -> Result[str]:
        if cmd.name == "missing":
            return Result.failure(NotFoundError(code="user.not_found", message="x"))
        if cmd.name == "":
            return Result.failure(
                ValidationError(
                    code="name.empty",
                    message="name required",
                    details={"fields": [{"path": "name", "code": "empty"}]},
                )
            )
        return Result.success(f"hello, {cmd.name}")


# ---- App factory for tests ----


@pytest.fixture
def app() -> FastAPI:
    container = Container()
    mediator = Mediator(container)
    mediator.register_decorated(GreetHandler)
    container.register_instance(Mediator, mediator)

    metrics = Metrics(registry=CollectorRegistry())
    health = HealthRegistry()
    settings = QxSettings()

    fastapi_app = setup_qx_app(
        container,
        settings,
        metrics=metrics,
        health=health,
    )

    @fastapi_app.post("/greet")
    async def greet(cmd: GreetCommand, m: Mediator = Inject(Mediator)):
        result = await m.send(cmd)
        return envelope_success(unwrap(result))

    return fastapi_app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


# ---- Success envelope ----


def test_success_envelope(client: TestClient) -> None:
    r = client.post("/greet", json={"name": "ada"})
    assert r.status_code == 200
    payload = r.json()
    assert payload["success"] is True
    assert payload["data"] == "hello, ada"
    assert payload["error"] is None
    assert payload["metadata"]["correlation_id"]
    assert payload["metadata"]["request_id"]


# ---- Failure envelopes carry status from Error class ----


def test_not_found_returns_404(client: TestClient) -> None:
    r = client.post("/greet", json={"name": "missing"})
    assert r.status_code == 404
    payload = r.json()
    assert payload["success"] is False
    assert payload["error"]["code"] == "user.not_found"


def test_validation_error_returns_400(client: TestClient) -> None:
    r = client.post("/greet", json={"name": ""})
    assert r.status_code == 400
    payload = r.json()
    assert payload["success"] is False
    assert payload["error"]["code"] == "name.empty"


# ---- Correlation id propagation ----


def test_correlation_id_propagates_from_request_header(client: TestClient) -> None:
    cid = "00000000-0000-0000-0000-000000000123"
    r = client.post("/greet", json={"name": "ada"}, headers={"X-Correlation-Id": cid})
    assert r.status_code == 200
    assert r.headers["x-correlation-id"] == cid
    assert r.json()["metadata"]["correlation_id"] == cid


def test_correlation_id_synthesized_when_missing(client: TestClient) -> None:
    r = client.post("/greet", json={"name": "ada"})
    assert "x-correlation-id" in r.headers
    assert r.json()["metadata"]["correlation_id"]


# ---- Probes ----


def test_healthz_returns_envelope(client: TestClient) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"


def test_readyz_returns_ok_with_no_checks(client: TestClient) -> None:
    r = client.get("/readyz")
    assert r.status_code == 200


def test_metrics_endpoint_returns_prometheus_text(client: TestClient) -> None:
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    # Smoke: the framework counters are declared, even if not yet incremented.
    assert b"qx_command_total" in r.content


# ---- Request body validation ----


def test_bad_json_returns_validation_envelope(client: TestClient) -> None:
    r = client.post("/greet", json={})  # missing name
    assert r.status_code == 400
    payload = r.json()
    assert payload["success"] is False
    assert payload["error"]["code"] == "request.validation_failed"
    assert "fields" in payload["error"]["details"]
