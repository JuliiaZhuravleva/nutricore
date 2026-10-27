# Tech Debt — backlog

<!-- split by scripts/debt-split/split.py — item bodies are byte-identical to the original -->

Medium / Low / без приоритета, открытые. Не читать целиком.
Активное: `_tech-debt.md`.

## Medium

- [ ] **TD-024**: Продумать оптимизацию памяти контейнеров (её слово 19.10: память mini на пределе, общая ВМ colima 3,0 из 3,5 ГБ)
  - **Замер 19.10:** 5 контейнеров, вместе ~410 МБ, у всех без лимита (видят все 2,8 ГиБ ВМ). Больше всех `celery_worker` — 142 МБ, затем `app` — 86, `telegram_bot` — 78, `celery_beat` — 73.
  - **Что выяснить:** пик за неделю; поставить лимиты `mem_limit` по пику, чтобы один контейнер не съел всю ВМ; нужен ли постоянный `celery_beat`/`celery_worker` на такой нагрузке.
  - **Priority:** Medium
  - **Added:** 2026-10-19
  - **Source:** openclaw-setup, обзор памяти mini (TD-225 там)

- [ ] **TD-018**: A photo caption can silently disable three resolution strategies.
  `_NAME_SEARCH_MAX_FOODS = 2` gates NameOFF / LabelOCR / NameWebSearch on the vision food count.
  Now that the caption reaches the vision call, a note naming a sauce plus a visible dish can make
  vision return 3+ foods, so photos that previously reached those strategies fall straight to
  low-confidence vision. No signal distinguishes "gate skipped me" from "searched, found nothing" —
  `strategy_attempts` records both as `miss`. Watch it in `resolution_signals` before changing the
  cap; the cap exists to avoid confident-wrong matches on multi-item plates.
  - **Priority:** Medium · **Source:** review of fix/meal-pipeline-caption-websearch-rag · **Created:** 2026-09-13

- [ ] **TD-019**: The personal-food ANN has never been exercised against real pgvector.
  The `:embedding::vector` defect (fixed 2026-09-13) is now guarded by a compile-level control that
  asserts the shipped Postgres SQL and its bind params — but that proves the statement is
  well-formed, not that the matching WORKS. The `<=>` distances, the 1536-dim match and
  `SAVED_FOOD_SIM_THRESHOLD = 0.15` have never run against data, and the strategy sits FIRST in the
  pipeline, so a bad threshold turns a silent miss into a confidently wrong answer. Needs either a
  Postgres-backed test path (docker-compose has a `db` service; would need a `postgres` pytest
  marker) or a deliberate post-deploy check of the first real hits.
  - **Priority:** Medium · **Source:** review of fix/meal-pipeline-caption-websearch-rag · **Created:** 2026-09-13

- [ ] **TD-020**: The meal-of-day answer is stored but neither shown nor correctable — and it is not a time.
  `meals.meal_type` now keeps "Завтрак"/"Обед"/"Ужин" (migration `b1c2d3e4f5a6`), but: the reply never
  mentions it, the confirm step cannot change it (only the REST `MealUpdate` can), and picking
  "Завтрак" at 16:00 still records `meal_time = now()` — deriving 08:00 was rejected as inventing a
  timestamp the user never gave, so the real eating time is still uncaptured. `NULL` also conflates
  "the user chose Сейчас" with "row predates the migration". A user-settable time plus surfacing the
  label at the confirm step is the honest fix; it needs a product decision, not just code.
  - **Priority:** Medium · **Source:** review of fix/meal-pipeline-caption-websearch-rag · **Created:** 2026-09-13

- [ ] **TD-025**: Saving a meal waits on Redis: with the broker down, «Прием пищи сохранен!» comes ~20 s late.
  `confirm_meal` → `_schedule_personal_food_save` → `embed_and_save_personal_food.delay(...)`
  (`app/services/telegram.py` ~406) publishes to Celery synchronously, before the reply. With Redis
  unreachable the publish blocks until kombu gives up; the `except` then logs a warning, so the meal is
  saved and nothing breaks — only the reply is late. Observed 2026-10-28 on the mini (local Redis not
  running): every confirm-path test took 20.6–21.0 s (`--durations`), the full gate 3 min 51 s for 647
  tests. The comment at the call site says tests "mock .delay", but most confirm tests in
  `tests/test_meal_handler.py` and `tests/test_barcode_integration.py` don't. Two halves:
  - **Prod:** bound the publish — e.g. `apply_async(..., retry=False)` plus a short
    `broker_connection_timeout`, or send the reply first / run the enqueue in `asyncio.to_thread`.
    Which knob produces the ~20 s is not pinned yet (INFERRED: kombu connect retries).
  - **Tests — DONE 2026-10-28 (feat/meal-reply-ux):** autouse `celery_delay` fixture in
    `tests/conftest.py`; `test_save_button_saves_what_is_on_screen` asserts the enqueue and its
    per-100g kwargs. Full suite ~4 s instead of 3 min 51 s. Only the prod half stays open.
  - **Priority:** Medium · **Source:** feat/favorites-menu full gate run · **Created:** 2026-10-28

## Low

- [ ] **TD-021**: `brand` reaches the personal food DB but is never shown to the user.
  Every OFF-backed strategy resolves a brand, and since 2026-09-13 it is finally persisted to
  `personal_foods.brand` — but the reply shows only `Продукт: <name>`, including at the "проверь"
  confidence tiers where the user is being asked to verify the match. A store brand and a name brand
  with the same generic product name are indistinguishable in the reply.
  - **Priority:** Low · **Source:** review of fix/meal-pipeline-caption-websearch-rag · **Created:** 2026-09-13

- [ ] **TD-022**: openai is pinned to `^2.54.0` while 3.x is current.
  3.x switches the httpx stack and reshapes the web-search tool types, so the bump was deliberately
  stopped at 2.x. The tripwires for a future migration: three test sites construct `openai.NotFoundError`
  / `BadRequestError` from `httpx.Request`/`httpx.Response`, and `is_model_not_found_error` reads that
  shape. Floors are asserted in `tests/test_dependency_floors.py` — update them with any bump.
  - **Priority:** Low · **Source:** fix/meal-pipeline-caption-websearch-rag · **Created:** 2026-09-13

- [ ] **TD-023**: Test-gate and repo hygiene left alone on purpose.
  (1) No `[tool.pytest.ini_options]` anywhere — the suite's configuration is accidental; adding
  `--strict-markers --strict-config` would also be the cheapest home for a `postgres` marker (TD-019),
  but it can turn a currently-green gate red on pre-existing warnings, so it needs its own pass.
  (2) `.gitignore` does not cover `.claude/handoffs/`, so per-session live handoff files sit untracked
  forever — decide whether they are committed or ignored.
  - **Priority:** Low · **Source:** review of fix/meal-pipeline-caption-websearch-rag · **Created:** 2026-09-13

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

