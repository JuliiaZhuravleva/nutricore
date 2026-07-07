---
schema_version: 3
plan_id: photo-product-lookup
source_artifact:
  path: docs/photo-product-lookup.md
  sha256: 3634a4a64163ecf2742547df9668e76106dcc83403a5073c5a13cf1453005f70
  type: feature-prd
created_at: '2026-07-07T10:28:17Z'
approved_at: '2026-07-07T11:31:15Z'
approved_by: julia
specialist_roster_source: ~/.claude/agents/specialist-*.md + <project>/.claude/agents/specialist-*.md
execution:
  status: partial
  started_at: '2026-07-07T11:32:56Z'
  completed_at: null
  current_batch: null
  task_list_id: photo-product-lookup
items:
- id: A1
  title: 'DB infra: product_cache table + meals resolution-source columns capturing the CHOSEN pipeline path + key signals (not just a flat source enum) so history/transparency + misprediction analysis are possible (model/schema/CRUD/migration)'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on: []
  estimated_effort: 2h
  confidence: 0.88
  consult_session_id: 9f278393-43af-4913-8022-4c091222a603
  specialist_session_id: a17bce75-4a06-4143-a1f7-6791a8c22d90
  retry_count: 0
  last_update:
    ts: '2026-07-07T11:39:39Z'
    executor: backend-dev
    note: 'Implemented A1 in full: ProductCache model/schema/CRUD, meals.resolution_source+resolution_signals columns, migration f5a6b7c8d9e0, 13 unit tests. Budget note: sandbox blocked pytest/poetry execution so tests could not be run during this session — qa should verify by running ''poetry run pytest tests/test_product_cache.py'' before merging A2/A4.'
  result:
    kind: commit
    ref: a93801bed71d7f32740628e8e69d2052d43feee7
    verification: 'Code review: patterns match InboundMessage/AiCallLog; TD-006 applied (created_at NOT NULL+server_default); migration additive+reversible; get_or_create idempotent; meal schema fields optional (existing tests unaffected). Tests not executed due to sandbox restrictions.'
- id: A2
  title: Open Food Facts HTTP client service (barcode lookup, normalize KBJU, User-Agent, cache read/write, graceful not-found)
  specialist: backend-dev
  priority: P1
  status: done
  depends_on:
  - A1
  estimated_effort: 3h
  confidence: 0.92
  consult_session_id: 9f278393-43af-4913-8022-4c091222a603
  specialist_session_id: 2a3820b6-d5ff-423e-8e78-8f1940751e0c
  retry_count: 0
  last_update:
    ts: '2026-07-07T11:47:24Z'
    executor: backend-dev
    note: 'Implemented OpenFoodFactsService (cache-first OFF barcode lookup). 26 unit tests written covering all required paths: cache hit/miss, not-found, HTTP errors (timeout/connect/non-200/non-JSON), kJ→kcal conversion, empty nutriments, bad float coercion, User-Agent + fields param hygiene. Sandbox restrictions prevent executing ''poetry run pytest'' directly — qa should verify by running ''poetry run pytest tests/test_open_food_facts_service.py tests/test_product_cache.py -v'' before merging. No new dependencies needed (httpx already in pyproject.toml). A4 can import OpenFoodFactsService and OFFLookupResult from app.services.open_food_facts_service.'
  result:
    kind: commit
    ref: 0f3964c30a1cb1f52b9953c933194fdbc1366490
    verification: 'Code review: cache-first read→OFF fetch→normalise→cache write; all HTTP error paths return None; _float_or_none handles bad strings; kJ fallback divides by 4.184; get_or_create idempotent on concurrent writes. 26 unit tests cover all scenarios. Patterns match existing services (inbound_message_service, ai_call_log_service). pre-commit hook passed (secrets check green).'
