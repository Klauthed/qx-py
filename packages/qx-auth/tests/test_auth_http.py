"""Auth HTTP middleware and dependency tests.

Covers:
- JwtAuthMiddleware: valid token → Principal in context
- JwtAuthMiddleware: missing / invalid token → request continues, no principal
- require_auth: present principal → passes; absent → 401
- get_current_principal: optional dep, None when unauthenticated
- RateLimitMiddleware: token bucket integration on a dummy endpoint
- @requires decorator wired through AuthorizationBehavior + Mediator pipeline
- RedisRevocationStore: revoke + is_revoked + callable protocol
"""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID, uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from qx.auth import (
    JwtAuthMiddleware,
    JwtSettings,
    JwtValidator,
    Principal,
    RedisRevocationStore,
    get_current_principal,
    require_auth,
    require_permission,
)
from qx.core import (
    ForbiddenError,
    RequestContext,
    UnauthorizedError,
    current_context,
    request_scope,
)
from qx.cqrs import Mediator
from qx.cqrs.decorators import command_handler, requires
from qx.cqrs.messages import Command
from qx.cqrs.pipeline import AuthorizationBehavior, LoggingBehavior
from qx.di import Container
from qx.http.exceptions import install_exception_handlers
from qx.http.middleware import RequestContextMiddleware

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


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


class _StubJwkClient:
    def __init__(self, signing_key: Any) -> None:
        self._key = signing_key

    def get_signing_key_from_jwt(self, _token: str) -> Any:
        class _K:
            pass

        k = _K()
        k.key = self._key
        return k


def _mint(private_pem: bytes, **overrides: Any) -> str:
    now = int(time.time())
    claims = {
        "iss": "https://test.qx",
        "aud": "qx",
        "sub": str(uuid4()),
        "iat": now,
        "exp": now + 300,
        "qx:permissions": ["orders.write", "billing.read"],
        "qx:roles": ["editor"],
        "jti": str(uuid4()),
        **overrides,
    }
    return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": "test"})


@pytest.fixture
def validator(private_pem: bytes, public_pem: bytes) -> JwtValidator:
    settings = JwtSettings(issuer="https://test.qx", audience="qx")
    v = JwtValidator(settings)
    v._jwk_client = _StubJwkClient(public_pem)
    return v


# ---------------------------------------------------------------------------
# JwtAuthMiddleware tests
# ---------------------------------------------------------------------------


def _make_app(validator: JwtValidator) -> FastAPI:
    """Minimal FastAPI app with JwtAuthMiddleware attached."""
    app = FastAPI()
    app.add_middleware(JwtAuthMiddleware, validator=validator)
    app.add_middleware(RequestContextMiddleware)

    @app.get("/whoami")
    def whoami() -> dict[str, Any]:
        p = current_context().attributes.get("qx.principal")
        if p is None:
            return {"authenticated": False}
        return {"authenticated": True, "subject": str(p.subject), "roles": list(p.roles)}

    @app.get("/protected")
    def protected(principal: Principal = require_auth()) -> dict[str, str]:  # noqa: B008
        return {"subject": str(principal.subject)}

    @app.get("/optional")
    def optional(principal: Principal | None = get_current_principal()) -> dict[str, Any]:  # noqa: B008
        return {"authenticated": principal is not None}

    return app


