"""FastAPI app factory.

Single entrypoint for services that want all framework conventions wired up::

    from qx.http import setup_qx_app

    app = setup_qx_app(container, settings, metrics=metrics, health=health)

After this:

- ``RequestContextMiddleware`` is on the stack.
- ``MetricsMiddleware`` is on the stack.
- Exception handlers translate ``Error`` instances to envelopes.
- ``/healthz``, ``/readyz``, ``/metrics`` routes are registered.
- The container is attached to ``app.state`` so ``Inject`` works.
- Lifespan handles container.aclose() on shutdown.

Services then mount their own routers::

    from myservice.presentation import users_router

    app.include_router(users_router, prefix="/v1")
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from qx.http.deps import attach_container
from qx.http.exceptions import install_exception_handlers
from qx.http.middleware import MetricsMiddleware, RequestContextMiddleware
from qx.http.probes import make_probes_router
from qx.observability import HealthRegistry, Metrics

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from qx.core import QxSettings
    from qx.di import Container

__all__ = ["setup_qx_app"]


def setup_qx_app(
    container: Container,
    settings: QxSettings,
    *,
    metrics: Metrics,
    health: HealthRegistry,
    title: str | None = None,
    docs_url: str | None = "/docs",
    openapi_url: str | None = "/openapi.json",
    extra_lifespan: Any | None = None,
) -> FastAPI:
    """Build a FastAPI app preconfigured with framework conventions."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        try:
            if extra_lifespan is not None:
                async with extra_lifespan(app):
                    yield
            else:
                yield
        finally:
            await container.aclose()

    # Ensure Metrics + HealthRegistry can be resolved by Inject(). Idempotent
    # if the caller already registered them.
    if Metrics not in container._providers:
        container.register_instance(Metrics, metrics)
    if HealthRegistry not in container._providers:
        container.register_instance(HealthRegistry, health)

    app = FastAPI(
        title=title or settings.app.name,
        version=settings.app.version,
        docs_url=docs_url,
        openapi_url=openapi_url,
        lifespan=lifespan,
    )
    attach_container(app, container)
    install_exception_handlers(app)

    # Middleware order is "first added, outermost". We want request-context
    # outermost (so it's in scope for everything else) and metrics innermost
    # (so it measures the full request including other middleware).
    app.add_middleware(MetricsMiddleware, metrics=metrics)
    app.add_middleware(RequestContextMiddleware)

    app.include_router(make_probes_router())

    return app
