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
from qx.http.middleware import MetricsMiddleware, RegionRedirectMiddleware, RequestContextMiddleware
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
    "ApiError",
    "ApiMetadata",
    "ApiResponse",
    "Inject",
    "MetricsMiddleware",
    "RegionRedirectMiddleware",
    "RequestContextMiddleware",
    "__version__",
    "attach_container",
    "envelope_failure",
    "envelope_success",
    "get_container",
    "install_exception_handlers",
    "make_probes_router",
    "scope_dep",
    "setup_qx_app",
    "unwrap",
]