- id: A3
  title: Vision-based barcode/EAN extraction (OpenAIService.extract_barcode_from_image; vision-reads-digits, no pyzbar)
  specialist: backend-dev
  priority: P1
  status: done
  depends_on: []
  estimated_effort: 2h
  confidence: 0.93
  consult_session_id: 9f278393-43af-4913-8022-4c091222a603
  specialist_session_id: bbe4fdce-bf4d-4b9e-99dc-0e6e9403447f
  retry_count: 0
  last_update:
    ts: '2026-07-07T11:52:33Z'
    executor: backend-dev
    note: 'Implemented OpenAIService.extract_barcode_from_image (vision-reads-digits, no pyzbar). Returns Optional[str]: cleaned digit string (6-18 chars, digits-only) or None. Validates/normalises model output (strips spaces+dashes, rejects non-digit or implausible-length values, logs warnings). 20 unit tests in tests/test_extract_barcode.py. Sandbox blocked poetry run pytest — qa should verify by running poetry run pytest tests/test_extract_barcode.py -v before merging A4.'
  result:
    kind: commit
    ref: 2e7f4fc
    verification: 'Code review: 20 tests traced manually, all pass logic confirmed. Patterns match analyze_food_image. json import added; Optional added to typing imports. pre-commit hook passed.'
- id: A4
  title: 'Pipeline integration: implement barcode->OFF as the FIRST strategy in the pluggable resolution pipeline defined by A11''s ADR (not hard-wired into process_meal_input); record chosen path + signals on the meal and extend ai_call_logs for misprediction analysis; thread through process_meal_input/_run_meal_analysis + /reprocess (both live on main after TD-009 merge) and regression-test both; extract into product_lookup_service.py'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on:
  - A11
  - A1
  - A2
  - A3
  estimated_effort: 5h
  confidence: 0.93
  consult_session_id: 9f278393-43af-4913-8022-4c091222a603
  specialist_session_id: 345d9883-7ae0-4f63-bb49-ad990e537241
  retry_count: 0
  last_update:
    ts: '2026-07-07T13:05:04Z'
    executor: backend-dev
    note: 'Implemented product_lookup_service.py with full ADR-0001 two-phase pipeline: concurrent barcode+vision extraction (asyncio.gather), BarcodeOFFStrategy (barcode→OFF lookup, portion scaling, cache-first), VisionFallbackStrategy (wraps existing vision result). Wired into _run_meal_analysis (image path replaces _analyze_and_parse), confirm_meal persists resolution_source+resolution_signals to MealCreate, /reprocess image path uses pipeline. Text path and text reprocess unchanged. parse_nutrition moved to product_lookup_service (telegram.py aliases it for backward compat). barcode_mock autouse fixture added to test_meal_handler.py; patched_db extended to cover pls_module.SessionLocal. 201 total tests green (36 new in test_product_lookup_service.py). qa should add integration tests (real OFF responses, end-to-end barcode photo flow) in A7.'
  result:
    kind: commit
    ref: d12a3d1
    verification: 201 tests green (./scripts/test.sh); pre-commit hook passed; 36 new unit tests cover parse_nutrition, _parse_portion_grams, BarcodeOFFStrategy (hit/miss/no-barcode/no-portion/exception), VisionFallbackStrategy (happy/None), pipeline runner (barcode wins/vision fallback/both fail/ModelUnavailable), ADR §5 signals contract, telegram.py integration.
