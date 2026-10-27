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

# Poetry names the cache-venv after this checkout's real path:
# nutricore-<first 8 chars of urlsafe-base64(sha256(path))>-py3.12. Compute it
# here so the script works on any machine; the laptop's original venv name stays
# as a fallback.
VENVS="$HOME/Library/Caches/pypoetry/virtualenvs"
venv_hash="$(printf '%s' "$(pwd -P)" | openssl dgst -sha256 -binary | base64 | tr '+/' '-_' | cut -c1-8)" || venv_hash=""
VENV_PY=""
for name in "nutricore-$venv_hash-py3.12" "nutricore-SKSdxrGe-py3.12"; do
  if [[ -x "$VENVS/$name/bin/python" ]]; then
    VENV_PY="$VENVS/$name/bin/python"
    break
  fi
done
if [[ -z "$VENV_PY" ]]; then
  echo "error: cache-venv python not found in $VENVS" >&2
  echo "       (looked for nutricore-$venv_hash-py3.12 and nutricore-SKSdxrGe-py3.12)." >&2
  echo "       run 'poetry install' first (TD-001). If Poetry fails with" >&2
  echo "       \"No such file or directory: 'python'\", an inherited CONDA_PREFIX is" >&2
  echo "       posing as the active venv: run 'env -u CONDA_PREFIX poetry install'." >&2
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
