# Tech Debt

Track deferred improvements. Review monthly.

## Critical
_Blocks feature work or security risk._

- [ ] **TD-004**: Food-image analysis is broken — `app/services/openai_service.py:53`
  hardcodes `model="gpt-4-vision-preview"`, which OpenAI has **deprecated/removed**. Live on
  the mini the bot answers text but any photo fails with `404 model_not_found`
  (`Error analyzing food image: Error code: 404 ... gpt-4-vision-preview has been deprecated`).
  Two problems in `analyze_food_image`:
  1. Dead model, and it **ignores** the configured `settings.OPENAI_MODEL` (`gpt-4o-mini`).
     Fix: `model=settings.OPENAI_MODEL` (gpt-4o / gpt-4o-mini are vision-capable), don't hardcode.
  2. Latent: the image part is `{"type": "image_url", "image_url": image_url}` (bare string) —
     current models require the nested form `{"type": "image_url", "image_url": {"url": image_url}}`.
     Swapping the model alone will likely then fail on the image_url format; fix both together.
  - **Priority:** Critical · **Source:** first live run on the mini 2026-07-06 · **Created:** 2026-07-06

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

## Resolved
_Keep 90 days then remove._

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
