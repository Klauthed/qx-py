#!/usr/bin/env bash
# Bump the version field in every package's pyproject.toml.
# Usage: ./scripts/version-bump.sh <new-version>
# Example: ./scripts/version-bump.sh 0.2.0
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NEW_VERSION="${1:-}"

if [[ -z "$NEW_VERSION" ]]; then
  echo "Usage: $0 <new-version>" >&2
  echo "Example: $0 0.2.0" >&2
  exit 1
fi

# Validate semver format
if ! [[ "$NEW_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9.]+)?(\+[a-zA-Z0-9.]+)?$ ]]; then
  echo "ERROR: '$NEW_VERSION' is not a valid semver string" >&2
  exit 1
fi

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

echo "Bumping all packages to $NEW_VERSION ..."

for pkg in "${PACKAGES[@]}"; do
  toml="$ROOT/packages/$pkg/pyproject.toml"
  if [[ ! -f "$toml" ]]; then
    echo "WARNING: $toml not found, skipping" >&2
    continue
  fi

  current=$(grep '^version = ' "$toml" | head -1 | sed 's/version = "\(.*\)"/\1/')
  LC_ALL=C sed -i '' "s/^version = \"$current\"/version = \"$NEW_VERSION\"/" "$toml"

  # Also bump the __version__ constant in the package's __init__.py if present
  init="$ROOT/packages/$pkg/src/qx/$(echo $pkg | sed 's/qx-//')/__init__.py"
  if [[ -f "$init" ]]; then
    LC_ALL=C sed -i '' "s/__version__ = \"$current\"/__version__ = \"$NEW_VERSION\"/" "$init"
  fi

  echo "  $pkg: $current → $NEW_VERSION"
done

echo ""
echo "Done. Verify with: grep -r '^version' packages/*/pyproject.toml"
