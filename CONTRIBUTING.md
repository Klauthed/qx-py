# Contributing to qx-py

Thank you for considering a contribution. This document covers everything you need to get started.

## Prerequisites

- Python 3.14+
- [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Docker (for integration tests and the local stack)

## Set up the workspace

```bash
git clone https://github.com/klauthed/qx-py.git
cd qx-py
uv sync
```

All 15 packages and the identity-service example are installed in editable mode into one shared virtualenv managed by uv.

## Running checks

```bash
# Everything: lint, types, tests
./scripts/check.sh

# Just tests
uv run pytest packages/ examples/ -q

# Single test file
uv run pytest examples/identity-service/tests/integration/test_users_api.py -v

# Lint only
uv run ruff check .

# Type check only
uv run mypy .
```

Integration tests require Docker (testcontainers spins up a Postgres container automatically).

## Coding conventions

- **`Result[T]` over exceptions** — return `Result.success(value)` / `Result.failure(error)`. No bare `raise` in business logic.
- **No `from __future__ import annotations`** in files with FastAPI routes or Pydantic models — it breaks runtime type resolution.
- **Runtime imports for Pydantic fields** — if a type is used in a Pydantic model field or FastAPI route annotation, it must be importable at runtime (add `# noqa: TC002` / `TC003` rather than moving it under `TYPE_CHECKING`).
- **No comments explaining what code does** — good names do that. Comments only for non-obvious constraints or workarounds.
- **UUID v7 for DB primary keys** (`Identifier.new()`), **UUID v4 for tokens / non-sequential IDs** (`Identifier.new_v4()`).

## Submitting a PR

1. Fork the repo and create a feature branch off `master`.
2. Make your changes and run `./scripts/check.sh` — it must be green.
3. Write or update tests. New behaviour without a test will not be merged.
4. Open a PR against `master` using the template provided.

For large changes (new packages, breaking changes, significant API additions), open a discussion or issue first so we can align before you invest the effort.

## Commit messages

Use the [Conventional Commits](https://www.conventionalcommits.org/) format:

```
feat(qx-db): add cursor pagination to Repository.list()
fix(qx-di): propagate scope through transient factory resolution
docs: add architecture diagram to docs/architecture.md
```

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`.

## Reporting issues

Use the GitHub [issue templates](.github/ISSUE_TEMPLATE/). For security vulnerabilities, see [SECURITY.md](.github/SECURITY.md).

## License

By contributing you agree that your contributions will be licensed under the [MIT License](LICENSE).
