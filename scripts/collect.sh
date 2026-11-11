#!/usr/bin/env bash
set -euo pipefail
host=${1:?usage: collect.sh user@host new-local-directory}
dest=${2:?new local directory required}
mkdir -- "$dest"
scp -r -o StrictHostKeyChecking=yes "$host:visual-link/runs" "$dest/"
