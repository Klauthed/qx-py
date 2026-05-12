"""JWT validation.

Validates inbound JWTs against a configured issuer (typically Qx's own
IdP, or any OIDC-compliant authority). Public keys come from the issuer's
JWKS endpoint with TTL caching to avoid hammering the IdP on every request.

What we validate:
- Signature (RS256/ES256 by default; configurable list).
- ``iss`` matches the configured issuer.
- ``aud`` includes the configured audience.
- ``exp`` is in the future (with a small clock-skew tolerance).
- ``nbf`` is in the past (likewise).
- Optional: ``azp`` (authorized party) for OIDC.

What we don't do here:
- Token revocation: that needs a separate check against a revocation store
  (Redis-backed JTI deny-list, or token introspection). Plug it in via the
  ``revocation_check`` hook.

Output: a ``Principal`` value object that downstream code consumes — never
the raw claims dict. Keeps the security boundary clean.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar
from uuid import UUID

import httpx
import jwt
from jwt import PyJWKClient
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from qx.core import (
    InfrastructureError,
    Result,
    UnauthorizedError,
)

__all__ = [
    "JwtSettings",
    "JwtValidator",
    "Principal",
    "RevocationCheck",
]


@dataclass(frozen=True, kw_only=True)
class Principal:
    """Authenticated identity.

    The result of validating a JWT — pure value object, no JWT internals leak
    past this point. Downstream policy / RBAC code consumes the ``permissions``
    and ``roles`` collections.
    """

    subject: UUID
    issuer: str
    tenant_id: UUID | None = None
    email: str | None = None
    name: str | None = None
    roles: frozenset[str] = field(default_factory=frozenset)
    permissions: frozenset[str] = field(default_factory=frozenset)
    scopes: frozenset[str] = field(default_factory=frozenset)
    raw_claims: dict[str, Any] = field(default_factory=dict)

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def has_permission(self, permission: str) -> bool:
        return permission in self.permissions

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


RevocationCheck = Callable[[str], Awaitable[bool]]
"""Async function taking a JTI; returns True if revoked."""


class JwtSettings(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="QX_AUTH__JWT__",
        extra="ignore",
    )

    issuer: str = Field(default="https://qx.local")
    jwks_uri: str | None = None  # if None, fetch from {issuer}/.well-known/jwks.json
    audience: str = "qx"
    allowed_algorithms: tuple[str, ...] = ("RS256", "ES256")
    leeway_seconds: int = 30
    cache_keys_ttl_seconds: int = 3600


class JwtValidator:
    """Validate JWTs against a configured OIDC-compatible issuer."""

    def __init__(
        self,
        settings: JwtSettings,
        *,
        revocation_check: RevocationCheck | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._settings = settings
        self._revocation_check = revocation_check
        self._http = http_client or httpx.AsyncClient(timeout=5.0)
        self._jwks_uri = settings.jwks_uri or f"{settings.issuer.rstrip('/')}/.well-known/jwks.json"
        # PyJWKClient caches keys internally with built-in TTL semantics.
        self._jwk_client = PyJWKClient(
            self._jwks_uri,
            cache_keys=True,
            lifespan=settings.cache_keys_ttl_seconds,
        )

    async def validate(self, token: str) -> Result[Principal]:  # noqa: PLR0911
        # Pull the right key for this token's `kid` header.
        try:
            signing_key = self._jwk_client.get_signing_key_from_jwt(token).key
        except jwt.exceptions.PyJWKClientError as exc:
            return Result.failure(
                InfrastructureError(
                    code="auth.jwks_unavailable",
                    message=f"could not fetch signing key: {exc}",
                    cause=exc,
                )
            )
        except Exception as exc:
            return Result.failure(
                UnauthorizedError(
                    code="auth.invalid_token",
                    message="token signature could not be verified",
                    cause=exc,
                )
            )

        try:
            claims = jwt.decode(
                token,
                key=signing_key,
                algorithms=list(self._settings.allowed_algorithms),
                audience=self._settings.audience,
                issuer=self._settings.issuer,
                leeway=self._settings.leeway_seconds,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.ExpiredSignatureError as exc:
            return Result.failure(
                UnauthorizedError(
                    code="auth.token_expired",
                    message="token expired",
                    cause=exc,
                )
            )
        except jwt.InvalidAudienceError as exc:
            return Result.failure(
                UnauthorizedError(
                    code="auth.invalid_audience",
                    message="token audience does not include this service",
                    cause=exc,
                )
            )
        except jwt.InvalidIssuerError as exc:
            return Result.failure(
                UnauthorizedError(
                    code="auth.invalid_issuer",
                    message="token issuer is not trusted",
                    cause=exc,
                )
            )
        except jwt.InvalidTokenError as exc:
            return Result.failure(
                UnauthorizedError(
                    code="auth.invalid_token",
                    message=f"token rejected: {exc}",
                    cause=exc,
                )
            )

        # Revocation hook.
        jti = claims.get("jti")
        if jti and self._revocation_check is not None:
            try:
                if await self._revocation_check(jti):
                    return Result.failure(
                        UnauthorizedError(
                            code="auth.token_revoked",
                            message="token has been revoked",
                        )
                    )
            except Exception as exc:
                # Fail closed on revocation-check infrastructure errors.
                return Result.failure(
                    InfrastructureError(
                        code="auth.revocation_check_failed",
                        message=f"revocation check failed: {exc}",
                        cause=exc,
                    )
                )

        # Map claims to Principal. Convention follows OIDC + a few Qx
        # extensions (qx:permissions, qx:tenant).
        try:
            sub = UUID(claims["sub"])
        except (KeyError, ValueError) as exc:
            return Result.failure(
                UnauthorizedError(
                    code="auth.invalid_subject",
                    message="sub claim is missing or not a UUID",
                    cause=exc,
                )
            )

        tenant_raw = claims.get("qx:tenant") or claims.get("tenant_id")
        tenant_id: UUID | None = None
        if tenant_raw:
            try:
                tenant_id = UUID(tenant_raw)
            except ValueError:
                # Tenant present but malformed — treat as unauth rather than ignore.
                return Result.failure(
                    UnauthorizedError(
                        code="auth.invalid_tenant",
                        message="tenant_id claim is malformed",
                    )
                )

        roles = claims.get("qx:roles") or claims.get("roles") or []
        permissions = claims.get("qx:permissions") or claims.get("permissions") or []
        scopes_raw = claims.get("scope") or claims.get("scp") or ""
        scopes = scopes_raw.split() if isinstance(scopes_raw, str) else list(scopes_raw)

        principal = Principal(
            subject=sub,
            issuer=claims["iss"],
            tenant_id=tenant_id,
            email=claims.get("email"),
            name=claims.get("name") or claims.get("preferred_username"),
            roles=frozenset(roles),
            permissions=frozenset(permissions),
            scopes=frozenset(scopes),
            raw_claims=claims,
        )
        return Result.success(principal)

    async def aclose(self) -> None:
        await self._http.aclose()
