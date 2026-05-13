"""JWT authentication middleware and FastAPI dependencies.

Wire ``JwtAuthMiddleware`` **after** ``RequestContextMiddleware`` in the stack
so the request context is already open when the JWT is validated and the
``Principal`` is written into it.

Middleware stack order (outermost → innermost)::

    RequestContextMiddleware   ← opens context
    JwtAuthMiddleware          ← validates token, stores Principal
    MetricsMiddleware
    ─── handler ───

Endpoints requiring auth use the ``require_auth`` FastAPI dependency::

    @router.post("/orders")
    async def create_order(
        body: CreateOrderBody,
        principal: Principal = require_auth(),   # noqa: B008
        mediator: Mediator = Inject(Mediator),   # noqa: B008
    ) -> ...:
        ...

Endpoints that support optional auth use ``get_current_principal``::

    @router.get("/feed")
    async def feed(
        principal: Principal | None = get_current_principal(),  # noqa: B008
    ) -> ...:
        ...
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import Depends
from qx.core import UnauthorizedError, current_context
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

if TYPE_CHECKING:
    from qx.auth.jwt import JwtValidator, Principal
    from starlette.requests import Request
    from starlette.types import ASGIApp

__all__ = [
    "PRINCIPAL_KEY",
    "JwtAuthMiddleware",
    "get_current_principal",
    "require_auth",
]

PRINCIPAL_KEY = "qx.principal"

_AUTH_HEADER = "authorization"


def _extract_bearer(request: Request) -> str | None:
    header = request.headers.get(_AUTH_HEADER, "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return None


class JwtAuthMiddleware(BaseHTTPMiddleware):
    """Extract and validate ``Authorization: Bearer`` tokens per request.

    On a valid token the ``Principal`` is stored in
    ``RequestContext.attributes[principal_key]`` so downstream code
    (``AuthorizationBehavior``, ``require_auth``) can read it without re-parsing
    the JWT.

    Requests with a missing or invalid token are **not** rejected here — the
    middleware is deliberately lenient to support mixed public/protected routes.
    Enforcement happens at the handler via ``require_auth`` or at the mediator
    pipeline via ``AuthorizationBehavior``.
    """

    def __init__(
        self,
        app: ASGIApp,
        validator: JwtValidator,
        *,
        principal_key: str = PRINCIPAL_KEY,
    ) -> None:
        super().__init__(app)
        self._validator = validator
        self._principal_key = principal_key

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        token = _extract_bearer(request)
        if token:
            result = await self._validator.validate(token)
            if result.is_success:
                ctx = current_context()
                ctx.attributes[self._principal_key] = result.value
        return await call_next(request)


def get_current_principal(
    *,
    principal_key: str = PRINCIPAL_KEY,
) -> Any:
    """FastAPI dependency — return the current ``Principal`` or ``None``.

    Returns ``None`` when no valid token was presented (unauthenticated
    requests). Use for endpoints that support optional authentication::

        @router.get("/feed")
        async def feed(
            principal: Principal | None = get_current_principal(),  # noqa: B008
        ) -> ...:
            ...
    """

    def _dep() -> Principal | None:
        return current_context().attributes.get(principal_key)

    return Depends(_dep)


def require_auth(
    *,
    principal_key: str = PRINCIPAL_KEY,
) -> Any:
    """FastAPI dependency — return the current ``Principal`` or raise ``UnauthorizedError``.

    Raises ``UnauthorizedError`` (which the exception handler maps to HTTP 401)
    when no valid token is present::

        @router.post("/orders")
        async def create_order(
            principal: Principal = require_auth(),  # noqa: B008
        ) -> ...:
            ...
    """

    def _dep() -> Principal:
        principal: Principal | None = current_context().attributes.get(principal_key)
        if principal is None:
            raise UnauthorizedError(
                code="auth.not_authenticated",
                message="This endpoint requires authentication.",
            ).as_exception()
        return principal

    return Depends(_dep)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply a token-bucket rate limit keyed on a request attribute.

    ``key_fn`` receives the ``Request`` and returns the bucket key string
    (e.g. the client IP or the authenticated principal's subject). A ``None``
    return skips rate-limiting for that request (useful for health/metrics
    routes).

    Buckets are **scoped**: pass different ``TokenBucket`` instances for
    different limits (per-IP, per-user, per-tenant, etc.)::

        app.add_middleware(
            RateLimitMiddleware,
            bucket=TokenBucket(redis, scope="api", capacity=100, refill_per_second=10),
            key_fn=lambda req: req.client.host if req.client else None,
        )
    """

    def __init__(
        self,
        app: ASGIApp,
        bucket: Any,
        key_fn: Any,
        *,
        skip_prefixes: tuple[str, ...] = ("/healthz", "/readyz", "/metrics"),
    ) -> None:
        super().__init__(app)
        self._bucket = bucket
        self._key_fn = key_fn
        self._skip = skip_prefixes

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if any(request.url.path.startswith(p) for p in self._skip):
            return await call_next(request)

        key: str | None = self._key_fn(request)
        if key is None:
            return await call_next(request)

        result = await self._bucket.check(key)
        if result.is_failure:
            return Response(
                content='{"detail":"rate limit exceeded"}',
                status_code=429,
                headers={
                    "Content-Type": "application/json",
                    "Retry-After": str(int(1 / max(self._bucket._refill, 0.001))),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(int(getattr(result, "value", 0) or 0))
        return response
