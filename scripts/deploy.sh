#!/usr/bin/env bash
set -euo pipefail
host=${1:?usage: deploy.sh user@host}
# Verify host fingerprint manually before running this script.
root=$(cd -- "$(dirname -- "$0")/.." && pwd)
archive=$(mktemp)
trap 'rm -f "$archive"' EXIT
tar --exclude=__pycache__ --exclude='*.pyc' -czf "$archive" -C "$root" pyproject.toml src configs scripts docs README.md 使用说明.md
scp -o StrictHostKeyChecking=yes "$archive" "$host:visual-link-source.tar.gz"
ssh -o StrictHostKeyChecking=yes "$host" 'mkdir -p visual-link && tar -xzf visual-link-source.tar.gz -C visual-link'
