# qx-http

FastAPI integration layer for the Qx framework — envelope responses, DI bridge, `Result`-to-HTTP error mapping, middleware, and health probes.

## What lives here

- **`qx.http.setup_qx_app`** — one-call FastAPI application factory. Mounts routers, installs middleware, registers exception handlers, and attaches the DI container.
- **`qx.http.Inject`** — FastAPI `Depends`-compatible DI bridge. Resolves any registered type from the container for use in route function signatures.
- **`qx.http.scope_dep`** — `Depends` factory that opens a per-request DI scope, shared across all dependencies in the same request.
- **`qx.http.unwrap`** — extracts the value from a `Result[T]`, raising an `ErrorException` (translated to HTTP by the exception handler) on failure.
- **`qx.http.envelope_success` / `envelope_failure`** — build the standard `{"success": true, "data": ..., "metadata": ...}` response shape.
- **`qx.http.ApiResponse` / `ApiError` / `ApiMetadata`** — Pydantic models for the response envelope.
- **`qx.http.RequestContextMiddleware`** — populates `RequestContext` (request_id, correlation_id, trace_id, tenant_id) from incoming headers for every request.
- **`qx.http.MetricsMiddleware`** — records request duration and status-code counters via Prometheus.
- **`qx.http.make_probes_router`** — `/healthz` (liveness) and `/readyz` (readiness) endpoints backed by `HealthRegistry`.
- **`qx.http.install_exception_handlers`** — maps `ErrorException` subclasses to HTTP status codes (`DomainError` → 422, `NotFoundError` → 404, `ConflictError` → 409, etc.).

## Usage

```python
from fastapi import APIRouter, Depends
from qx.http import Inject, envelope_success, scope_dep, setup_qx_app, unwrap
from qx.cqrs import Mediator
from qx.di import Scope

router = APIRouter(prefix="/users")

@router.post("", status_code=201)
async def create_user(
    cmd: CreateUserCommand,
    mediator: Mediator = Inject(Mediator),  # noqa: B008
    scope: Scope = Depends(scope_dep),      # noqa: B008
) -> dict:
    result = await mediator.send(cmd, scope=scope)
    return envelope_success(unwrap(result))


def build_app() -> FastAPI:
    return setup_qx_app(
        settings=settings,
        container=container,
        routers=[router],
        metrics=metrics,
        health=health,
    )
```

## Response envelope

All successful responses follow:

```json
{
  "success": true,
  "data": { ... },
  "metadata": { "page": 1, "page_size": 20, "total": 57 }
}
```

All error responses follow:

```json
{
  "success": false,
  "error": { "code": "user.not_found", "message": "User 123 not found", "details": {} }
}
```

## Design rules

- `Inject(T)` is syntactic sugar for `Depends(lambda: container.resolve(T, scope=...))`. The scope is shared within a request via FastAPI's dependency deduplication on `scope_dep`.
- Do not use `from __future__ import annotations` in route files — FastAPI reads annotations at runtime for OpenAPI schema generation and body parsing.
- Runtime imports (not under `TYPE_CHECKING`) are required for any type used as a FastAPI body parameter, `Inject` target, or Pydantic field.
