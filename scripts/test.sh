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

# Guard against pyproject.toml <-> poetry.lock drift. This suite runs in the
# prebuilt cache-venv, so a dependency added to pyproject.toml without a
# regenerated lock passes here but breaks the clean Docker build (installs
# strictly from poetry.lock). Fast + offline. Non-fatal if poetry is unavailable
# (e.g. a stripped headless PATH) so it never blocks a legitimate test run.
if command -v poetry >/dev/null 2>&1; then
  if ! lock_check_out="$(poetry check --lock 2>&1)"; then
    echo "error: pyproject.toml <-> poetry.lock drift detected." >&2
    echo "       run 'poetry lock' and commit poetry.lock (the Docker build fails on this)." >&2
    echo "$lock_check_out" >&2
    exit 1
  fi
else
  echo "warn: poetry not on PATH — skipping poetry.lock drift check." >&2
fi

exec "$VENV_PY" -m pytest "$@"