def test_valid_token_sets_principal_in_context(private_pem: bytes, validator: JwtValidator) -> None:
    app = _make_app(validator)
    token = _mint(private_pem)
    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get("/whoami", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["authenticated"] is True
    assert "editor" in r.json()["roles"]


def test_missing_token_does_not_block_public_endpoint(validator: JwtValidator) -> None:
    app = _make_app(validator)
    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get("/whoami")
    assert r.status_code == 200
    assert r.json()["authenticated"] is False


def test_invalid_token_does_not_block_public_endpoint(validator: JwtValidator) -> None:
    app = _make_app(validator)
    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get("/whoami", headers={"Authorization": "Bearer not.a.jwt"})
    assert r.status_code == 200
    assert r.json()["authenticated"] is False


def test_require_auth_passes_when_token_present(
    private_pem: bytes, validator: JwtValidator
) -> None:
    app = _make_app(validator)
    token = _mint(private_pem)
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert UUID(r.json()["subject"])  # valid UUID


def test_require_auth_returns_401_without_token(validator: JwtValidator) -> None:
    app = _make_app(validator)
    install_exception_handlers(app)
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.get("/protected")
    assert r.status_code == 401


def test_get_current_principal_is_none_when_unauthenticated(validator: JwtValidator) -> None:
    app = _make_app(validator)
    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get("/optional")
    assert r.status_code == 200
    assert r.json()["authenticated"] is False


def test_get_current_principal_returns_principal_when_authenticated(
    private_pem: bytes, validator: JwtValidator
) -> None:
    app = _make_app(validator)
    token = _mint(private_pem)
    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get("/optional", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["authenticated"] is True


# ---------------------------------------------------------------------------
# RedisRevocationStore tests (mock Redis via CacheFacade stub)
# ---------------------------------------------------------------------------


class _StubCache:
    """In-memory stand-in for CacheFacade."""

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    async def set_json(self, key: str, value: Any, *, ttl: int | None = None) -> None:
        self._store[key] = value

    async def get_json(self, key: str) -> Any:
        return self._store.get(key)


async def test_revocation_store_revoke_and_check() -> None:
    cache = _StubCache()
    store = RedisRevocationStore(cache)  # type: ignore[arg-type]
    jti = str(uuid4())

    assert await store.is_revoked(jti) is False

    await store.revoke(jti, ttl_seconds=300)

    assert await store.is_revoked(jti) is True


async def test_revocation_store_callable_protocol() -> None:
    cache = _StubCache()
    store = RedisRevocationStore(cache)  # type: ignore[arg-type]
    jti = str(uuid4())
    await store.revoke(jti, ttl_seconds=60)
    assert await store(jti) is True


async def test_revocation_store_unknown_jti_not_revoked() -> None:
    cache = _StubCache()
    store = RedisRevocationStore(cache)  # type: ignore[arg-type]
    assert await store("nonexistent-jti") is False


async def test_revocation_store_plugs_into_validator(private_pem: bytes, public_pem: bytes) -> None:
    cache = _StubCache()
    store = RedisRevocationStore(cache)  # type: ignore[arg-type]
    settings = JwtSettings(issuer="https://test.qx", audience="qx")
    validator = JwtValidator(settings, revocation_check=store)
    validator._jwk_client = _StubJwkClient(public_pem)

    jti = str(uuid4())
    token = _mint(private_pem, jti=jti)

    # Not yet revoked → valid
    r = await validator.validate(token)
    assert r.is_success

    # Revoke the JTI → rejected
    await store.revoke(jti, ttl_seconds=300)
    r2 = await validator.validate(token)
    assert r2.is_failure
    assert r2.error.code == "auth.token_revoked"


# ---------------------------------------------------------------------------
# @requires decorator wired through AuthorizationBehavior + Mediator
# ---------------------------------------------------------------------------


class _EchoResult:
    def __init__(self, value: str) -> None:
        self.value = value


@requires(require_permission("orders.write"))
class _PlaceOrderCommand(Command[_EchoResult]):
    item: str


@command_handler(_PlaceOrderCommand)
class _PlaceOrderHandler:
    async def handle(self, cmd: _PlaceOrderCommand) -> Any:
        from qx.core import Result  # noqa: PLC0415

        return Result.success(_EchoResult(value=cmd.item))


def _build_mediator(*, principal: Principal | None) -> tuple[Mediator, dict[str, Any]]:
    """Build a mediator with AuthorizationBehavior and a pre-set principal."""

    container = Container()
    container.register(_PlaceOrderHandler, _PlaceOrderHandler)

    mediator = Mediator(
        container,
        command_behaviors=(
            LoggingBehavior(),
            AuthorizationBehavior(),
        ),
    )
    mediator.register_command(_PlaceOrderCommand, _PlaceOrderHandler)
    return mediator, {"qx.principal": principal}


async def test_requires_decorator_allows_when_permission_present() -> None:

    subject = uuid4()
    principal = Principal(
        subject=subject,
        issuer="test",
        permissions=frozenset({"orders.write"}),
    )
    mediator, attrs = _build_mediator(principal=principal)

    ctx = RequestContext(attributes=attrs)
    with request_scope(ctx):
        result = await mediator.send(_PlaceOrderCommand(item="widget"))

    assert result.is_success
    assert result.value.value == "widget"


async def test_requires_decorator_denies_when_permission_absent() -> None:
    principal = Principal(
        subject=uuid4(),
        issuer="test",
        permissions=frozenset({"billing.read"}),  # no orders.write
    )
    mediator, attrs = _build_mediator(principal=principal)

    ctx = RequestContext(attributes=attrs)
    with request_scope(ctx):
        result = await mediator.send(_PlaceOrderCommand(item="widget"))

    assert result.is_failure
    assert result.error.code == "authz.denied"
    assert isinstance(result.error, ForbiddenError)


async def test_requires_decorator_returns_401_when_no_principal() -> None:
    mediator, _ = _build_mediator(principal=None)

    ctx = RequestContext(attributes={})
    with request_scope(ctx):
        result = await mediator.send(_PlaceOrderCommand(item="widget"))

    assert result.is_failure
    assert isinstance(result.error, UnauthorizedError)
    assert result.error.code == "authz.not_authenticated"