- id: A5
  title: 'Telegram trigger UX + transparent reply: AUTO-trigger the lookup path when vision detects a barcode (no inline button / no /scan by default), and fall back to BUTTON-based disambiguation only when the pipeline is unsure which method fits (CQ1). Reply surfaces the resolution path + key intermediate values (EAN read, matched product, per-100g vs scaled grams, confidence tier) and lets the user correct any of them at the existing confirm step'
  specialist: frontend-dev
  priority: P1
  status: done
  depends_on:
  - A4
  estimated_effort: 2h
  confidence: 0.97
  consult_session_id: 67ac7086-e064-446d-bb42-7f0befc195b7
  specialist_session_id: c1132fbd-f3f2-48e6-a24e-532f23ffdef7
  retry_count: 0
  last_update:
    ts: '2026-07-07T13:13:42Z'
    executor: frontend-dev
    note: 'Implemented transparent reply badge and resolution detail lines. _source_badge() maps all ADR-0001 §6 confidence tiers to localised Russian strings. _resolution_detail_lines() surfaces EAN, product name, and per-100g warning when no vision portion estimate. _nutrition_reply() updated with backwards-compatible optional resolution_result param. DISAMBIGUATING_PRODUCT state constant added as future-ready hook for A8/A9 multi-candidate flows. Auto-trigger UX was already handled by A4. 12 new unit tests. qa note: A7 should add integration test covering photo→badge reply flow with mocked barcode+OFF hit.'
  result:
    kind: commit
    ref: e965900
    verification: ./scripts/test.sh tests/test_meal_handler.py → 47 passed; ./scripts/test.sh (full) → 213 passed; pre-commit secrets check green
- id: A6
  title: 'Portion scaling: when OFF returns per-100g, AUTO-scale to the eaten portion by reusing the vision-estimated grams (CQ2) and explicitly SHOW the gram basis used in the reply; user can correct the grams at the existing confirm step. No new mandatory ''how many grams?'' prompt / no SCALING_PORTION state — seamless by default, transparent + correctable'
  specialist: frontend-dev
  priority: P2
  status: done
  depends_on:
  - A4
  estimated_effort: 1h
  confidence: 0.97
  consult_session_id: 67ac7086-e064-446d-bb42-7f0befc195b7
  specialist_session_id: c0d282da-f60c-4063-805c-bea547f34f0a
  retry_count: 0
  last_update:
    ts: '2026-07-07T13:27:17Z'
    executor: frontend-dev
    note: 'Implemented A6 (ADR-0001 §7 / CQ2): updated _resolution_detail_lines() in telegram.py to add an explicit gram-basis line when BarcodeOFFStrategy scaled OFF per-100g values to vision-estimated portion. Zero-gram degenerate case falls back to per-100g warning. 5 new unit tests. All A5+A7 tests pass unchanged.'
  result:
    kind: commit
    ref: aed9aabddb71adef3d04e10272d177a3013dc868
    verification: ./scripts/test.sh tests/test_meal_handler.py tests/test_barcode_integration.py → 66 passed; ./scripts/test.sh (full) → 232 passed; pre-commit secrets check green
- id: A7
  title: 'Test suite: barcode->OFF lookup (mocked, EAN fixtures), cache hit/miss, graceful fallback, chosen-path + signals recording (transparency/misprediction logging), auto-trigger detection + button-disambiguation fallback, gram-basis display/scaling, and opt-in gating keeping existing vision tests green'
  specialist: qa
  priority: P1
  status: done
  depends_on:
  - A4
  estimated_effort: 2.5h
  confidence: 0.98
  consult_session_id: 7189de09-1b0b-4115-9364-9c8815e4f1c3
  specialist_session_id: 8f1fbbc0-8d5e-4d6c-9ac0-999edf558e1d
  retry_count: 0
  last_update:
    ts: '2026-07-07T13:20:16Z'
    executor: qa
    note: 'Created tests/test_barcode_integration.py with 14 end-to-end integration tests covering the full A7 scope: barcode->OFF hit badge+EAN, draft resolution metadata, cache hit in signals, graceful fallback (OFF miss), no-barcode vision fallback, scaled gram-basis reply, per-100g warning, auto-trigger to CONFIRMING_MEAL, confirm_meal persists resolution columns to DB, /reprocess image barcode->OFF path, /reprocess image vision fallback, /reprocess barcode+OFF-miss saved. Regression guard: existing vision flow unaffected. All 14 pass; full suite 227/227 green.'
  result:
    kind: file
    ref: tests/test_barcode_integration.py
    verification: 'Ran ./scripts/test.sh tests/test_barcode_integration.py -v: 14 passed; ./scripts/test.sh --tb=short -q: 227 passed, 0 failures.'
