# Using qx from GitHub Packages

All qx packages are published to **GitHub Packages** at
`https://pip.pkg.github.com/klauthed/`. This registry mirrors every PyPI
release and is the primary channel for beta testers and CI pipelines that
need packages before (or instead of) the PyPI release.

---

## Authentication

GitHub Packages requires a Personal Access Token (PAT) with **`read:packages`** scope.

Generate one at:
**GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)**

---

## Using with uv (recommended)

### 1. Add the index to your project

```toml
# pyproject.toml
[[tool.uv.index]]
name = "klauthed"
url  = "https://pip.pkg.github.com/klauthed/"
```

### 2. Set credentials

Via environment variables (add to `.env` or your shell profile):

```bash
export UV_INDEX_KLAUTHED_USERNAME=__token__
export UV_INDEX_KLAUTHED_PASSWORD=ghp_YOUR_TOKEN_HERE
```

Or store them permanently with uv's keyring integration:

```bash
uv keyring set https://pip.pkg.github.com/klauthed/ __token__
# Enter your PAT when prompted
```

### 3. Add dependencies normally

```bash
uv add qx-core qx-di qx-cqrs qx-db qx-http \
       qx-observability qx-events qx-worker \
       qx-cache qx-auth qx-cli
```

### 4. Lock and sync

```bash
uv lock
uv sync
```

---

## Installing the CLI globally

```bash
uv tool install qx-cli \
  --index "https://pip.pkg.github.com/klauthed/" \
  --index-username __token__ \
  --index-password "ghp_YOUR_TOKEN_HERE"

qx version  # verify
```

---

## Using with pip

```bash
pip install qx-core qx-di qx-cqrs \
  --extra-index-url "https://__token__:ghp_YOUR_TOKEN_HERE@pip.pkg.github.com/klauthed/"
```

---

## Using in CI (GitHub Actions)

Within the same GitHub organisation, `GITHUB_TOKEN` works directly — no
separate PAT needed.

```yaml
- name: Install qx packages
  env:
    UV_INDEX_KLAUTHED_USERNAME: __token__
    UV_INDEX_KLAUTHED_PASSWORD: ${{ secrets.GITHUB_TOKEN }}
  run: uv sync
```

Add the index to your service's `pyproject.toml` as shown above.

For repositories **outside** the `klauthed` org, create a PAT with
`read:packages` and store it as a repository secret (e.g. `QX_PACKAGES_TOKEN`),
then replace `secrets.GITHUB_TOKEN` with `secrets.QX_PACKAGES_TOKEN`.

---

## Complete example `pyproject.toml`

```toml
[project]
name = "my-service"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = [
    "qx-core>=0.1.0",
    "qx-di>=0.1.0",
    "qx-cqrs>=0.1.0",
    "qx-db>=0.1.0",
    "qx-http>=0.1.0",
    "qx-observability>=0.1.0",
    "qx-events>=0.1.0",
    "qx-worker>=0.1.0",
    "qx-cache>=0.1.0",
    "qx-auth>=0.1.0",
    "uvicorn[standard]>=0.32.0",
]

[dependency-groups]
dev = [
    "qx-testing>=0.1.0",
    "qx-cli>=0.1.0",
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
]

[[tool.uv.index]]
name = "klauthed"
url  = "https://pip.pkg.github.com/klauthed/"
```

---

## Available packages and versions

| Package | Latest | Description |
|---|---|---|
| `qx-core` | 0.1.0 | `Result[T]`, entities, identifiers, pagination |
| `qx-di` | 0.1.0 | Async DI container with scopes |
| `qx-cqrs` | 0.1.0 | Mediator, commands, queries, pipeline behaviors |
| `qx-db` | 0.1.0 | SQLAlchemy 2 async, UnitOfWork, outbox routing |
| `qx-http` | 0.1.0 | FastAPI envelope, DI bridge, middleware |
| `qx-observability` | 0.1.0 | OTel tracing, Prometheus, structlog, health probes |
| `qx-events` | 0.1.0 | NATS JetStream publisher/consumer, OutboxRelay |
| `qx-worker` | 0.1.0 | Consumer runtime, ack/nak/drop, graceful drain |
| `qx-cache` | 0.1.0 | Redis client, idempotency store, distributed lock |
| `qx-auth` | 0.1.0 | JWT/OIDC, RBAC, rate limiter |
| `qx-grpc` | 0.1.0 | gRPC server factory and interceptors |
| `qx-search` | 0.1.0 | OpenSearch async client, SearchRepository |
| `qx-testing` | 0.1.0 | testcontainers helpers, stubs, OutboxAssert |
| `qx-cli` | 0.1.0 | `qx new`, `qx generate`, `qx dev`, `qx doctor` |
| `qx-devtools` | 0.1.0 | Shared ruff/mypy/pre-commit config |

---

## Troubleshooting

**`401 Unauthorized`** — PAT is missing or expired. Regenerate it and update your
`UV_INDEX_KLAUTHED_PASSWORD` env var.

**`404 Not Found`** for a specific package — the package may not be published
yet. Check [GitHub Packages](https://github.com/orgs/klauthed/packages) for
the current list.

**Package not resolving in uv** — make sure the `[[tool.uv.index]]` block is
present in `pyproject.toml` and credentials are exported before running `uv sync`.
