# Tech Debt — архив

<!-- split by scripts/debt-split/split.py — item bodies are byte-identical to the original -->

Закрытые пункты. Тела сохранены дословно — в них записано,
что измерено и что НЕ является решением. Не удалять и не сжимать.
Активное: `_tech-debt.md`.

## Critical

- [x] **TD-004**: Food-image analysis was broken — `analyze_food_image` had **four** faults,
  each fatal on its own: (1) hardcoded the removed `gpt-4-vision-preview` (→ `404
  model_not_found`) instead of `self.model` (`settings.OPENAI_MODEL`); (2) bare-string
  `image_url` instead of the nested `{"url": ...}` form; (3) the image handler never
  `json.loads`-ed the returned JSON string before indexing it (→ `TypeError`); (4) the
  prompt emitted `portion_estimate` while the handler read `portion` (→ `KeyError`). Fixed
  all four in `openai_service.py` + the image branch of `telegram.py::process_meal_input`,
  added `tests/test_openai_service.py`, and deleted the dead duplicate `app/services/openai.py`.
  - **Priority:** Critical · **Source:** first live run on the mini 2026-07-06 · **Resolved:** 2026-07-06

## High

- [x] **TD-009**: Inbound messages are now persisted on receipt (history + replay). A new
  `inbound_messages` table (model/schema/CRUD/`inbound_message_service.py`/migration `e4f5a6b7c8d9`)
  captures each meal photo/text the moment it arrives — **before** the OpenAI call, so even a
  photo-fetch or analysis failure leaves a `status=pending` row (the deprecated-model outage used to
  lose the message entirely: Telegram had ack'd, the DB held nothing). `process_meal_input` records
  it best-effort; `_run_meal_analysis` flips it `analyzed` (with parsed nutrition) / `failed` (with
  the error), threaded through the TD-005 self-heal retry. Owner-only `/reprocess` re-analyzes
  pending/failed rows with the current model (capped at `REPROCESS_BATCH_LIMIT=20`/call; stops early
  if the model is still unavailable) — the "after a model fix, replay what failed" mechanism. Photos
  kept as `file_id` (bytes-on-disk deferred → TD-010). Retention via `INBOUND_MESSAGE_RETENTION_DAYS`
  (60d) + a daily Celery-beat `purge_inbound_messages`. Reviewed via /review-deep (bug/arch/silent/
  claudemd) → applied fixes: reply-failure no longer mislabels a clean analysis as `failed`, shared
  `_analyze_and_parse` (replay now hits the ai_call_logs audit trail + no duplicated pipeline),
  `mark_*` return bool so /reprocess counts only persisted writes, `exc_info` on best-effort logs.
  18 new tests; 109 green. Scope (owner-picked): file_id-only + `/reprocess` command;
  disk-bytes/delete-on-request/reprocess→meal deferred to TD-010.
  - **Priority:** High · **Source:** owner insight after the lost-message incident 2026-07-06 · **Resolved:** 2026-07-06

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

- [x] **TD-002**: Bot token leaked into logs — httpx logged the full Telegram Bot API
  URL (token in path) at `INFO`. Fixed by clamping `httpx`/`httpcore` loggers to
  `WARNING` in `create_bot_application()` (covers both polling and webhook entry points).
  - **Priority:** High · **Source:** external review note 2026-07-06 · **Resolved:** 2026-07-06

## Medium

- [x] **TD-001**: Poetry venv recreation loop — `poetry run` used to spawn an empty venv without the
  project deps (`ModuleNotFoundError`) because the venv's **base interpreter dangled** on nvm/pyenv
  drift. Root cause is gone now that pyenv global is a stable `3.12.1`, and the durable fix is a
  **local pin**: added `.python-version` (`3.12.1`) so the project always resolves to pyenv 3.12.1
  regardless of a future global switch — the base can no longer dangle. Verified: `poetry env info`
  → `Valid: True`, `poetry run pytest` works again, the venv hash is unchanged
  (`nutricore-SKSdxrGe-py3.12`, so `scripts/test.sh`'s hardcoded path still resolves). Left
  `scripts/test.sh` on the cache-venv python defensively (it's allowlisted for specialist runs and
  independent of `poetry run` health) — both paths now work.
  - **Priority:** Medium · **Source:** /wrap 2026-07-02 (env broke mid-wrap) · **Resolved:** 2026-07-08
    (bug non-reproducing after pyenv stabilized; pinned to prevent recurrence)

- [x] **TD-015**: Meal-confirm step no longer silently loses the analyzed draft, and now ships
  buttons. **(1)** The "Всё верно? (Да/Нет)" prompt (`_run_meal_analysis`) is sent with a
  `confirm_keyboard` **ReplyKeyboardMarkup** (`[["Да", "Нет"]]`, consistent with the TD-005 model
  picker). **(2)** `confirm_meal` now classifies the reply via `_confirm_intent` (case-insensitive,
  trailing-punctuation-tolerant) into **affirm / reject / correction**: an explicit `Да` (incl.
  lowercase `да`, `ага`, `верно`, 👍) saves; an explicit `Нет`/`Отмена` discards + restarts; **any
  other free text is treated as a correction** — re-analyzed as a new text description while staying
  in `CONFIRMING_MEAL` (meal_time preserved; the prior attempt's stale photo/`resolution_source` are
  dropped, same hygiene as the reject path). The old `if text == "Да": save else: discard+restart`
  wiped the draft on a mistyped/lowercase reply or a real correction (owner hit it live 2026-07-08).
  Photo+text *merge* for a photo-draft correction is still out of scope (re-analyzes text only — Gap ①
  in the input-processing diagram → TD-013). 5 new tests; 325 green.
  - **Priority:** Medium · **Source:** owner live report 2026-07-08 · **Resolved:** 2026-07-08
  - **Related:** TD-013 — the confidence-gate redesign supersedes this with quick-select buttons +
    inline correction; TD-015 is the shipped papercut fix.

## Low

- [x] **TD-007 / TD-008**: Model self-heal (TD-005) extracted out of `telegram.py` and made consistent
  across every `OpenAIService` instance. New **`app/services/model_selection.py`** (mirrors
  `access_control.py`) now owns the persisted model override: `OPENAI_MODEL_SETTING_KEY`,
  `get_persisted_model()`, `persist_model()`, `apply_persisted_model(service)`. **TD-008:** `telegram.py`
  lost `_persist_model`/`_load_persisted_model` and its `crud_app_setting` import — `process_model_choice`
  calls `persist_model`, startup calls `apply_persisted_model(...)`. **TD-007:** `OpenAIService.__init__`
  now best-effort loads the override (`get_persisted_model() or settings.OPENAI_MODEL`), so a fresh
  per-request service (e.g. the still-unmounted `ai.py` `Depends()` path) honours the owner's in-chat
  model switch instead of silently using the default. No import cycle (the KEY moved into
  `model_selection`, which imports only crud + SessionLocal). Tests: conftest autouse keeps unit
  construction hermetic (no real DB read); +2 construction tests; self-heal tests re-pointed to the new
  module. 405 green.
  - **Priority:** Low · **Source:** /review-deep 2026-07-06 · **Resolved:** 2026-07-12

- [x] **TD-016**: A9 web-search modernized from the legacy Responses-API tool type `web_search_preview`
  to the GA `web_search` in `OpenAIService.web_search_nutrition`. Bare `{"type": "web_search"}` — behaviour
  preserved (`external_web_access` defaults to true = live access; none of the GA-only controls
  filters/external_web_access/token-budget are used). Verified GA shape against OpenAI docs. No test
  asserted the tool-type string, so a drop-in swap.
  - **Priority:** Low · **Source:** /review-deep 2026-07-08 (round-2) · **Resolved:** 2026-07-12

- [x] **TD-012**: pre-existing flake8 debt in the photo-product-lookup test files cleaned up in a
  flake8 pass (F401/F811/F821/F841): dropped unused imports (`patch` in `test_extract_barcode.py`;
  `SimpleNamespace` in `test_open_food_facts_service.py`; `dataclass`/`Optional`/`patch` in
  `test_product_lookup_service.py`), the unused `result1`/`web_mock` locals, and — the real cruft — a
  **dead no-op helper** in `test_product_lookup_service.py`: a `with patch.object(pls, "_extract_signals",
  wraps=lambda ...: _patched_extract_signals(...)): pass` whose body was empty and whose
  `_patched_extract_signals` was undefined (F821, never executed). Simplified `_patch` to just return the
  fake service. Also the long-standing `telegram.py` F841 `'ve'` (`except ValueError as ve` → `except
  ValueError`). No runtime behaviour change → landed on `main` directly (docs/no-runtime per RELEASE.md).
  403 tests green; flake8 F-checks clean on all four files.
  - **Priority:** Low · **Source:** /review-deep 2026-07-07 (flake8 on touched files) · **Resolved:** 2026-07-12

- [x] **TD-014** (RAG-reuse core): Personal-food DB + RAG reuse **shipped & deployed** via the
  `plan/personal-food-db` multi-agent plan (ADR-0003). New `personal_foods` + `personal_food_embeddings`
  (pgvector `VECTOR(1536)` + HNSW), `SavedFoodRAGStrategy` (`source_id=saved_rag`: exact-barcode
  short-circuit + user-scoped embedding ANN) plugged in after `barcode_off`, `OpenAIService.embed_text`,
  and a Celery fire-and-forget **learning loop** that upserts each confirmed food (+ alias embeddings) with
  dedup. Deploy: Postgres image → `pgvector/pgvector:pg15`; **no new required env**. A `/review-deep` pass
  fixed **F1–F11** — incl. a critical `Decimal×float` bug (feature was dead on Postgres, hidden by the
  SQLite test gap), barcode-vs-fuzzy ordering, and a Celery event-loop bug — plus dedup/observability
  hardening; 403 tests green. **Quick-pick UX (B5) deferred → TD-017.** Merged to main via
  `nutricore-release` (`9505093`, `e6ccb92`).
  - **Priority:** Low · **Source:** input-processing-flow «Следующее» tier · **Resolved:** 2026-07-11

- [x] **TD-006**: `ai_call_logs.created_at` was `nullable=True` in the DDL while the retention
  purge filters `created_at < cutoff` — a NULL-dated row would never be pruned. Made the column
  `NOT NULL` with `server_default=func.now()` in both the model and migration `c2d3e4f5a6b7`
  (edited in place — the migration was never deployed, so no follow-up migration needed).
  - **Priority:** Low · **Source:** /review 2026-07-06 · **Resolved:** 2026-07-06

- [x] **TD-003**: `ALLOWED_HOSTS` defaulted to `['*']` (TrustedHostMiddleware no-op).
  Narrowed the default to `["127.0.0.1", "localhost"]` in `config.py` + `.env.example`;
  prod adds its domain via env.
  - **Priority:** Low · **Source:** external review note 2026-07-06 · **Resolved:** 2026-07-06

