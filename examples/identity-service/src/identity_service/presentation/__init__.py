"""Presentation layer — FastAPI routers."""

from __future__ import annotations

from fastapi import FastAPI

from identity_service.presentation.routes.users import router as users_router


def register_routes(app: FastAPI) -> None:
    app.include_router(users_router, prefix="/v1")
