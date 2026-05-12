#!/usr/bin/env bash
# Pre-publish quality checks: tests, lint, type-check.
# Usage: ./scripts/check.sh
# Exit code 0 = all clear; non-zero = something failed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

FAILED=()

run_check() {
  local name="$1"
  shift
  echo "  → $name"
  if ! "$@" > /tmp/qx-check-output 2>&1; then
    FAILED+=("$name")
    echo "    FAILED:"
    cat /tmp/qx-check-output | sed 's/^/      /'
  fi
}

echo "=== qx pre-publish checks ==="
echo ""

echo "[1/3] Lint (ruff)"
run_check "ruff-check"  uv run ruff check .
run_check "ruff-format" uv run ruff format --check .

echo ""
echo "[2/3] Type check (mypy)"
run_check "mypy" uv run mypy packages/*/src/ examples/*/src/

echo ""
echo "[3/3] Tests"
run_check "pytest" uv run pytest packages/ examples/ -q --tb=short

echo ""
if [[ ${#FAILED[@]} -eq 0 ]]; then
  echo "All checks passed. Ready to publish."
  exit 0
else
  echo "FAILED checks (${#FAILED[@]}):"
  for f in "${FAILED[@]}"; do
    echo "  - $f"
  done
  exit 1
fi
