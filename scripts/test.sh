#!/usr/bin/env bash
# Canonical test runner for nutricore.
#
# Uses the Poetry CACHE-venv python directly. Both `poetry run pytest` and a
# bare `python -m pytest` are broken in this repo (TD-001: `poetry run`
# recreates an empty in-project venv without the deps → ModuleNotFoundError).
# Do NOT "simplify" this back to `poetry run`.
#
# Allowlisted for headless specialist runs via Bash(./scripts/*).
#
# Usage:
#   ./scripts/test.sh                      # full suite
#   ./scripts/test.sh tests/test_foo.py    # one file
#   ./scripts/test.sh -k some_test -x      # any pytest args pass through
set -euo pipefail
cd "$(dirname "$0")/.."

VENV_PY="$HOME/Library/Caches/pypoetry/virtualenvs/nutricore-SKSdxrGe-py3.12/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
  echo "error: cache-venv python not found at $VENV_PY" >&2
  echo "       run 'poetry install' first, or update the path in scripts/test.sh (TD-001)." >&2
  exit 1
fi

exec "$VENV_PY" -m pytest "$@"
