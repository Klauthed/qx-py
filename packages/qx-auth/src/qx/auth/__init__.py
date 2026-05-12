"""Qx auth: JWT validation, OIDC, RBAC, policies, rate limiting."""

from __future__ import annotations

from qx.auth.jwt import JwtSettings, JwtValidator, Principal, RevocationCheck
from qx.auth.oidc import OidcConfiguration, OidcDiscovery
from qx.auth.policy import (
    Decision,
    Policy,
    PolicyEvaluator,
    PolicyResult,
    require_all_permissions,
    require_any_permission,
    require_permission,
)
from qx.auth.rate_limit import TokenBucket, TokenBucketResult
from qx.auth.rbac import Permission, Role

__version__ = "0.1.0"

__all__ = [
    # JWT
    "JwtSettings",
    "JwtValidator",
    "Principal",
    "RevocationCheck",
    # OIDC
    "OidcConfiguration",
    "OidcDiscovery",
    # RBAC
    "Permission",
    "Role",
    # Policy
    "Decision",
    "Policy",
    "PolicyEvaluator",
    "PolicyResult",
    "require_permission",
    "require_any_permission",
    "require_all_permissions",
    # Rate limit
    "TokenBucket",
    "TokenBucketResult",
    "__version__",
]
