# qx-core

Foundational primitives for the Qx framework.

## What lives here

- **`qx.core.result`** — `Result[T]` algebraic type with success/failure variants and combinators.
- **`qx.core.errors`** — Canonical error hierarchy (`Error`, `ValidationError`, `DomainError`, `NotFoundError`, etc.) used across the framework. HTTP/gRPC layers map these to transport codes.
- **`qx.core.entities`** — Base `Entity`, `AggregateRoot`, and `ValueObject` with auditing, optimistic concurrency, and soft-delete.
- **`qx.core.context`** — `RequestContext` propagated via `contextvars` across async boundaries (request_id, correlation_id, trace_id, user_id, tenant_id).
- **`qx.core.domain`** — `DomainEvent`, `IntegrationEvent`, `Notification` base types.
- **`qx.core.types`** — Common types: `Identifier`, `Page`, `Cursor`, `Sort`, `Filter`.
- **`qx.core.config`** — Layered configuration via `pydantic-settings`.

## Design rules

- This package has **zero infrastructure dependencies**. No SQLAlchemy, no Redis, no FastAPI, no NATS.
- Public API is exported from `qx.core`. Submodules are implementation detail and may move.
- Backward compatibility from `0.x` is best-effort; `1.0` will lock the API surface.
