#!/usr/bin/env bash
# Chat simulator: the real bot (create_bot_application, real handlers, a SQLite
# file) behind a fake Telegram, driven one command at a time. For agents and
# people walking a persona through a scenario; no model, no network, no key.
#
# Usage:  ./scripts/sim.sh <state-dir> <command> [args]
#   new <name> [--owner] [--zone Europe/Berlin]   a person (the first is --owner);
#                                                 no --zone = no zone chosen
#   as <name>                                     act as them from now on
#   say "<text>"                                  a text message
#   voice "<transcript>"                          a voice note heard as this text
#   photo "<what vision sees>" [--caption "..."]  a photo, read as that text
#   tap "<button label>" [--msg N]                inline button on the latest
#                                                 message having it (or #N); else
#                                                 a reply-keyboard key
#   reply N "<text>"                              reply to message #N
#   clock [2026-11-06T21:00 | 21:00]              show / set (person's zone, forward)
#   advance 40m|2h|1d [--tick]                    move the clock (--tick: every beat)
#   tick                                          run every periodic push due now
#   chat [--last N]                               the chat as text, numbered
#   day [YYYY-MM-DD]                              records: time, name, precision,
#                                                 time_source
# Food text is split on commas and «и» into dishes with fixed numbers.
# Each command prints only what changed. More: tests/sim/README.md
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd -P)"
if [[ $# -lt 2 ]]; then
  sed -n '2,26p' "$0" >&2
  exit 2
fi
STATE="$1"
shift
mkdir -p "$STATE"
STATE="$(cd "$STATE" && pwd -P)"

# The same python ./scripts/test.sh uses (see there for why not `poetry run`).
VENVS="$HOME/Library/Caches/pypoetry/virtualenvs"
venv_hash="$(printf '%s' "$REPO" | openssl dgst -sha256 -binary | base64 | tr '+/' '-_' | cut -c1-8)" || venv_hash=""
VENV_PY=""
for name in "nutricore-$venv_hash-py3.12" "nutricore-SKSdxrGe-py3.12"; do
  if [[ -x "$VENVS/$name/bin/python" ]]; then
    VENV_PY="$VENVS/$name/bin/python"
    break
  fi
done
if [[ -z "$VENV_PY" && -x "$REPO/.venv/bin/python" ]]; then
  VENV_PY="$REPO/.venv/bin/python"
fi
if [[ -z "$VENV_PY" ]]; then
  echo "error: no nutricore venv found; run 'poetry install' (see scripts/test.sh)" >&2
  exit 1
fi

# A clean environment: none of the shell's keys or settings reach the bot.
exec env -i HOME="$HOME" PATH="/usr/bin:/bin" LANG="en_US.UTF-8" \
  PYTHONPATH="$REPO" PYTHONIOENCODING="utf-8" PYTHONDONTWRITEBYTECODE=1 \
  "$VENV_PY" -m tests.sim.cli "$STATE" "$@"
