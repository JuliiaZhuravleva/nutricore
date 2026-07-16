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

_None open._

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
  - **Note 2026-07-07:** re-flagged by the photo-product-lookup specialist consult ("already-ballooning
    `telegram.py`"). That plan's A4 will extract a `product_lookup_service.py` (same pattern) — a second
    data point that `telegram.py` needs decomposition, not just per-feature carve-outs. Consider a
    broader pass (meal-conversation orchestration vs. service modules) once product-lookup lands.
  - **Note 2026-07-07 (post-review):** the /review-deep fix pass added a `get_openai_service()` factory
    in `openai_service.py` (H2) that removed the `product_lookup_service → telegram` circular import —
    one dependency untangled. Still open: the pipeline reply formatting (`_source_badge` /
    `_resolution_detail_lines`) lives in `telegram.py`; moving it onto `ResolutionResult`
    (e.g. `.to_reply_lines()`) would keep the handler thinner as A8/A9/A10 add strategies.
- [ ] **TD-010**: TD-009 follow-ups intentionally deferred (the "at minimum file_id" pass shipped).
  (1) **Disk-bytes archival** — `inbound_messages` keeps only the Telegram `file_id`; a photo is
  lost if Telegram ever drops the file. For Telegram-independent replay, also persist the base64'd
  bytes to a **host bind-mount under `/Users/claw/data/...`** on the mini (survives the disposable
  Colima VM), with a path config + a compose volume. (2) **Delete-on-request** — a `/forget`-style
  command to purge a user's inbound rows (+photos) on demand; low value while single-owner, but the
  retention purge is currently the only deletion path. (3) **Reprocess → meal** — `/reprocess` only
  re-analyzes and fills `ai_analysis`; it does not create a `meals` row (keeps the confirm step). A
  future "replay straight into a confirmed meal" is possible but needs a `meal_time` decision.
  - **Priority:** Low · **Source:** TD-009 scope decision 2026-07-06 · **Created:** 2026-07-06
- [ ] **TD-011**: photo-product-lookup accuracy residuals (accepted during the /review-deep fix pass;
  all mitigated by the transparency reply — path/product/gram-basis shown, correctable at confirm).
  (1) **Misread barcode passing the check digit** — the new GS1 mod-10 validation (H1) rejects most
  single-digit vision misreads, but a misread that both passes the check digit AND is a registered
  product would still return that *other* product's КБЖУ at high confidence. A name-vs-vision overlap
  downgrade was deliberately **not** implemented: OFF names are often English/brand while vision
  returns Russian, so token-overlap would false-positive on correct matches and make UX worse. Revisit
  if a cheap language-robust cross-check appears (e.g. compare on brand tokens only, or a translation
  step). (2) **Portion semantics** — `BarcodeOFFStrategy` scales OFF per-100g by vision's *whole-photo*
  portion estimate; for a packaged item vision may estimate the whole package, not the eaten serving,
  inflating КБЖУ. Mitigated by the shown gram basis + correct-at-confirm.
  - **Priority:** Low · **Source:** /review-deep 2026-07-07 (photo-product-lookup) · **Created:** 2026-07-07
- [ ] **TD-013**: Confidence gate — three separate scores + minimal clarification. Resolution today
  yields a single per-strategy tier (high/med/low) shown as one badge, and the only check is human
  Да/Нет — no inline portion/gram correction (reject-and-resend is the only path). The ratified target
  (`docs/diagrams/input-processing-flow.md`, «Следующее» tier) splits confidence into **identity /
  portion / nutrition** with reconciliation rules → auto-accept when confident, else **minimal
  clarification via quick-select buttons** (portion S/M/L, candidate pick). Research basis: "smallest
  question that resolves the largest uncertainty" + separate identity/portion/nutrition confidences
  (`docs/researches/…ecosystem.md`). **Not** part of round-2 (which only adds resolution strategies —
  the confidence phase is unchanged). Pairs with the `_source_badge`/`_resolution_detail_lines` →
  `ResolutionResult.to_reply_lines()` move noted in TD-008.
  - **Priority:** Low · **Source:** input-processing-flow diagram + round-2 scope analysis 2026-07-08 · **Created:** 2026-07-08
- [ ] **TD-017**: Quick-pick from saved/recent meals (Telegram UX) — deferred from the personal-food-db
  plan (item **B5**). The RAG-reuse half of TD-014 shipped (personal_foods + `SavedFoodRAGStrategy` +
  learning loop); this is the remaining **capture-side shortcut**: recency/frequency buttons → change
  quantity → **skip analysis entirely** (direct DB query, not RAG — needs only B1/B4). All three consulted
  specialists recommended deferring it (blocked on the now-shipped B1–B4; `telegram.py` has no
  InlineKeyboard/CallbackQueryHandler precedent — everything is ReplyKeyboard). Needs its own small plan:
  decide **ReplyKeyboard** (consistent, low-risk, ~3.5h) vs **InlineKeyboard** (richer, new pattern, +risk).
  `times_used` / `last_used_at` on `personal_foods` already feed it.
  - **Priority:** Low · **Source:** personal-food-db plan B5 deferral 2026-07-11 · **Created:** 2026-07-11
- [ ] **TD-016**: A9 web-search uses the **legacy** Responses-API tool type `web_search_preview`
  (`OpenAIService.web_search_nutrition`), not the GA `web_search`. Works today (no GA-only controls
  used), but modernize to `web_search` when convenient — and revisit if the preview type is
  deprecated. Surfaced by the round-2 /review-deep as a "future modernization note only" (not a bug).
  - **Priority:** Low · **Source:** /review-deep 2026-07-08 (round-2) · **Created:** 2026-07-08

## Resolved
_Keep 90 days then remove._

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
