"""FastAPI exception handlers that produce framework-standard envelopes.

Installed once at app startup via ``install_exception_handlers(app)``. After
that, controllers can:

- Return a ``Result`` and let the dependency adapter unwrap it.
- ``raise qx_error.as_exception()`` for early-exit cases.
- Allow unexpected exceptions to bubble; we catch them, log with full
  context, and respond with a 500 envelope (no stacktrace leakage to the
  client).
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError
from qx.core import (
    Error,
    ErrorException,
    ValidationError,
)
from qx.http.responses import envelope_failure

__all__ = ["install_exception_handlers"]


_log = logging.getLogger("qx.http.errors")


def install_exception_handlers(app: FastAPI) -> None:
    """Wire framework-standard handlers onto a FastAPI app."""

    @app.exception_handler(ErrorException)
    async def handle_qx_error(_req: Request, exc: ErrorException) -> JSONResponse:
        err: Error = exc.error
        # Don't leak cause details to the client; log them.
        if err.cause is not None:
            _log.warning(
                "qx.error code=%s cause=%s",
                err.code,
                err.cause,
                exc_info=err.cause,
            )
        return JSONResponse(
            status_code=err.http_status,
            content=envelope_failure(
                code=err.code,
                message=err.message,
                details=err.details,
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation(_req: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {
                "path": ".".join(str(p) for p in err["loc"] if p != "body"),
                "code": err["type"],
                "message": err["msg"],
            }
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=ValidationError.http_status,
            content=envelope_failure(
                code="request.validation_failed",
                message="One or more fields failed validation.",
                details={"fields": fields},
            ),
        )

    @app.exception_handler(PydanticValidationError)
    async def handle_pydantic_validation(
        _req: Request, exc: PydanticValidationError
    ) -> JSONResponse:
        fields = [
            {
                "path": ".".join(str(p) for p in err["loc"]),
                "code": err["type"],
                "message": err["msg"],
            }
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=ValidationError.http_status,
            content=envelope_failure(
                code="model.validation_failed",
                message="Internal model validation failed.",
                details={"fields": fields},
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unknown(_req: Request, exc: Exception) -> JSONResponse:
        # Last-resort handler. Logs full exception; client gets a generic 500.
        _log.exception("unhandled exception in HTTP handler")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=envelope_failure(
                code="server.internal_error",
                message="An unexpected error occurred.",
            ),
        )
