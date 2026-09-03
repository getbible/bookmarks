#!/usr/bin/env bash
# Run every check CI runs. Expects the dev requirements installed in the active
# interpreter (python -m pip install --require-hashes -r requirements-dev.txt).
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python3}"

echo "== ruff format"; "$PYTHON" -m ruff format --check .
echo "== ruff check";  "$PYTHON" -m ruff check .
echo "== mypy";        "$PYTHON" -m mypy
echo "== compile";     "$PYTHON" -m compileall -q getbible_bookmarks scripts tests
echo "== shell";       bash -n deploy/deploy.sh && bash -n deploy/install.sh
echo "== sources";     "$PYTHON" -m getbible_bookmarks validate
echo "== v1 tree";     "$PYTHON" -m getbible_bookmarks build --check
echo "== tests";       "$PYTHON" -m unittest discover -s tests -t . "$@"
echo "All checks passed."