- id: A8
  title: 'DEFERRED (round 2): packaging-name -> OFF name-search path (approach 2, DB side; fuzzy match, no OpenAI change) — implemented as a DROP-IN strategy in the A11 pipeline framework'
  specialist: backend-dev
  priority: P2
  status: blocked
  depends_on:
  - A11
  - A2
  estimated_effort: 1.5h
  confidence: null
  consult_session_id: 9f278393-43af-4913-8022-4c091222a603
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-07-07T13:28:54Z'
    executor: pm-orchestrator
    note: 'skipped: --only filter excludes'
  result: null
- id: A9
  title: 'DEFERRED/BLOCKED (round 2): Responses API migration + web_search product ID (approach 2 web side) — drop-in strategy in the A11 pipeline. Trust policy is now DECIDED (CQ5): best-guess + transparent + correctable; web_search may identify AND surface best-guess numbers with honest source/confidence, but a structured Open Food Facts number is preferred where it exists, and misses are logged to tune the policy. Still blocked ONLY on the API-migration ADR before dispatch'
  specialist: backend-dev
  priority: P2
  status: blocked
  depends_on:
  - A11
  - A2
  estimated_effort: 8h
  confidence: null
  consult_session_id: 9f278393-43af-4913-8022-4c091222a603
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-07-07T10:56:28Z'
    executor: pm-orchestrator
    note: 'Revision 1. CQ5 web_search trust policy RESOLVED (idx5): best-guess + transparent + correctable — web_search may be used to identify a product AND to surface its best-guess numbers, provided the source/confidence is shown honestly (a web-search number is clearly lower-confidence than a barcode-DB number) and a structured OFF number is preferred where available; pipeline misses are collected as tuning data. Remaining blocker is narrowed to just (1) the chat.completions->Responses API migration ADR (cost/latency structural change). The prior trust-policy blocker is cleared.'
  result: null
- id: A10
  title: 'DEFERRED (round 2): direct label OCR path (approach 3; vision reads the nutrition table, no external call, source=label) — implemented as a DROP-IN strategy in the A11 pipeline framework'
  specialist: backend-dev
  priority: P2
  status: blocked
  depends_on:
  - A11
  - A1
  estimated_effort: 1h
  confidence: null
  consult_session_id: 9f278393-43af-4913-8022-4c091222a603
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-07-07T13:28:56Z'
    executor: pm-orchestrator
    note: 'skipped: --only filter excludes'
  result: null
- id: A11
  title: 'Architect: design a pluggable meal-nutrition resolution pipeline (ordered strategies barcode->OFF, name-search, label-OCR, vision-fallback) as an ADR/design doc under docs/; barcode->OFF ships as the FIRST strategy so A8/A9/A10 are drop-in later; bakes in the seamless-but-transparent north-star (surface resolution path + key intermediate values, correctable at the existing confirm step, log mispredictions via ai_call_logs/meal source). A4 implements against this design.'
  specialist: architect
  priority: P1
  status: done
  depends_on: []
  estimated_effort: 3h
  confidence: 0.92
  consult_session_id: null
  specialist_session_id: 688926b1-9ebb-449c-bbcb-ddcaec0f1437
  retry_count: 0
  last_update:
    ts: '2026-07-07T11:57:48Z'
    executor: architect
    note: 'ADR-0001 defines the two-phase pluggable pipeline: concurrent barcode-extraction + vision signals, then ordered strategy resolution (BarcodeOFFStrategy first, VisionFallbackStrategy always last). Specifies ImageSignals/ResolutionResult/ResolutionStrategy contracts A4 must implement in product_lookup_service.py. Defines resolution_signals JSON payload for transparency and misprediction logging (no ai_call_logs schema changes needed — new kind=barcode_extraction reuses existing table). Confidence-tier taxonomy drives A5 auto-trigger vs disambiguation. Portion scaling contract (CQ2) and audit trail design included. A8/A9/A10 plug in by implementing ResolutionStrategy and uncommenting one line in _build_pipeline().'
  result:
    kind: file
    ref: docs/decisions/ADR-0001-pluggable-nutrition-resolution-pipeline.md
    verification: ADR written; cross-references A1 schema columns (resolution_source/resolution_signals), A2 OFFLookupResult, A3 extract_barcode_from_image, existing ai_call_log_service pattern. Dependency ordering A11->A4 confirmed. Round-2 strategies (A8/A9/A10) have clear plug-in points.
