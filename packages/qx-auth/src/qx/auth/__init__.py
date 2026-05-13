"""Qx auth: JWT validation, OIDC, RBAC, policies, rate limiting."""

from __future__ import annotations

from qx.auth.http import (
    PRINCIPAL_KEY,
    JwtAuthMiddleware,
    RateLimitMiddleware,
    get_current_principal,
    require_auth,
)
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
from qx.auth.revocation import RedisRevocationStore

__version__ = "0.2.0"

__all__ = [
    "PRINCIPAL_KEY",
    # Policy
    "Decision",
    # HTTP middleware / FastAPI deps
    "JwtAuthMiddleware",
    # JWT
    "JwtSettings",
    "JwtValidator",
    # OIDC
    "OidcConfiguration",
    "OidcDiscovery",
    # RBAC
    "Permission",
    "Policy",
    "PolicyEvaluator",
    "PolicyResult",
    "Principal",
    "RateLimitMiddleware",
    "RedisRevocationStore",
    "RevocationCheck",
    "Role",
    # Rate limit
    "TokenBucket",
    "TokenBucketResult",
    "__version__",
    "get_current_principal",
    "require_all_permissions",
    "require_any_permission",
    "require_auth",
    "require_permission",
]
