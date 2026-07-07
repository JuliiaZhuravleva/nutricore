# Session conventions — nutricore

Read this before writing or verifying code. It is loaded by the plan orchestrator
(the `## Orchestrator config` block) and by specialist agents (the conventions below).

## Orchestrator config

```yaml
plans_dir: docs/plans/
plan_id_series: letter-number
budget:
  max_usd_per_item: 6.00
  max_usd_per_plan: 20.00
```

## Conventions for specialists (READ FIRST)

### Running tests — use the wrapper, nothing else
Run the suite with the repo's canonical runner:

```
./scripts/test.sh                          # full suite
./scripts/test.sh tests/test_foo.py        # one file
./scripts/test.sh -k some_name -x          # any pytest args pass through
```

**Do NOT use `poetry run pytest` or a bare `python -m pytest`.** Both are broken in
this repo (TD-001: `poetry run` recreates an empty venv without the deps; bare `python`
is the wrong interpreter). `poetry run …` is also not in the allowlist, so it will be
DENIED and waste your budget. `./scripts/test.sh` wraps the correct cache-venv python and
is allowlisted. Always verify your change by running it before reporting PASS.

### Formatting — touched files only
Run `black`/`isort` **only on files you changed**. The repo is NOT uniformly black-clean;
a wholesale `black app/ tests/` reformats ~30 unrelated files. The flake8 (default 79) vs
black (88) E501 noise is pre-existing — don't chase it.

### Gotchas that cost real debugging time
- **`OpenAIService` is a process-wide singleton.** A test that switches its `.model` must
  restore it (see the `_restore_model` autouse fixture in `tests/test_meal_handler.py`).
- **Monkeypatch `SessionLocal` on the module that USES it**, not on `app.db.session`
  (e.g. `app.services.telegram`, `app.services.inbound_message_service`,
  `app.services.ai_call_log_service`).
- **API endpoint tests:** construct `TestClient(app, base_url="http://localhost")` —
  `TrustedHostMiddleware` rejects the default `testserver` host (`400 Invalid host`).
  Importing `app.main` builds the bot app via a best-effort DB read; using `TestClient(app)`
  NOT as a context manager skips the Telegram/webhook startup events.
- **SQLite drops tzinfo on read-back** in tests — compare tz-naive where the test DB is SQLite.

### REST auth convention
New auth gates go in `app/core/deps.py` as `require_*` FastAPI dependencies, **fail-closed**,
comparing secrets via `_token_matches` (constant-time `hmac.compare_digest`, encodes to bytes
for non-ASCII safety) — **never `!=`**. Apply via `dependencies=[Depends(require_*)]`.

### Product direction (for the photo-product-lookup work)
- North star + operating principles: [`docs/product-philosophy.md`](../../docs/product-philosophy.md)
  — best-guess + seamless, but transparent + correctable; surface the resolution path and key
  intermediate values; log mispredictions.
- Pipeline design to implement against:
  [`docs/decisions/ADR-0001-pluggable-nutrition-resolution-pipeline.md`](../../docs/decisions/ADR-0001-pluggable-nutrition-resolution-pipeline.md).
  Barcode→OFF is the first strategy in that pluggable pipeline — do not hard-wire it into
  `process_meal_input`.