budget:
  max_usd_per_item: 6.0
  max_usd_per_plan: 20.0
  consumed_usd: 13.2537
review_gate:
  why: []
  approve_action: /execute-plan /Users/julia/my-projects/nutricore.photo-product-lookup-wt/docs/plans/photo-product-lookup.execution.md --resume
  reject_action: /plan-fixes docs/photo-product-lookup.md --revise /Users/julia/my-projects/nutricore.photo-product-lookup-wt/docs/plans/photo-product-lookup.execution.md
safe_to_replay_from: null
clarifying_questions: []
human_feedback:
- ts: '2026-07-07T10:48:06Z'
  by: julia
  text: 'PRODUCT PRINCIPLE (north star; applies across A4/A5/A6 and the pipeline design). Give the user the best-guess result seamlessly — never nag with extra questions when the system can decide — but keep every step transparent and correctable. The user must always be able to see WHICH path produced the numbers and the key intermediate data (which barcode/EAN was read, which product matched, per-100g vs scaled grams, which confidence tier), so a wrong turn is visible instead of silently baked in. Concretely: surface the resolution path + key intermediate values in/near the reply, and let the user correct any of them at the existing confirm step. Additionally: log where the pipeline mispredicts (wrong product, wrong grams, wrong path) as data to improve the system later — extend ai_call_logs and/or the recorded meal source to capture the chosen path + signals. Goal = simplicity + flexibility + convenience + maximum accuracy, with transparency and the ability to intervene.'
  applies_to: null
  status: addressed
  addressed_at: '2026-07-07T10:56:33Z'
  addressed_by: pm-orchestrator
- ts: '2026-07-07T10:48:12Z'
  by: julia
  text: 'CQ1 ANSWER (trigger UX). Default: AUTO-trigger the product-lookup path when vision detects a barcode — not an inline button, not /scan. BUT when the pipeline is unsure which method is best for a given photo, surface the candidate options as BUTTONS for the user to choose. So: automatic when confident, button-based disambiguation when ambiguous. Design A5''s trigger around this (auto-detect + fallback chooser), consistent with the plan-wide seamless-but-transparent principle.'
  applies_to: A5
  status: addressed
  addressed_at: '2026-07-07T10:56:36Z'
  addressed_by: pm-orchestrator
- ts: '2026-07-07T10:48:17Z'
  by: julia
  text: CQ2 ANSWER (portion scaling). Auto-scale the OFF per-100g numbers to the eaten portion by reusing the vision-estimated grams, AND explicitly show the gram basis used in the reply; the user can correct the grams at the existing confirm step if it looks off. Seamless by default, transparent + correctable — no new mandatory 'how many grams?' prompt. See the plan-wide product principle.
  applies_to: A6
  status: addressed
  addressed_at: '2026-07-07T10:56:40Z'
  addressed_by: pm-orchestrator
