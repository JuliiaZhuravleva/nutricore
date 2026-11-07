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
- [ ] **TD-238**: The new-person walkthrough left findings open. Worst: an open dish or time screen does not hold the next text, so a typed correction becomes a new record. Also: silence on «привет» and /help, an empty stats picture that sounds like a reproach, three instructions after the first record, a long voice notice, a dish screen and settings over the row limit, three taps to delete, a dense ℹ️, an invitation that does not say who invites, a crowded person card.
  - **Priority:** Medium (each needs the owner's yes)
  - **Source:** fix/access-end-and-welcome
  - **Created:** 2026-11-02

- [ ] **TD-245**: Time words that were wrong before stage 6 R0 and are still wrong: «утром каша» sent at 6:30 lands on yesterday, «вчера суп в 13:00» lands on today, and «ночью» has edge cases. R0 deliberately kept the old behaviour (tests/test_time_phrase_corpus.py freezes it), so any fix must update that corpus row by row.
  - **Priority:** Medium
  - **Source:** feat/meal-reminders (stage 6)
  - **Created:** 2026-11-07

- [ ] **TD-246**: A «без цифр» mode for people for whom counting is harmful: no КБЖУ in cards, the week picture or answers, only what was eaten. ED safety (docs/researches/reminders-retention-evidence.md, «Риски»).
  - **Priority:** Medium (needs the owner's yes and wording)
  - **Source:** owner research 2026-11-07
  - **Created:** 2026-11-07

- [ ] **TD-247**: Reminder metrics, aggregate only (owner screens show usage, never chats): logging-day rate, share of reminders followed by a record within 3 h, ✖ turn-offs (also within 48 h of a send), recovery within 7 days after a re-entry message, records per 100 reminders.
  - **Priority:** Medium
  - **Source:** owner decision 2026-11-07 («Позже, отдельной задачей»)
  - **Created:** 2026-11-07

## Low

- [ ] **TD-260**: «⬅️ К ответу бота» on a typed meal still in its read draws the line with no «Считаю…». The marker was dropped there because the tapped message may not be the one the read edits at its end: a retry queued while the line was a card (`job.line_id=None`), a reply's message (time, «и сыр»), or a retry whose `shown` is the M7 text (no edit when it fails again). A marker there could stay for good. To bring it back, the tap must know the live read's line (and that read's end must always edit it). Photos and voice have the same issue with `pending_text` on a back tap. Also `_read_kind` takes the first source, so a typed meal made from a failed photo (M7c) shows the photo's «читаю…» on a back tap.
  - **Priority:** Low (needs a tap into the card and back within the seconds of a read)
  - **Source:** feat/text-reading-marker review
  - **Created:** 2026-11-08

- [ ] **TD-259**: A re-read started from the background (`read_again`: the sweep's automatic retry, /reprocess, /models) flips the line's view to pending but does not re-render it, so the line shows no «читаю…»/«Считаю…» while that read runs. Her rule (2026-11-08): a line waiting on the model always says so until the answer. Same for photos and voice.
  - **Priority:** Low (the first read, the one people watch, has the marker)
  - **Source:** feat/text-reading-marker
  - **Created:** 2026-11-08

- [ ] **TD-248**: The 🔔 custom-emoji tile is in the sticker-set source but the set was not rebuilt and uploaded; until then the reminder title falls back to the plain emoji. Verify the fallback before release, upload with the telegram-emoji-pack flow.
  - **Priority:** Low
  - **Source:** feat/meal-reminders (stage 6)
  - **Created:** 2026-11-07

- [ ] **TD-249**: R3 «По записям» should learn the hours when reminders lead to a record (response-to-prompt), not only the hours of past records.
  - **Priority:** Low (for the R3 branch)
  - **Source:** owner research 2026-11-07
  - **Created:** 2026-11-07

- [ ] **TD-250**: The week card's progress line should describe («5 из 7 дней с записями»), never evaluate. Check the current wording against R10 and the research.
  - **Priority:** Low
  - **Source:** owner research 2026-11-07
  - **Created:** 2026-11-07

- [ ] **TD-251**: Voice «ничего» (R16): `capture.read_said_nothing` stores no reading row, unlike `read_no_speech`, so the voice leaves no trace for diagnostics. Also confirm the «✓ Принято.» wording on the voice's own line with the owner.
  - **Priority:** Low
  - **Source:** feat/meal-reminders (stage 6)
  - **Created:** 2026-11-07

- [x] **TD-252** (closed 2026-11-08, owner: keep as is): Inside a reminder's recall window, a reply to a meal line that changes only grams («300») or time («в 14») does not renew the window. Owner to decide; renewing means routing those two `_on_reply` branches through `capture._renews`. Related: `_renews` marks the reminder answered for a correction of a meal that is not bound to it (review probe P19).
  - **Priority:** Low (owner's call)
  - **Source:** feat/meal-reminders (stage 6)
  - **Created:** 2026-11-07

- [ ] **TD-253**: Stage 6 review minors: flake8 E203 in tests/sim/cli.py:776; `tick` followed by `advance --tick` can run the same beat twice in the simulator; `_reply_moment` with rule day_only and of_day=True has no test; the voice path of «вчера утром каша, вечером суп» on the next day is not tested.
  - **Priority:** Low
  - **Source:** feat/meal-reminders (stage 6)
  - **Created:** 2026-11-07

- [ ] **TD-254**: «ничего» (R16) outside a live recall window costs a model read: after ✅ ОК, after an R16 answer, or more than 30 minutes after the send («почти ничего не ела» at 21:48). Option: a full `is_nothing` match within the evening of today's send, closed or not, is swallowed with no read and a short neutral line. The meal added after an R16 answer also gets the exact arrival time, not the reminder's day without a clock.
  - **Priority:** Low (owner's call)
  - **Source:** feat/meal-reminders (stage 6 persona acceptance)
  - **Created:** 2026-11-07

- [ ] **TD-255**: A bare «да», «ок» or «окей» inside a reminder's window gets no reply and leaves the reminder's buttons in place. Option: treat it like ✅ ОК (neutral, buttons removed). A bare «нет» stays silent and counts as unanswered (F2).
  - **Priority:** Low (owner's call)
  - **Source:** feat/meal-reminders (stage 6 persona acceptance)
  - **Created:** 2026-11-07

- [ ] **TD-256**: Stage 6 acceptance copy questions for the owner:
  - ~~Re-entry #2 reads like an offer~~ resolved 2026-11-08: reworded into a statement.
  - ~~The week card's «✨ Снова с записями — после паузы»~~ resolved 2026-11-08: removed.
  - The 🔔 sub-screen goes «rarer» only on the sweep after the 3rd unanswered evening, so for one day it still shows the daily line.
  - **Priority:** Low (owner's call)
  - **Source:** feat/meal-reminders (stage 6 persona acceptance)
  - **Created:** 2026-11-07

- [ ] **TD-257**: Simulator polish:
  - The fake `nutrition_of` gives no per-item numbers for multi-dish meals, so a ⭐ starred from 🍲 Блюда for one dish has null kcal.
  - The clock line hides seconds after `advance 5s`, and the README does not list the `s` unit.
  - **Priority:** Low
  - **Source:** feat/meal-reminders (stage 6 persona acceptance)
  - **Created:** 2026-11-07

- [ ] **TD-258**: Stage 6 acceptance-fix review minors:
  - When an old reminder holds a logged ⭐, the next send only strips its buttons, so the title still reads «🔔 Еда за сегодня».
  - «Утром овсянка. В обед суп.» still gets ≈ 9:00, because a period or a newline is not a clause edge in R0 (pre-existing).
  - «не было ничего», «ничего больше не было» and «ничего сегодня не ела» are not R16.
  - The 🔔 sub-screen of a person who has never logged gives no stop line.
  - A join chip tapped on the later line, then ➖ Отделить, leaves a join chip under both lines.
  - On the first reminder, «Или нажать на блюдо из ⭐:» stands before the footnote.
  - After 🕐 → ⬅️, a voice line loses its trial paragraph.
  - The rm:v test has no @BACKENDS case.
  - «по времени Новосибирск (UTC+7)» has the city in the nominative (pre-existing for every city).
  - A return after a re-entry stays sparse, because D7 resets only on an answer.
  - **Priority:** Low
  - **Source:** feat/meal-reminders (stage 6 persona acceptance)
  - **Created:** 2026-11-07

- [ ] **TD-244**: HTMLBubbles (interactive widgets in a message) — prototype once Bot API 10.4 ships them publicly
  - **Priority:** Low
  - **Source:** owner, 2026-11-06 (her research note on Telegram Serverless + HTMLBubbles; facts there are after my knowledge cutoff, re-check before starting)
  - **Created:** 2026-11-06
  - **Wait for:** the widget methods in the public Bot API changelog (10.4); python-telegram-bot will lag, so the first send may go through the raw Bot API.
  - **Candidates, in order:** (1) the week card as a live widget — day bars, tap a day for its meals; data baked in at send time, no request to the bot; the current PNG card stays as the fallback. (2) Portion choice in a draft — a horizontal scale or big S/M/L/150 г tiles instead of size chips Telegram cuts at ~12 chars; the choice goes back through the 64-byte `request()`. (3) ⭐ in the reminder as tiles — only after stage 6 shows how people answer.
  - **Rules:** nothing personal in `publicData`/`chatData` (it travels with a forward); every widget has a plain message or card fallback (old clients see a stub, the server may refuse the send).
  - **Not Telegram Serverless:** no scheduler (reminders, week card need the 15-min sweep), health data would live at Telegram with no described export.
- [ ] **TD-226**: With no weight, the screen says «считаю 100 г» while an immediate 🍽 Записать logs the portion guess
  - **Priority:** Low
  - **Source:** stage 5 smart portions review; left until someone notices it
  - **Created:** 2026-11-01
- [ ] **TD-227**: Meal cards print г for drinks, and a failed portion ask is retried only by `scripts/suggest_portions.py`, not by the bot
  - **Priority:** Low
  - **Source:** stage 5 smart portions
  - **Created:** 2026-11-01
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
  retention purge is currently the only deletion path. (3) ~~**Reprocess → meal**~~ — done in stage 5:
  `/reprocess` reads a meal still unread through the live path and fills it; a row whose meal stands,
  or with nothing to read, leaves the queue unpaid. An old row with no meal is still only re-analyzed
  onto its row: it may have been re-sent since, and a meal out of it could be a double.
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


- [ ] **TD-026**: 💸 cost line — OpenRouter's `usage.cost` is ASSUMED from its docs, never seen live
  (`usage_meter._reported_cost`). On the first real OpenRouter analysis with ⚙️ «💸 Стоимость
  запросов» on, check the line shows a sum, not «цена неизвестна»; if the field is absent, ask for
  usage accounting (`extra_body` `usage.include`) or price OpenRouter calls from the token counts.
  - **Priority:** Low
  - **Source:** feat/request-cost (released 2703865)
  - **Created:** 2026-10-29


- [ ] **TD-228**: alembic check is red on main: models and migrations disagree on nullable created_at/updated_at (meal_messages, meal_readings, meal_sources) and hand-written indexes on personal_foods and personal_food_embeddings. Removed from CI so it would not paint every branch red; the four tables added by feat/people show no drift. Reconcile, then add the check back.
  - **Priority:** Medium
  - **Source:** feat/people
  - **Created:** 2026-11-02

- [ ] **TD-229**: Custom emoji set is behind the code: marks 🍖 🥚 👤 👥 📤 💬 👁 🗒, writing and tap are drawn in render/emoji or listed for it, none uploaded. INFERRED from custom_emoji.py: stock emoji show until the set is rebuilt. Never seen in a real chat.
  - **Priority:** Low
  - **Source:** feat/people
  - **Created:** 2026-11-02

- [ ] **TD-230**: /grant_sub on an invited person can shorten their 1200-month subscription: create_subscription overwrites end_date.
  - **Priority:** Medium
  - **Source:** feat/people
  - **Created:** 2026-11-02

- [ ] **TD-231**: Old records and saved dishes with fatty fish keep stored groups without omega: no paid retag ran. Omega-3 days from fish appear only for meals read after feat/people.
  - **Priority:** Low
  - **Source:** feat/people
  - **Created:** 2026-11-02

- [ ] **TD-232**: If the model tags fatty fish with omega but not anchor, or a plate as one item (salad with salmon), week_card._fish_omega does not recognise it and it can inflate «🌱 Разные растения». The 12-dish real check did not hit it; plates like that were not in the set.
  - **Priority:** Low
  - **Source:** feat/people
  - **Created:** 2026-11-02

- [ ] **TD-233**: Ledger counts one call per SDK call: internal SDK retries the provider may bill are invisible. Not measured. app/api/v1/ai.py is unmounted but holds paid-call code: mount or remove.
  - **Priority:** Low
  - **Source:** feat/people
  - **Created:** 2026-11-02

- [ ] **TD-234**: feedback rows have no retention rule and each keeps the whole original message.
  - **Priority:** Low
  - **Source:** feat/people
  - **Created:** 2026-11-02

- [ ] **TD-235**: Inbox: a delivery still in flight can show as undelivered, so a re-send could send a second copy. /reprocess: a closed person's rows take slots in REPROCESS_BATCH_LIMIT before filtering.
  - **Priority:** Low
  - **Source:** feat/people
  - **Created:** 2026-11-02

- [ ] **TD-236**: Album tests other than test_the_read_waits_for_the_last_photo_not_the_first keep the 0.5 s wall-clock margin that failed on a busy CI runner.
  - **Priority:** Low
  - **Source:** feat/people
  - **Created:** 2026-11-02
- [ ] **TD-237**: An invited person's subscription now really ends (12 months by default). The end shows only on the person card: no mark in /users, and no note to the owner before it. The person now meets one calm line that the term ended and who can renew it.
  - **Priority:** Medium (first possible end is a year from the first such invite)
  - **Source:** fix/settings-and-clock-test
  - **Created:** 2026-11-02
- [ ] **TD-239**: Tap refusals always say the term ended, while message refusals tell a person who never had a term that apart. Reachable only in the open mode, with an old button.
  - **Priority:** Low
  - **Source:** fix/access-end-and-welcome
  - **Created:** 2026-11-02
- [ ] **TD-240**: TELEGRAM_ADMIN_USERNAME has no reader since the padlock screen went. Remove it with its default, once no env map sets it.
  - **Priority:** Low
  - **Source:** fix/access-end-and-welcome
  - **Created:** 2026-11-02
- [ ] **TD-241**: A closed person's tap is not answered by the gate, so the button spins until Telegram gives up. The line still arrives on the first tap.
  - **Priority:** Low
  - **Source:** fix/access-end-and-welcome
  - **Created:** 2026-11-02
- [ ] **TD-242**: Whether someone with the draft mode on before the first record gets the first-day hint and the zone line has no test of its own.
  - **Priority:** Low
  - **Source:** fix/access-end-and-welcome
  - **Created:** 2026-11-02
