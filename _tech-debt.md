# Tech Debt

Track deferred improvements. Review monthly.

## Critical
_Blocks feature work or security risk._

_None open._

## High
_Causes recurring problems._

- [ ] **TD-005**: Handle model-deprecation gracefully instead of failing — when an OpenAI call
  returns `model_not_found` / a "model has been deprecated" error (see TD-004), don't abort the
  user's action. Instead:
  1. Catch the deprecation/404-model error in the OpenAI service.
  2. Fetch the current model list (OpenAI `GET /v1/models`) and **filter to suitable ones**
     (e.g. vision-capable chat models for image analysis).
  3. Surface the candidates as **inline-keyboard buttons** in the bot ("the model is gone — pick a
     new one"), let the user choose.
  4. **Persist** the choice (settings / DB) and **continue/retry** the original operation with the
     selected model — so the flow completes without the user re-sending.
  - This makes the bot self-heal against model churn (models get deprecated periodically).
  - **Caveat for the implementer:** `/v1/models` does not cleanly tag capabilities (vision, JSON
    mode), so "suitable" needs a maintained allowlist or heuristic, not just the raw list.
  - **Note (2026-07-06):** TD-004 is now fixed, so the immediate `model_not_found` break is no
    longer live — this remains open as insurance against *future* model churn, not an active bug.
  - **Priority:** High · **Source:** owner request 2026-07-06 (follow-up to TD-004) · **Created:** 2026-07-06

## Medium
_Slows development but doesn't block._

- [ ] **TD-001**: Poetry venv recreation loop — `poetry run` keeps recreating an empty
  in-project/cache venv without the project deps (its base interpreter appears dangling),
  so `poetry run pytest`/`black` intermittently fail with `ModuleNotFoundError`. Workaround:
  `poetry install` then invoke the cache-venv python directly
  (`~/Library/Caches/pypoetry/virtualenvs/nutricore-SKSdxrGe-py3.12/bin/python -m pytest`).
  Proper fix: recreate the venv against a stable Python (`poetry env remove --all && poetry install`),
  and pin the interpreter so nvm/pyenv changes don't dangle it.
  - **Priority:** Medium
  - **Source:** /wrap session 2026-07-02 (env broke mid-wrap; code was green)
  - **Created:** 2026-07-02

## Low
_Track for later._

- [ ] **TD-006**: `ai_call_logs.created_at` is `nullable=True` in the DDL while the retention
  purge filters `created_at < cutoff`. Harmless today (the model's Python-side default always
  sets it), but a NULL-dated row would never be pruned — silently leaking past retention.
  Fix: make the column `NOT NULL` with a `server_default` (e.g. `func.now()`) in the model +
  a follow-up migration.
  - **Priority:** Low · **Source:** /review 2026-07-06 · **Created:** 2026-07-06

## Resolved
_Keep 90 days then remove._

- [x] **TD-004**: Food-image analysis was broken — `analyze_food_image` had **four** faults,
  each fatal on its own: (1) hardcoded the removed `gpt-4-vision-preview` (→ `404
  model_not_found`) instead of `self.model` (`settings.OPENAI_MODEL`); (2) bare-string
  `image_url` instead of the nested `{"url": ...}` form; (3) the image handler never
  `json.loads`-ed the returned JSON string before indexing it (→ `TypeError`); (4) the
  prompt emitted `portion_estimate` while the handler read `portion` (→ `KeyError`). Fixed
  all four in `openai_service.py` + the image branch of `telegram.py::process_meal_input`,
  added `tests/test_openai_service.py`, and deleted the dead duplicate `app/services/openai.py`.
  - **Priority:** Critical · **Source:** first live run on the mini 2026-07-06 · **Resolved:** 2026-07-06
- [x] **TD-002**: Bot token leaked into logs — httpx logged the full Telegram Bot API
  URL (token in path) at `INFO`. Fixed by clamping `httpx`/`httpcore` loggers to
  `WARNING` in `create_bot_application()` (covers both polling and webhook entry points).
  - **Priority:** High · **Source:** external review note 2026-07-06 · **Resolved:** 2026-07-06
- [x] **TD-003**: `ALLOWED_HOSTS` defaulted to `['*']` (TrustedHostMiddleware no-op).
  Narrowed the default to `["127.0.0.1", "localhost"]` in `config.py` + `.env.example`;
  prod adds its domain via env.
  - **Priority:** Low · **Source:** external review note 2026-07-06 · **Resolved:** 2026-07-06

---

### When to Add
- Skipped tests to meet deadline
- Used workaround instead of proper fix
- Copy-paste instead of abstract
- Disabled linter rules
- Known performance issue deferred
- Bug acknowledged but deprioritized

### Entry Format
```
- [ ] **TD-NNN**: Brief description
  - **Priority:** Critical | High | Medium | Low
  - **Source:** what identified this (review, bug report, etc.)
  - **Created:** YYYY-MM-DD
```
