#!/usr/bin/env bash
# Build all qx packages into dist/.
# Usage: ./scripts/build.sh [--clean]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST="$ROOT/dist"

if [[ "${1:-}" == "--clean" ]]; then
  echo "Cleaning $DIST ..."
  rm -rf "$DIST"
fi

mkdir -p "$DIST"

# Topological order (leaves first, packages with dependents last).
PACKAGES=(
  qx-core
  qx-devtools
  qx-di
  qx-cqrs
  qx-observability
  qx-cache
  qx-db
  qx-http
  qx-events
  qx-worker
  qx-auth
  qx-grpc
  qx-search
  qx-testing
  qx-cli
)

echo "Building ${#PACKAGES[@]} packages ..."
echo ""

for pkg in "${PACKAGES[@]}"; do
  dir="$ROOT/packages/$pkg"
  if [[ ! -d "$dir" ]]; then
    echo "ERROR: $dir does not exist" >&2
    exit 1
  fi
  echo "  → $pkg"
  uv build --package "$pkg" --out-dir "$DIST" --quiet
done

echo ""
echo "Done. Artifacts in $DIST:"
ls "$DIST" | sed 's/^/  /'
