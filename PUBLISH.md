# PUBLISH.md — Publishing qx packages to PyPI

This document covers everything needed to release the qx framework packages to PyPI: account setup, token management, the release workflow, versioning policy, and rollback procedures.

---

## Package overview

qx ships as 20 independent PyPI packages under the `qx-` prefix, plus a `qx-py` meta-package that installs them all at once.

**Quick install (full stack):**
```bash
pip install qx-py
```

**Cherry-pick (lightweight services):**
```bash
pip install qx-core qx-di qx-cqrs qx-db qx-http qx-observability
```

| Package | PyPI name | Description |
|---|---|---|
| **qx-py** | `qx-py` | **Meta-package — installs all 20 packages below** |
| qx-core | `qx-core` | Result, Error, Entity, AggregateRoot, RequestContext, Settings |
| qx-di | `qx-di` | Async DI container (SINGLETON / SCOPED / TRANSIENT) |
| qx-cqrs | `qx-cqrs` | Command / Query / Event mediator + pipeline behaviors |
| qx-db | `qx-db` | SQLAlchemy 2 async, Repository, UnitOfWork, outbox |
| qx-cache | `qx-cache` | Redis client, IdempotencyStore, DistributedLock |
| qx-events | `qx-events` | EventRegistry, NATS JetStream publisher/consumer, OutboxRelay |
| qx-http | `qx-http` | FastAPI envelope, middleware, DI bridge, health probes |
| qx-worker | `qx-worker` | NATS consumer runtime with ack / nak / drop |
| qx-observability | `qx-observability` | structlog, OpenTelemetry, Prometheus, health checks |
| qx-auth | `qx-auth` | JWT, OIDC, RBAC, policy evaluator, token-bucket rate limit |
| qx-grpc | `qx-grpc` | gRPC server factory + request-context / metrics interceptors |
| qx-search | `qx-search` | OpenSearch async client + SearchRepository abstract base |
| qx-saga | `qx-saga` | Orchestrated process managers / sagas with compensation |
| qx-eventstore | `qx-eventstore` | Event-sourced aggregates with snapshot support |
| qx-projections | `qx-projections` | Incremental read-model projections from the event stream |
| qx-flags | `qx-flags` | Feature flags via OpenFeature |
| qx-regions | `qx-regions` | Multi-region tenant routing and cross-region event replication |
| qx-testing | `qx-testing` | testcontainers helpers, MediatorStub, RepositoryStub, OutboxAssert |
| qx-cli | `qx-cli` | `qx` CLI: scaffold service, generate artifacts, `dev up/down` |
| qx-devtools | `qx-devtools` | Shared ruff / mypy / pre-commit configs for services |

All packages share the `qx.*` Python namespace and follow the same version number (lockstep releases).

---

## Prerequisites

### 1. PyPI account

Create accounts at:
- **PyPI**: https://pypi.org/account/register/
- **TestPyPI**: https://test.pypi.org/account/register/ (separate account required)

### 2. API tokens

**PyPI token** (for production releases):
1. Go to https://pypi.org/manage/account/token/
2. Create a token scoped to your project (or account-wide for the first upload).
3. Copy and store securely — it is only shown once.

**TestPyPI token** (for test releases):
1. Go to https://test.pypi.org/manage/account/token/
2. Same steps as above.

### 3. Set tokens in your environment

```bash
export PYPI_TOKEN="pypi-AgEI..."
export TEST_PYPI_TOKEN="pypi-AgEI..."
```

Add these to your shell profile or a secrets manager (1Password CLI, Doppler, etc.). Never commit tokens to version control.

### 4. Alternative: ~/.pypirc

```ini
[distutils]
index-servers =
    pypi
    testpypi

[pypi]
repository = https://upload.pypi.org/legacy/
username = __token__
password = pypi-AgEI...

[testpypi]
repository = https://test.pypi.org/legacy/
username = __token__
password = pypi-AgEI...
```

### 5. Install uv (if not already)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

## Release workflow

### Step 1 — Bump the version

All packages release at the same version. Use the version-bump script:

```bash
./scripts/version-bump.sh 0.2.0
```

This updates `version = "..."` in all 15 `pyproject.toml` files and the `__version__` constant in each package's `__init__.py`.

Review the diff:
```bash
git diff packages/*/pyproject.toml
```

Commit:
```bash
git add packages/*/pyproject.toml packages/*/src/qx/**/__init__.py
git commit -m "release: bump all packages to 0.2.0"
git tag v0.2.0
```

### Step 2 — Publish to TestPyPI (dry run)

Always test against TestPyPI first:

```bash
export TEST_PYPI_TOKEN="pypi-AgEI..."
./scripts/publish.sh --test
```

This runs checks, builds all 15 packages, and uploads to https://test.pypi.org.

Verify the test install:
```bash
uv pip install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  qx-core qx-cqrs qx-http
```

> `--extra-index-url` is needed because TestPyPI doesn't mirror third-party dependencies (pydantic, sqlalchemy, etc.) — they come from PyPI.

