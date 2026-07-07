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
  title: 'DB infra: product_cache table + meals resolution-source columns capturing the CHOSEN pipeline path + key signals (not just a flat source enum) so history/transparency + misprediction analysis are possible (model/schema/CRUD/migration)'
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
    executor: pm-orchestrator
    note: 'Revision 1 (human_feedback idx 0, product principle): the meals source recording must capture WHICH resolution path produced the numbers plus key signals (barcode/EAN read, matched product id, per-100g vs scaled grams, confidence tier), so a wrong turn is visible and mispredictions are analyzable later — coordinate the exact columns/shape with the A11 pipeline ADR.'
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
  title: 'Pipeline integration: implement barcode->OFF as the FIRST strategy in the pluggable resolution pipeline defined by A11''s ADR (not hard-wired into process_meal_input); record chosen path + signals on the meal and extend ai_call_logs for misprediction analysis; thread through process_meal_input/_run_meal_analysis + /reprocess (both live on main after TD-009 merge) and regression-test both; extract into product_lookup_service.py'
  specialist: backend-dev
  priority: P1
  status: pending
  depends_on:
  - A11
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
    executor: pm-orchestrator
    note: 'Revision 1. idx3: now depends on A11 (architect pipeline ADR) and implements barcode->OFF as the first pluggable strategy, so A8/A9/A10 drop in later. idx4: TD-009 (inbound persistence + /reprocess) is MERGED to main as of 2026-07-07 — the stale ''fix/td-004-food-image un-merged'' framing is dropped; _run_meal_analysis and /reprocess are on main and A4 must regression-test both the live flow and /reprocess. Not gated behind Stage 1. idx0: record chosen path + signals + extend ai_call_logs for transparency/misprediction logging.'
  result: null
- id: A5
  title: 'Telegram trigger UX + transparent reply: AUTO-trigger the lookup path when vision detects a barcode (no inline button / no /scan by default), and fall back to BUTTON-based disambiguation only when the pipeline is unsure which method fits (CQ1). Reply surfaces the resolution path + key intermediate values (EAN read, matched product, per-100g vs scaled grams, confidence tier) and lets the user correct any of them at the existing confirm step'
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
    executor: pm-orchestrator
    note: 'Revision 1. CQ1 answered (idx1): auto-trigger when confident (barcode detected), button-based chooser only for ambiguous photos — resolves the trigger question all 4 specialists raised. idx0 product principle: reply must expose the resolution path + intermediate signals and stay correctable at the existing confirm step (seamless-but-transparent). CQ1 is no longer open.'
  result: null
- id: A6
  title: 'Portion scaling: when OFF returns per-100g, AUTO-scale to the eaten portion by reusing the vision-estimated grams (CQ2) and explicitly SHOW the gram basis used in the reply; user can correct the grams at the existing confirm step. No new mandatory ''how many grams?'' prompt / no SCALING_PORTION state — seamless by default, transparent + correctable'
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
    executor: pm-orchestrator
    note: 'Revision 1. CQ2 answered (idx2): option (a) — auto-scale from vision grams, show the gram basis, correct at confirm; the SCALING_PORTION new-state option is dropped. Aligns with idx0 product principle (seamless best-guess, transparent + correctable). CQ2 no longer open.'
  result: null
- id: A7
  title: 'Test suite: barcode->OFF lookup (mocked, EAN fixtures), cache hit/miss, graceful fallback, chosen-path + signals recording (transparency/misprediction logging), auto-trigger detection + button-disambiguation fallback, gram-basis display/scaling, and opt-in gating keeping existing vision tests green'
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
    executor: pm-orchestrator
    note: 'Revision 1: scope extended to cover the transparency/misprediction-logging (idx0), auto-trigger + disambiguation (idx1), and gram-basis scaling (idx2) behaviors added this revision.'
  result: null
- id: A8
  title: 'DEFERRED (round 2): packaging-name -> OFF name-search path (approach 2, DB side; fuzzy match, no OpenAI change) — implemented as a DROP-IN strategy in the A11 pipeline framework'
  specialist: backend-dev
  priority: P2
  status: pending
  depends_on:
  - A11
  - A2
  estimated_effort: 1.5h
  confidence: null
  consult_session_id: 9f278393-43af-4913-8022-4c091222a603
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: pm-orchestrator
    note: 'Revision 1 (idx3): now depends on A11 and plugs into the pluggable pipeline as an additional strategy rather than a separate branch. Still deferred out of round 1.'
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
  status: pending
  depends_on:
  - A11
  - A1
  estimated_effort: 1h
  confidence: null
  consult_session_id: 9f278393-43af-4913-8022-4c091222a603
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: pm-orchestrator
    note: 'Revision 1 (idx3): now depends on A11 and plugs into the pluggable pipeline as an additional strategy. Still deferred out of round 1.'
  result: null
- id: A11
  title: 'Architect: design a pluggable meal-nutrition resolution pipeline (ordered strategies barcode->OFF, name-search, label-OCR, vision-fallback) as an ADR/design doc under docs/; barcode->OFF ships as the FIRST strategy so A8/A9/A10 are drop-in later; bakes in the seamless-but-transparent north-star (surface resolution path + key intermediate values, correctable at the existing confirm step, log mispredictions via ai_call_logs/meal source). A4 implements against this design.'
  specialist: architect
  priority: P1
  status: pending
  depends_on: []
  estimated_effort: 3h
  confidence: null
  consult_session_id: null
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: null
    executor: pm-orchestrator
    note: 'Added in revision 1 per Julia''s CQ3 answer (human_feedback idx 3): round 1 stays barcode->OFF only, but the architecture must be a pluggable resolution pipeline from the start rather than hard-wiring the barcode branch into process_meal_input. Architect proposes the design (ADR/design doc under docs/); exact shape open. Embeds the plan-wide product principle (idx 0).'
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
