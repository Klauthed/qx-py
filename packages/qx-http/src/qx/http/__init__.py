"""Qx HTTP layer.

Public surface — import from ``qx.http``.
"""

from __future__ import annotations

from qx.http.app import setup_qx_app
from qx.http.deps import (
    Inject,
    attach_container,
    get_container,
    scope_dep,
    unwrap,
)
from qx.http.exceptions import install_exception_handlers
from qx.http.middleware import MetricsMiddleware, RequestContextMiddleware
from qx.http.probes import make_probes_router
from qx.http.responses import (
    ApiError,
    ApiMetadata,
    ApiResponse,
    envelope_failure,
    envelope_success,
)

__version__ = "0.1.0"

__all__ = [
    "setup_qx_app",
    "Inject",
    "attach_container",
    "get_container",
    "scope_dep",
    "unwrap",
    "install_exception_handlers",
    "MetricsMiddleware",
    "RequestContextMiddleware",
    "make_probes_router",
    "ApiResponse",
    "ApiError",
    "ApiMetadata",
    "envelope_success",
    "envelope_failure",
    "__version__",
]