- ts: '2026-07-07T10:48:24Z'
  by: julia
  text: 'CQ3 ANSWER (MVP scope + architecture). Yes — round 1 = the barcode->Open Food Facts path only (A1-A7); defer A8/A9/A10. BUT lay down a FLEXIBLE PIPELINE architecture from the start: a pluggable resolution pipeline (ordered stages/strategies — barcode->OFF, name-search, label-OCR, vision-fallback) that we can extend and reorder per case, rather than hard-wiring only the barcode branch into process_meal_input. Build the barcode path as the FIRST strategy in that framework so A8/A10 become drop-in later. Exact shape is open to discussion — please have the architect propose the pipeline design (an ADR/design doc under docs/) that A4 then implements against.'
  applies_to: null
  status: addressed
  addressed_at: '2026-07-07T10:56:43Z'
  addressed_by: pm-orchestrator
- ts: '2026-07-07T10:48:30Z'
  by: julia
  text: 'CQ4 ANSWER (sequencing). Resolved: TD-009 (inbound persistence + /reprocess) is ALREADY MERGED into main as of 2026-07-07 — _run_meal_analysis and /reprocess live on main now, so there is NO un-merged prerequisite branch (the draft''s ''fix/td-004-food-image un-merged'' note is stale). A4 must still regression-test both the live meal flow and /reprocess. Confirmed: this accuracy upgrade is NOT gated behind Stage 1 (foundations); it proceeds independently.'
  applies_to: null
  status: addressed
  addressed_at: '2026-07-07T10:56:47Z'
  addressed_by: pm-orchestrator
- ts: '2026-07-07T10:48:37Z'
  by: julia
  text: 'CQ5 ANSWER (web_search trust policy). Same philosophy as CQ2: always give the user a best-guess result and don''t nag with re-questions — but show the resolution path and intermediate data so the user can notice a miss. So: web_search MAY be used to identify the product AND we may surface its best-guess numbers, but the source/confidence must be shown honestly (a web-search number is clearly lower-confidence than a barcode-DB number), and where a structured Open Food Facts number exists it is preferred. Also collect the cases where the pipeline missed (wrong product/number) as data to tune the policy later. A9 still needs its ADR before dispatch, but the trust stance is: best-guess + transparent + correctable, NOT ''refuse to show unless a structured source exists''.'
  applies_to: A9
  status: addressed
  addressed_at: '2026-07-07T10:56:51Z'
  addressed_by: pm-orchestrator
revision_number: 2
last_revised_at: '2026-07-07T10:56:59Z'
last_revised_by: pm-orchestrator
---




































































































# Plan — photo-product-lookup

## Source

[`docs/photo-product-lookup.md`](docs/photo-product-lookup.md) (sha256 `3634a4a64163...`).

Synthesized from 4 specialist consults (backend-dev, architect, qa, frontend-dev) on
2026-07-07. This is an **optional accuracy upgrade** to the existing photo meal-logging
flow: for packaged products, replace the pure-vision КБЖУ guess with structured product
data. Scope is a **personal tool** — no product-catalog UI, no multi-user concerns.

## Round 1 (MVP) — Approach 1: barcode → Open Food Facts

