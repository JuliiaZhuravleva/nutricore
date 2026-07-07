---
schema_version: 3
plan_id: photo-product-lookup
source_artifact:
  path: docs/photo-product-lookup.md
  sha256: 3634a4a64163ecf2742547df9668e76106dcc83403a5073c5a13cf1453005f70
  type: feature-prd
created_at: '2026-07-07T10:28:17Z'
approved_at: null
approved_by: null
specialist_roster_source: ~/.claude/agents/specialist-*.md + <project>/.claude/agents/specialist-*.md
execution:
  status: draft
  started_at: null
  completed_at: null
  current_batch: null
  task_list_id: photo-product-lookup
items:
- id: A1
  title: 'DB infra: product_cache table + meals.source column (model/schema/CRUD/migration)'
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on: []
  estimated_effort: 2h
  confidence: null
  consult_session_id: 9f278393-43af-4913-8022-4c091222a603
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: A2
  title: Open Food Facts HTTP client service (barcode lookup, normalize KBJU, User-Agent, cache read/write, graceful not-found)
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on:
  - A1
  estimated_effort: 3h
  confidence: null
  consult_session_id: 9f278393-43af-4913-8022-4c091222a603
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: A3
  title: Vision-based barcode/EAN extraction (OpenAIService.extract_barcode_from_image; vision-reads-digits, no pyzbar)
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on: []
  estimated_effort: 2h
  confidence: null
  consult_session_id: 9f278393-43af-4913-8022-4c091222a603
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: A4
  title: 'Pipeline integration: barcode->OFF path in process_meal_input/_run_meal_analysis, record source on meal, thread through /reprocess; extract into product_lookup_service.py (TD-008 spirit)'
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on:
  - A1
  - A2
  - A3
  estimated_effort: 5h
  confidence: null
  consult_session_id: 9f278393-43af-4913-8022-4c091222a603
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: A5
  title: Telegram trigger UX + source/confidence badge in reply (_nutrition_reply badge; trigger per CQ1 decision)
  specialist: frontend-dev
  priority: P1
  status: pending
  depends_on:
  - A4
  estimated_effort: 2h
  confidence: null
  consult_session_id: 67ac7086-e064-446d-bb42-7f0befc195b7
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: A6
  title: 'Portion scaling when OFF returns per-100g (approach per CQ2 decision: reuse vision portion / new SCALING_PORTION state / accept at confirm)'
  specialist: frontend-dev
  priority: P2
  status: pending
  depends_on:
  - A4
  estimated_effort: 1h
  confidence: null
  consult_session_id: 67ac7086-e064-446d-bb42-7f0befc195b7
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: A7
  title: 'Test suite: barcode->OFF lookup (mocked, EAN fixtures), cache hit/miss, graceful fallback, source recording, opt-in gating keeps existing vision tests green'
  specialist: qa
  priority: P1
  status: pending
  depends_on:
  - A4
  estimated_effort: 2.5h
  confidence: null
  consult_session_id: 7189de09-1b0b-4115-9364-9c8815e4f1c3
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: A8
  title: 'DEFERRED: packaging-name -> OFF name-search path (approach 2, DB side; fuzzy match, no OpenAI change)'
  specialist: backend-dev
  priority: P2
  status: pending
  depends_on:
  - A2
  estimated_effort: 1.5h
  confidence: null
  consult_session_id: 9f278393-43af-4913-8022-4c091222a603
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
- id: A9
  title: 'DEFERRED/BLOCKED: Responses API migration + web_search product ID (approach 2 web side; needs ADR + web_search trust policy)'
  specialist: backend-dev
  priority: P2
  status: blocked
  depends_on:
  - A2
  estimated_effort: 8h
  confidence: null
  consult_session_id: 9f278393-43af-4913-8022-4c091222a603
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-07-07T10:29:32Z'
    executor: pm-orchestrator
    note: 'Deferred out of first round per spec + unanimous specialist rec. Blocked pending: (1) an ADR for the chat.completions->Responses API migration (cost/latency structural change to OpenAIService), and (2) Julia''s web_search trust-policy decision (CQ open question: do we ever surface a web_search-sourced number, or only use it to identify the product then require OFF for the numbers?).'
  result: null
- id: A10
  title: 'DEFERRED: direct label OCR path (approach 3; vision reads nutrition table, no external call, source=label)'
  specialist: backend-dev
  priority: P2
  status: pending
  depends_on:
  - A1
  estimated_effort: 1h
  confidence: null
  consult_session_id: 9f278393-43af-4913-8022-4c091222a603
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: null
    note: null
  result: null
budget:
  max_usd_per_item: 2.0
  max_usd_per_plan: 20.0
  consumed_usd: 0.0
review_gate:
  why: []
  approve_action: /execute-plan /Users/julia/my-projects/nutricore.photo-product-lookup-wt/docs/plans/photo-product-lookup.execution.md --resume
  reject_action: /plan-fixes docs/photo-product-lookup.md --revise /Users/julia/my-projects/nutricore.photo-product-lookup-wt/docs/plans/photo-product-lookup.execution.md
safe_to_replay_from: null
clarifying_questions:
- 'CQ1 (trigger UX; raised by all 4 specialists; blocks A5). How should the product-lookup path be triggered: (a) inline button on the photo-confirmation message, (b) explicit /scan command, or (c) auto-detect when vision reports a barcode? PM + architect recommend (a) inline button — least disruptive to the existing ConversationHandler and keeps the vision estimate visible with an opt-in lookup. Confirm or override.'
- CQ2 (portion scaling; raised by backend/frontend/qa; blocks A6). Open Food Facts returns per-100g nutrition. When the lookup path is used, do we (a) silently reuse the vision-estimated portion grams and scale, (b) add a new SCALING_PORTION conversation step asking how many grams were eaten, or (c) show per-100g and let the user edit at the existing confirmation step? PM leans (a) for lowest friction on a personal tool.
- 'CQ3 (MVP scope / defer confirmation). Proposed round 1 = Approach 1 only (barcode -> Open Food Facts): items A1-A7. Deferred to a later round: A8 (packaging-name -> OFF name search), A9 (Responses API migration + web_search, blocked pending ADR), A10 (direct label OCR). Confirm this MVP boundary, or pull any deferred item into round 1.'
- 'CQ4 (sequencing / prerequisite). A4 edits _run_meal_analysis, which currently lives on the un-merged branch fix/td-004-food-image (TD-009: inbound persistence + /reprocess). Backend-dev flags A4 must regression-test both the live flow and /reprocess. Should TD-009 be merged before this plan executes? Also: architect notes this is a Stage 2/4 accuracy upgrade — confirm it is NOT gated behind Stage 1 (foundations) shipping first.'
- 'CQ5 (web_search trust policy; tied to blocked A9). If/when A9 is unblocked: do we ever surface a web_search-sourced number to the user, or only use web_search to identify the product and then require a structured Open Food Facts source for the actual numbers? This policy must be settled in the A9 ADR before that item can dispatch.'
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
