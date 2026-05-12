"""Presentation layer — FastAPI routers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from identity_service.presentation.routes.users import router as users_router

if TYPE_CHECKING:
    from fastapi import FastAPI


def register_routes(app: FastAPI) -> None:
    app.include_router(users_router, prefix="/v1")