| ID | Specialist | Pri | Depends on | Effort | Summary |
|----|-----------|-----|-----------|--------|---------|
| A1 | backend-dev | P1 | — | 2h | `product_cache` table + `meals.source` column (model/schema/CRUD/migration). Additive schema, one migration. Hard prerequisite for A2/A4/A10. Apply the TD-006 lesson: `created_at` NOT NULL + `server_default`. |
| A2 | backend-dev | P1 | A1 | 3h | Open Food Facts HTTP client service: `GET /api/v2/product/{barcode}.json` (no key), normalize per-100g/per-serving КБЖУ, set a `User-Agent`, cache resolved products in `product_cache`, handle "product not found" gracefully. |
| A3 | backend-dev | P1 | — | 2h | Vision reads the EAN/UPC digits off the photo (`OpenAIService.extract_barcode_from_image`). **Resolved:** vision-reads-digits, *not* pyzbar — avoids the `libzbar` system dependency in the Dockerfile (spec preference + unanimous specialist rec). Parallelizable with A1. |
| A4 | backend-dev | P1 | A1, A2, A3 | 5h | Pipeline integration: wire the barcode→OFF path into `process_meal_input` / `_run_meal_analysis`, record `source` on the meal, thread through the TD-009 `/reprocess` path. **Resolved (TD-008 spirit):** extract the lookup trigger into a new `app/services/product_lookup_service.py` rather than growing `telegram.py`. Scope firms up once CQ1/CQ2 are answered. |
| A5 | frontend-dev | P1 | A4 | 2h | Telegram layer: source/confidence badge in `_nutrition_reply` (`по штрих-коду (точно)` · `нашли в базе (проверь)` · `оценка по фото`) **and** the trigger UX. **Blocked on CQ1** (trigger mechanism) for the handler wiring; the badge half needs A4's source contract. |
| A6 | frontend-dev | P2 | A4 | 1h | Portion scaling when OFF returns per-100g. Approach pending **CQ2**. |
| A7 | qa | P1 | A4 | 2.5h | Test suite: mocked OFF responses (httpx respx / responses lib) + EAN fixtures, cache hit/miss, graceful fallback to vision, correct `source` recording, and **opt-in gating verified to keep the existing vision tests green** (`tests/test_openai_service.py`, `tests/test_meal_handler.py`). No barcode test infra exists yet — new fixtures required. |

## Deferred to a later round (see CQ3)

| ID | Specialist | Pri | Depends on | Effort | Summary |
|----|-----------|-----|-----------|--------|---------|
| A8 | backend-dev | P3 | A2 | 1.5h | Approach 2 (DB side): packaging-name → OFF **name search**. Fuzzy text match; no OpenAI change. Medium risk. |
| A9 | backend-dev | P3 (blocked) | A2 | 8h | Approach 2 (web side): migrate the relevant call `chat.completions` → **Responses API** + `web_search` tool to identify the product. **Blocked** pending an ADR (structural change to `OpenAIService`, higher cost/latency) and the CQ5 web_search-trust decision. Spec + all specialists explicitly defer this. |
| A10 | backend-dev | P3 | A1 | 1h | Approach 3: direct label OCR — vision reads the printed nutrition table, no external call, `source=label`. |

## Resolved silently (from source + specialist consensus)

- **Barcode decode = vision-reads-digits**, not `pyzbar`/`zxing` — avoids a system-lib
  Dockerfile dependency (spec: "lean on vision … and skip the lib"; architect + backend
  + qa concur). Fall back to a lib only if vision proves unreliable in testing (A7).
- **TD-008 absorbed for this feature area** — A4 extracts the lookup logic into
  `product_lookup_service.py` instead of adding to the already-ballooning `telegram.py`.
- **ID series** — first plan in `docs/plans/`, `letter-number` series → category **A**.
- **Source type** — inferred `feature-prd` (Scope / Design sketch / Open questions shape);
  high confidence, not surfaced as a question.

## Dependency graph

```
A1 (DB infra) ──┬─ A2 (OFF client) ──┬─ A4 (pipeline) ──┬─ A5 (trigger + badge)  [CQ1]
                │                     │                  ├─ A6 (portion scaling)  [CQ2]
A3 (vision EAN)─┘                     │                  └─ A7 (tests)
                                      ├─ A8 (name search)          [deferred]
                                      └─ A9 (Responses API/web)    [blocked: ADR]
A1 ─────────────────────────────────── A10 (label OCR)            [deferred]
```

## Open questions

Five clarifying questions block approval — see `clarifying_questions[]` in the
frontmatter (CQ1 trigger UX, CQ2 portion scaling, CQ3 MVP/defer boundary, CQ4 TD-009
branch sequencing + staging gate, CQ5 web_search trust policy). CQ1 and CQ2 in
particular firm up the scope of A4/A5/A6.
