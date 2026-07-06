# Tech Debt

Track deferred improvements. Review monthly.

## Critical
_Blocks feature work or security risk._

_None open._

## High
_Causes recurring problems._

_None open._

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

- [ ] **TD-007**: Model self-heal (TD-005) only covers the bot path. `OpenAIService.__init__`
  always uses `settings.OPENAI_MODEL`; the persisted override is loaded once in
  `create_bot_application` and mutates the single bot-side `OpenAIService`. `app/api/v1/ai.py`
  builds a fresh `OpenAIService()` per request via `Depends()` and has no `ModelUnavailableError`
  handling, so if it were mounted it would ignore the owner's model choice and 500 on a deprecated
  model. Currently latent: the `ai` router is **not mounted** in `main.py` (dead endpoints).
  Clean fix (also addresses TD-008): load the persisted override inside `OpenAIService`
  construction (a best-effort factory/`__init__` read) so every instance is consistent, and move
  `_persist_model`/`_load_persisted_model` onto the service that owns `self.model`.
  - **Priority:** Low · **Source:** /review-deep 2026-07-06 · **Created:** 2026-07-06
- [ ] **TD-008**: `telegram.py` keeps absorbing responsibilities. The TD-005 self-heal added
  `_offer_model_choice`/`process_model_choice`/`_persist_model`/`_load_persisted_model` and a third
  direct CRUD import (`crud_app_setting`) to a module that already owns the meal conversation,
  subscription gating, the admin grant command, and the consult relay. This is the pattern the
  earlier `access_control.py` / `ai_call_log_service.py` extractions set out to avoid. Extract the
  self-heal orchestration into `app/services/model_selection.py`, mirroring `access_control.py`.
  - **Priority:** Low · **Source:** /review-deep 2026-07-06 · **Created:** 2026-07-06

## Resolved
_Keep 90 days then remove._

- [x] **TD-005**: Model-deprecation self-heal — an OpenAI `model_not_found`/deprecation error no
  longer silently breaks meal logging. `OpenAIService._create` now translates it into a typed
  `ModelUnavailableError`; the meal handler catches it, fetches `/v1/models` filtered to a
  maintained family allowlist (`list_suitable_models`), and offers the candidates in a new
  `CHOOSING_MODEL` conversation state. Picking one switches the live model, persists it to a new
  `app_settings` KV table (migration `d3e4f5a6b7c8`, loaded at startup so it survives restarts),
  and **auto-retries** the stashed analysis — the flow completes without re-sending. Deviation
  from the spec: used a ReplyKeyboard state rather than an inline-keyboard `CallbackQueryHandler`,
  which keeps the retry inside the ConversationHandler with far less plumbing (same UX intent).
  Allowlist caveat from the ticket stands: `/v1/models` doesn't tag capabilities, so the family
  allowlist + non-chat-variant exclusions are a heuristic to maintain as models change.
  - **Priority:** High · **Source:** owner request 2026-07-06 (follow-up to TD-004) · **Resolved:** 2026-07-06
- [x] **TD-006**: `ai_call_logs.created_at` was `nullable=True` in the DDL while the retention
  purge filters `created_at < cutoff` — a NULL-dated row would never be pruned. Made the column
  `NOT NULL` with `server_default=func.now()` in both the model and migration `c2d3e4f5a6b7`
  (edited in place — the migration was never deployed, so no follow-up migration needed).
  - **Priority:** Low · **Source:** /review 2026-07-06 · **Resolved:** 2026-07-06
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
