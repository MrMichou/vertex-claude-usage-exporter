#!/bin/bash
#
# Bump project version: updates Chart.yaml and creates a git tag.
#
# Usage:
#   ./scripts/bump-version.sh <major|minor|patch>
#   ./scripts/bump-version.sh 1.2.3
#

set -euo pipefail

CHART_FILE="helm/vertex-claude-usage-exporter/Chart.yaml"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$ROOT_DIR"

# Get current version from Chart.yaml
current=$(grep '^version:' "$CHART_FILE" | awk '{print $2}')
IFS='.' read -r major minor patch <<< "$current"

case "${1:-}" in
    major) new_version="$((major + 1)).0.0" ;;
    minor) new_version="${major}.$((minor + 1)).0" ;;
    patch) new_version="${major}.${minor}.$((patch + 1))" ;;
    [0-9]*.[0-9]*.[0-9]*) new_version="$1" ;;
    *)
        echo "Usage: $0 <major|minor|patch|X.Y.Z>"
        echo "Current version: $current"
        exit 1
        ;;
esac

echo "Bumping version: $current -> $new_version"

# Update Chart.yaml version
sed -i "s/^version: .*/version: $new_version/" "$CHART_FILE"

# Update Chart.yaml appVersion
sed -i "s/^appVersion: .*/appVersion: \"$new_version\"/" "$CHART_FILE"

echo "Updated $CHART_FILE"

# Stage, commit, and tag
git add "$CHART_FILE"
git commit -m "chore: bump version to $new_version"
git tag "v$new_version"

echo ""
echo "Version $new_version ready. Push with:"
echo "  git push origin main v$new_version"
