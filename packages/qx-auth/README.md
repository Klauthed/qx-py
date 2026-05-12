# qx-auth

JWT validation, OIDC discovery, RBAC primitives, policy engine, and token-bucket rate limiting for the Qx framework.

## What lives here

- **`qx.auth.JwtValidator`** — validates and decodes JWT access tokens. Supports RS256/ES256 via JWKS endpoint, audience and issuer validation, and a pluggable `RevocationCheck`.
- **`qx.auth.JwtSettings`** — Pydantic settings: JWKS URI, issuer, audience, algorithm, leeway.
- **`qx.auth.Principal`** — decoded token claims: `subject`, `email`, `roles`, `permissions`, `tenant_id`, raw claims dict.
- **`qx.auth.OidcDiscovery`** — fetches and caches the OpenID Connect discovery document (`/.well-known/openid-configuration`). Populates `JwtSettings` from the discovery endpoint automatically.
- **`qx.auth.OidcConfiguration`** — parsed OIDC discovery document.
- **`qx.auth.Role` / `Permission`** — value objects for RBAC. `Role` contains a set of `Permission` strings with wildcard matching (`orders.*` matches `orders.read`).
- **`qx.auth.PolicyEvaluator`** — evaluates a list of `Policy` objects against a `Principal`. Policies are composable with `require_permission`, `require_any_permission`, and `require_all_permissions`.
- **`qx.auth.TokenBucket`** — in-memory token-bucket rate limiter. Returns a `TokenBucketResult` with `allowed`, `remaining`, and `retry_after` — no exceptions.

## Usage

### JWT validation in a FastAPI route

```python
from qx.auth import JwtValidator, Principal
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer

security = HTTPBearer()
validator = JwtValidator(settings.jwt)

async def current_principal(token=Depends(security)) -> Principal:
    result = await validator.validate(token.credentials)
    if not result.is_success:
        raise HTTPException(status_code=401)
    return result.value
```

### Policy evaluation

```python
from qx.auth import PolicyEvaluator, require_permission

evaluator = PolicyEvaluator([require_permission("users.write")])
decision = evaluator.evaluate(principal)
if not decision.allowed:
    return Result.failure(ForbiddenError(...))
```

### Token-bucket rate limiting

```python
from qx.auth import TokenBucket

bucket = TokenBucket(capacity=100, refill_rate=10)  # 10 tokens/sec

result = bucket.consume(principal.subject)
if not result.allowed:
    return Result.failure(RateLimitedError(retry_after=result.retry_after))
```

## Design rules

- `JwtValidator.validate()` returns `Result[Principal]` — it never raises. Callers decide how to translate validation failures to HTTP responses.
- JWKS are cached and refreshed lazily on key-ID miss so a key rotation doesn't require a restart.
- Permission wildcards follow a simple dot-separated scheme: `"orders.*"` grants all permissions starting with `"orders."`. Policies compose with AND (`require_all_permissions`) or OR (`require_any_permission`) semantics.
