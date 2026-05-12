"""Auth unit tests.

Generates RSA key pair in-memory, mints test JWTs, exercises ``JwtValidator``
against them. No live IdP, no live Redis — pure logic.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from qx.auth import (
    Decision,
    JwtSettings,
    JwtValidator,
    Permission,
    PolicyEvaluator,
    Principal,
    require_all_permissions,
    require_any_permission,
    require_permission,
)
from qx.auth.policy import Policy, PolicyResult


# ---- RSA key pair + JWKS fixture ----


@pytest.fixture(scope="module")
def rsa_key() -> Any:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def private_pem(rsa_key: Any) -> bytes:
    return rsa_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


@pytest.fixture(scope="module")
def public_pem(rsa_key: Any) -> bytes:
    return rsa_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _make_token(private_pem: bytes, **overrides: Any) -> str:
    now = int(time.time())
    claims = {
        "iss": "https://test.qx",
        "aud": "qx",
        "sub": str(uuid4()),
        "iat": now,
        "exp": now + 60,
        "email": "ada@example.com",
        "name": "Ada Lovelace",
        "qx:permissions": ["organization.read", "billing.invoice.read"],
        "qx:roles": ["admin"],
        "scope": "openid profile",
        **overrides,
    }
    return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": "test"})


class _StubJwkClient:
    """Stand-in for PyJWKClient that just returns our static key."""

    def __init__(self, signing_key: Any) -> None:
        self._key = signing_key

    def get_signing_key_from_jwt(self, _token: str) -> Any:
        class _K:
            pass

        k = _K()
        k.key = self._key
        return k


# ---- JWT validation ----


async def test_valid_token_yields_principal(private_pem: bytes, public_pem: bytes) -> None:
    settings = JwtSettings(issuer="https://test.qx", audience="qx")
    validator = JwtValidator(settings)
    validator._jwk_client = _StubJwkClient(public_pem)
    token = _make_token(private_pem)
    result = await validator.validate(token)
    assert result.is_success
    p = result.value
    assert p.email == "ada@example.com"
    assert "admin" in p.roles
    assert Permission(name="organization.read").matches(p.permissions)


async def test_expired_token_rejected(private_pem: bytes, public_pem: bytes) -> None:
    settings = JwtSettings(issuer="https://test.qx", audience="qx", leeway_seconds=0)
    validator = JwtValidator(settings)
    validator._jwk_client = _StubJwkClient(public_pem)
    token = _make_token(private_pem, exp=int(time.time()) - 60, iat=int(time.time()) - 120)
    result = await validator.validate(token)
    assert result.is_failure
    assert result.error.code == "auth.token_expired"


async def test_wrong_audience_rejected(private_pem: bytes, public_pem: bytes) -> None:
    settings = JwtSettings(issuer="https://test.qx", audience="qx")
    validator = JwtValidator(settings)
    validator._jwk_client = _StubJwkClient(public_pem)
    token = _make_token(private_pem, aud="someone-else")
    result = await validator.validate(token)
    assert result.is_failure
    assert result.error.code == "auth.invalid_audience"


async def test_wrong_issuer_rejected(private_pem: bytes, public_pem: bytes) -> None:
    settings = JwtSettings(issuer="https://test.qx", audience="qx")
    validator = JwtValidator(settings)
    validator._jwk_client = _StubJwkClient(public_pem)
    token = _make_token(private_pem, iss="https://imposter")
    result = await validator.validate(token)
    assert result.is_failure
    assert result.error.code == "auth.invalid_issuer"


async def test_invalid_signature_rejected(private_pem: bytes, public_pem: bytes) -> None:
    # Generate a *different* keypair and sign with it; validator has the original public key.
    rogue = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    settings = JwtSettings(issuer="https://test.qx", audience="qx")
    validator = JwtValidator(settings)
    validator._jwk_client = _StubJwkClient(public_pem)
    token = _make_token(rogue)
    result = await validator.validate(token)
    assert result.is_failure
    assert result.error.code == "auth.invalid_token"


async def test_revoked_token_rejected(private_pem: bytes, public_pem: bytes) -> None:
    async def revoked(_jti: str) -> bool:
        return True

    settings = JwtSettings(issuer="https://test.qx", audience="qx")
    validator = JwtValidator(settings, revocation_check=revoked)
    validator._jwk_client = _StubJwkClient(public_pem)
    token = _make_token(private_pem, jti=str(uuid4()))
    result = await validator.validate(token)
    assert result.is_failure
    assert result.error.code == "auth.token_revoked"


# ---- RBAC ----


def test_permission_literal_match() -> None:
    assert Permission(name="billing.read").matches(frozenset({"billing.read"})) is True
    assert Permission(name="billing.read").matches(frozenset({"billing.write"})) is False


def test_permission_wildcard_match() -> None:
    granted = frozenset({"billing.*"})
    assert Permission(name="billing.read").matches(granted) is True
    assert Permission(name="billing.invoice.refund").matches(granted) is True
    assert Permission(name="organization.read").matches(granted) is False


def test_permission_global_wildcard() -> None:
    assert Permission(name="anything").matches(frozenset({"*"})) is True


# ---- Policy evaluator ----


def _principal(*permissions: str) -> Principal:
    return Principal(
        subject=uuid4(),
        issuer="test",
        permissions=frozenset(permissions),
    )


async def test_require_permission_allows_when_present() -> None:
    e = PolicyEvaluator(require_permission("billing.read"))
    r = await e.evaluate(_principal("billing.read"))
    assert r.is_success


async def test_require_permission_denies_when_absent() -> None:
    e = PolicyEvaluator(require_permission("billing.read"))
    r = await e.evaluate(_principal("organization.read"))
    assert r.is_failure
    assert r.error.code == "authz.denied"


async def test_require_any_permission() -> None:
    e = PolicyEvaluator(require_any_permission("billing.read", "billing.read.limited"))
    r = await e.evaluate(_principal("billing.read.limited"))
    assert r.is_success


async def test_require_all_permissions() -> None:
    e = PolicyEvaluator(require_all_permissions("billing.read", "billing.export"))
    r = await e.evaluate(_principal("billing.read"))
    assert r.is_failure
    assert "billing.export" in (r.error.message or "")


async def test_custom_policy() -> None:
    async def owner_only(p: Principal, resource: Any) -> PolicyResult:
        if resource.get("owner_id") == str(p.subject):
            return PolicyResult(Decision.ALLOW)
        return PolicyResult(Decision.DENY, reason="not owner")

    e = PolicyEvaluator(Policy(name="owner_only", rule=owner_only))
    user = _principal()
    assert (await e.evaluate(user, {"owner_id": str(user.subject)})).is_success
    assert (await e.evaluate(user, {"owner_id": str(uuid4())})).is_failure