### Step 3 — Publish to PyPI

```bash
export PYPI_TOKEN="pypi-AgEI..."
./scripts/publish.sh
```

### Step 4 — Push the tag

```bash
git push origin main v0.2.0
```

---

## Individual scripts

### build.sh

Builds all 15 packages in dependency order and outputs `.whl` + `.tar.gz` to `dist/`.

```bash
./scripts/build.sh           # build into dist/
./scripts/build.sh --clean   # wipe dist/ first, then build
```

### check.sh

Runs ruff lint + format check + mypy + pytest before publishing.

```bash
./scripts/check.sh
```

Exit code 0 means all checks passed. This is called automatically by `publish.sh` unless `--skip-checks` is passed.

### version-bump.sh

Bumps the version string in all package manifests and `__init__.py` files.

```bash
./scripts/version-bump.sh 0.2.0
```

### publish.sh

Full release pipeline: checks → build → upload.

```bash
./scripts/publish.sh                  # → PyPI
./scripts/publish.sh --test           # → TestPyPI
./scripts/publish.sh --skip-checks    # skip ruff/mypy/pytest
./scripts/publish.sh --dry-run        # build only, do not upload
```

---

## Versioning policy

qx follows **semantic versioning** with lockstep releases — all 15 packages always share the same version number.

| Change | Version bump | Example |
|---|---|---|
| Bug fix, doc improvement | Patch (`x.y.Z`) | 0.1.0 → 0.1.1 |
| New feature, new behavior that is addable without breaking existing services | Minor (`x.Y.0`) | 0.1.0 → 0.2.0 |
| Breaking change to a public API (renamed class, changed signature, removed export) | Major (`X.0.0`) | 0.1.0 → 1.0.0 |

**What counts as public API:**
- Everything in a package's `__all__` list.
- The CLI command interface (`qx new service`, `qx generate ...`).
- The scaffold template output (changing it would break existing generated services).

**What does NOT count as breaking:**
- Internal `_` prefixed modules and classes.
- Adding new optional parameters with defaults.
- Adding new exports to `__all__`.

### Pre-release versions

For pre-release testing:
```bash
./scripts/version-bump.sh 0.2.0a1   # alpha
./scripts/version-bump.sh 0.2.0b1   # beta
./scripts/version-bump.sh 0.2.0rc1  # release candidate
./scripts/publish.sh --test          # always TestPyPI for pre-releases
```

---

## First-time publish (new package registration)

The first upload to PyPI requires an account-wide token (not a project-scoped token, because the project doesn't exist yet). After the first upload, rotate to a project-scoped token.

```bash
# First ever release — use account-wide token
export PYPI_TOKEN="pypi-account-wide-token..."
./scripts/publish.sh
```

After the packages are registered on PyPI, create project-scoped tokens for each package (or one token scoped to your org if using PyPI Organizations), and update `PYPI_TOKEN`.

---

## Rollback

PyPI does not allow re-uploading a file with the same version. If a bad release goes out:

1. **Yank the release** — yanked versions are hidden from `pip install` unless pinned explicitly:
   - Go to https://pypi.org/manage/project/qx-core/releases/
   - Select the version → "Yank release"
   - Repeat for all 15 packages.

2. **Publish a patch** — bump to the next patch version and release the fix:
   ```bash
   ./scripts/version-bump.sh 0.1.1
   ./scripts/publish.sh
   ```

3. **Delete is permanent** — PyPI allows deletion of a release, but the version number can never be reused. Prefer yanking over deletion.

---

## CI/CD (GitHub Actions example)

```yaml
# .github/workflows/release.yml
name: Release

on:
  push:
    tags: ["v*"]

jobs:
  publish:
    runs-on: ubuntu-latest
    environment: pypi  # requires PYPI_TOKEN secret in GitHub environment
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v4

      - name: Sync workspace
        run: uv sync

      - name: Run checks
        run: ./scripts/check.sh

      - name: Publish
        env:
          PYPI_TOKEN: ${{ secrets.PYPI_TOKEN }}
        run: ./scripts/publish.sh --skip-checks  # checks already ran above
```

For pre-release tags (`v0.2.0a1`, `v0.2.0b1`), route to TestPyPI instead by checking the tag format in the workflow condition.

---

## Troubleshooting

**`File already exists`** — A `.whl` or `.tar.gz` with that version is already on PyPI. You cannot re-upload. Bump the version.

**`Invalid token`** — Ensure `PYPI_TOKEN` starts with `pypi-` and is not expired. Rotate at https://pypi.org/manage/account/token/.

**`Package not found` after publishing** — PyPI index propagation takes 30–60 seconds. Wait and retry.

**`Dependency not found` on TestPyPI** — Use `--extra-index-url https://pypi.org/simple/` when installing from TestPyPI. Third-party deps only live on PyPI.

**Build fails with `package not found in workspace`** — Ensure you run scripts from the repo root (`qx-py/`), not from inside a package directory.
