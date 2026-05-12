#!/usr/bin/env bash
# Publish all qx packages to PyPI (or TestPyPI).
#
# Usage:
#   ./scripts/publish.sh                   # publish to PyPI
#   ./scripts/publish.sh --test            # publish to TestPyPI
#   ./scripts/publish.sh --skip-checks     # skip pre-publish checks
#   ./scripts/publish.sh --dry-run         # build only, do not upload
#
# Prerequisites:
#   - PYPI_TOKEN env var set (for PyPI)
#   - TEST_PYPI_TOKEN env var set (for TestPyPI)
#   - Or configure ~/.pypirc (see PUBLISH.md)
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

TARGET="pypi"
SKIP_CHECKS=false
DRY_RUN=false

for arg in "$@"; do
  case "$arg" in
    --test)          TARGET="testpypi" ;;
    --skip-checks)   SKIP_CHECKS=true ;;
    --dry-run)       DRY_RUN=true ;;
    *)
      echo "Unknown argument: $arg" >&2
      echo "Usage: $0 [--test] [--skip-checks] [--dry-run]" >&2
      exit 1
      ;;
  esac
done

# ---- Checks ----
if [[ "$SKIP_CHECKS" == false ]]; then
  echo "Running pre-publish checks ..."
  bash "$ROOT/scripts/check.sh"
  echo ""
fi

# ---- Build ----
echo "Building packages ..."
bash "$ROOT/scripts/build.sh" --clean
echo ""

if [[ "$DRY_RUN" == true ]]; then
  echo "Dry run complete. Artifacts in $ROOT/dist — not uploading."
  exit 0
fi

# ---- Token resolution ----
if [[ "$TARGET" == "testpypi" ]]; then
  REPO_URL="https://test.pypi.org/legacy/"
  TOKEN="${TEST_PYPI_TOKEN:-}"
  REPO_LABEL="TestPyPI"
else
  REPO_URL="https://upload.pypi.org/legacy/"
  TOKEN="${PYPI_TOKEN:-}"
  REPO_LABEL="PyPI"
fi

if [[ -z "$TOKEN" ]]; then
  echo "No token found in environment."
  if [[ "$TARGET" == "testpypi" ]]; then
    echo "Set TEST_PYPI_TOKEN or configure [testpypi] in ~/.pypirc"
  else
    echo "Set PYPI_TOKEN or configure [pypi] in ~/.pypirc"
  fi
  echo ""
  echo "Falling back to interactive credential prompt ..."
  TOKEN_ARGS=()
else
  TOKEN_ARGS=(--token "$TOKEN")
fi

# ---- Upload ----
echo "Uploading to $REPO_LABEL ..."
echo ""

DIST="$ROOT/dist"

# uv publish supports uploading a directory of dist artifacts
if [[ -n "${TOKEN:-}" ]]; then
  uv publish \
    --publish-url "$REPO_URL" \
    --token "$TOKEN" \
    "$DIST"/*.whl "$DIST"/*.tar.gz
else
  uv publish \
    --publish-url "$REPO_URL" \
    "$DIST"/*.whl "$DIST"/*.tar.gz
fi

echo ""
echo "Published to $REPO_LABEL successfully."

if [[ "$TARGET" == "testpypi" ]]; then
  echo ""
  echo "Verify on TestPyPI:"
  echo "  https://test.pypi.org/search/?q=qx-"
  echo ""
  echo "Install a test package:"
  echo "  uv pip install --index-url https://test.pypi.org/simple/ qx-core"
else
  echo ""
  echo "Packages on PyPI:"
  echo "  https://pypi.org/search/?q=qx-"
  echo ""
  echo "Install:"
  echo "  uv add qx-core qx-cqrs qx-http qx-db"
fi
