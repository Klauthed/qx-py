# Tester Setup Guide

This guide is for people testing qx packages before they land on PyPI.
Packages are available on **GitHub Packages** — a PyPI-compatible registry
that works with `uv` and `pip`. You need a GitHub account and a Personal
Access Token (PAT).

---

## 1. Create a GitHub PAT

Go to **GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)**
and create a token with the `read:packages` scope.

Keep the token — you'll need it below.

---

## 2. Install with uv (recommended)

### Add the registry to your project

Add this block to your service's `pyproject.toml`:

```toml
[[tool.uv.index]]
name = "klauthed"
url = "https://pip.pkg.github.com/klauthed/"
```

### Authenticate

Set these environment variables (add them to your shell profile or `.env`):

```bash
export UV_INDEX_KLAUTHED_USERNAME=__token__
export UV_INDEX_KLAUTHED_PASSWORD=ghp_YOUR_TOKEN_HERE
```

### Install qx packages

```bash
uv add qx-core qx-di qx-cqrs qx-db qx-http qx-observability \
       qx-events qx-worker qx-cache qx-auth qx-cli
uv sync
```

---

## 3. Install with pip

```bash
pip install qx-core qx-di qx-cqrs qx-db qx-http \
  --extra-index-url "https://__token__:ghp_YOUR_TOKEN_HERE@pip.pkg.github.com/klauthed/"
```

---

## 4. Install the CLI

```bash
# With uv (recommended — installs qx as a global tool)
uv tool install qx-cli \
  --index "https://pip.pkg.github.com/klauthed/" \
  --index-username __token__ \
  --index-password "ghp_YOUR_TOKEN_HERE"

# Verify
qx version
```

---

## 5. Scaffold a test service

```bash
qx new service my-test-svc
cd my-test-svc
uv sync
qx dev up          # start Postgres · Redis · NATS
uv run alembic upgrade head
uv run uvicorn my_test_svc.main:app --reload
```

Visit `http://localhost:8000/healthz` — should return `{"status": "ok"}`.

---

## 6. What to test

Focus on the full round-trip:

| Area | Command to try |
|---|---|
| Scaffold | `qx new service`, `qx generate aggregate/command/query/event/endpoint` |
| Environment check | `qx doctor --connectivity` |
| Dev stack | `qx dev up`, `qx dev logs`, `qx dev status`, `qx dev down` |
| HTTP layer | POST to a generated endpoint, check envelope shape |
| Domain | Create an aggregate, run a migration, persist via UoW |
| Outbox | Record a domain event, verify it appears in `qx_outbox_events` |
| CLI lint | `ruff check src/` on a freshly scaffolded service |

---

## 7. Reporting issues

Open an issue at https://github.com/klauthed/qx-py/issues with:

- The command you ran
- The full output (including stack traces)
- Your Python version (`python --version`) and OS
- The qx package versions (`pip show qx-core | grep Version`)

Label it `beta-feedback`.
