"""Health and metrics probe endpoints.

Mounted at the root by ``setup_qx_app``, exposing:

- ``GET /healthz`` — liveness; 200 if the process can serve.
- ``GET /readyz`` — readiness; 200 if everything is wired and downstreams ok.
- ``GET /metrics`` — Prometheus exposition format.

Health responses include per-check diagnostics so SREs can read them directly
without scraping logs.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status
from qx.http.deps import Inject
from qx.observability import (
    CONTENT_TYPE_LATEST,
    HealthRegistry,
    HealthStatus,
    Metrics,
    render_metrics,
)

__all__ = ["make_probes_router"]


def make_probes_router() -> APIRouter:
    router = APIRouter(tags=["probes"])

    @router.get("/healthz", include_in_schema=False)
    async def healthz(
        health: HealthRegistry = Inject(HealthRegistry),  # noqa: B008
    ) -> Any:
        result = await health.liveness()
        return {
            "status": result.status.value,
            "checks": [_check_to_dict(c) for c in result.checks],
        }

    @router.get("/readyz", include_in_schema=False)
    async def readyz(
        health: HealthRegistry = Inject(HealthRegistry),  # noqa: B008
    ) -> Response:
        result = await health.readiness()
        body = {
            "status": result.status.value,
            "checks": [_check_to_dict(c) for c in result.checks],
        }
        code = (
            status.HTTP_200_OK
            if result.status is HealthStatus.HEALTHY
            else status.HTTP_503_SERVICE_UNAVAILABLE
            if result.status is HealthStatus.UNHEALTHY
            else status.HTTP_200_OK  # DEGRADED: still serve traffic but mark
        )
        import json as _json  # noqa: PLC0415

        return Response(
            content=_json.dumps(body),
            status_code=code,
            media_type="application/json",
        )

    @router.get("/metrics", include_in_schema=False)
    async def metrics_endpoint(
        metrics: Metrics = Inject(Metrics),  # noqa: B008
    ) -> Response:
        return Response(content=render_metrics(metrics), media_type=CONTENT_TYPE_LATEST)

    return router


def _check_to_dict(c: Any) -> dict[str, Any]:
    return {
        "name": c.name,
        "status": c.status.value,
        "message": c.message,
        "duration_ms": round(c.duration_ms, 2),
        "details": c.details,
    }
