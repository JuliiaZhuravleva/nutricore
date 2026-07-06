# Handoff — fix/td-004-food-image (2026-07-06, session 2)

## State
Branch `fix/td-004-food-image`, **12 commits total, 3 NOT pushed** (99a879f, ebcf233,
965a4db), **NOT merged**. Tree clean. Suite green (**91 passed**, cache-venv python — TD-001).
PR still not opened → https://github.com/JuliiaZhuravleva/nutricore/pull/new/fix/td-004-food-image

## What shipped this session (on top of the earlier 9 pushed commits)
- **TD-006** (`99a879f`) — `ai_call_logs.created_at` is now `NOT NULL` + `server_default=func.now()`
  (a NULL-dated row would escape the retention purge). Migration `c2d3e4f5a6b7` edited in place
  (never deployed, so no follow-up migration).
- **TD-005** (`ebcf233`) — self-heal on OpenAI model deprecation:
  - `OpenAIService._create` wraps every chat call, translates a model 404/deprecation into a typed
    `ModelUnavailableError`; `list_suitable_models()` filters `/v1/models` by a maintained family
    allowlist (`_SUITABLE_MODEL_PREFIXES` minus `_EXCLUDE_SUBSTRINGS`), static fallback.
  - New `CHOOSING_MODEL` **ReplyKeyboard** conversation state (not an inline
    `CallbackQueryHandler` — keeps the retry inside the ConversationHandler). Picking a model
    switches the live model, persists it to a new generic `app_settings` KV table (model/crud +
    migration `d3e4f5a6b7c8`, loaded at startup via `_load_persisted_model`), and **auto-retries**
    the stashed analysis — completes without re-sending.
- **Review fixes** (`965a4db`, from `/review-deep`) — `foods` element str-coercion (a swapped-in
  model returning dicts/numbers would crash `", ".join` and silently drop a billed analysis),
  tightened `is_model_not_found_error`, `crud_app_setting.set` rollback, `app_settings.created_at`,
  `_create` return hint.

## Next up
1. **Push** the 3 commits (`git push` — upstream already set) and **open + merge the PR**.
2. **Deploy** on the mini: `alembic upgrade head` (creates `ai_call_logs` + `app_settings`).
3. Open debt: **TD-001** (poetry venv, Medium), **TD-007** (self-heal doesn't cover the *unmounted*
   `ai.py` per-request `OpenAIService`; clean fix = load override in the service's construction),
   **TD-008** (extract self-heal into `app/services/model_selection.py`, mirror `access_control.py`).

## Gotchas
- Tests: `~/Library/Caches/pypoetry/virtualenvs/nutricore-SKSdxrGe-py3.12/bin/python -m pytest`
  (not `poetry run` — TD-001).
- `OpenAIService` is a process-wide singleton; a test that switches its model must restore it
  (see the `_restore_model` autouse fixture in test_meal_handler.py).
- Monkeypatch `SessionLocal` on the module that *uses* it — `telegram` AND `ai_call_log_service`.
- Keep `black`/`isort` scoped to touched files — a project-wide `black app/ tests/` reformats ~30
  unrelated files (had to revert that this session).
- Two stale `HANDOFF-*timestamp*.md` files are untracked cruft — safe to delete.

_(Supersedes HANDOFF-20260703-012230.md and HANDOFF-20260706-202413.md.)_
