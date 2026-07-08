---
schema_version: 3
plan_id: photo-product-lookup-round2
source_artifact:
  path: docs/photo-product-lookup-round2.md
  sha256: af2448c4bc762dfd84471833b34fc6ac54fc751acefed9f12944c46217406781
  type: feature-prd
created_at: '2026-07-08T14:16:30Z'
approved_at: '2026-07-08T18:13:59Z'
approved_by: julia
specialist_roster_source: ~/.claude/agents/specialist-*.md + <project>/.claude/agents/specialist-*.md
execution:
  status: done
  started_at: '2026-07-08T18:26:09Z'
  completed_at: '2026-07-08T19:01:57Z'
  current_batch: null
  task_list_id: photo-product-lookup-round2
items:
- id: A12
  title: 'Responses-API migration ADR (enables A9): decide split-client vs full migration; document analyze_and_log adapter + web_search failure-mode enumeration'
  specialist: architect
  priority: P1
  status: done
  depends_on: []
  estimated_effort: 1h
  confidence: 0.92
  consult_session_id: db7bb8e8-b021-4f63-acb6-30bd84273d24
  specialist_session_id: 4fc083b4-532e-4ebe-93f3-0bb00aa076e1
  retry_count: 0
  last_update:
    ts: '2026-07-08T18:30:23Z'
    executor: architect
    note: 'Split-client approach confirmed: web_search_nutrition() on Responses API, all existing calls unchanged. Documented: analyze_and_log adapter compatibility (plain-string return, kind=web_search); 7-entry failure-mode table with explicit exception classes (no blanket swallow); dynamic confidence_tier scoped to ResolutionResult (class attr is a hint only); search-signal choice (vision_result[foods], brand-extraction deferred with trigger condition); final pipeline order barcode_off->name_off->label_ocr->name_web->vision. TD-005 self-heal and ModelUnavailableError explicitly excluded from Responses API path. All A9 depends_on items resolved.'
  result:
    kind: file
    ref: docs/decisions/ADR-0002-responses-api-migration.md
    verification: 'ADR covers all 4 A12 requirements: (a) analyze_and_log adapter shape section 2, (b) failure-mode enumeration section 4, (c) dynamic confidence_tier section 6, (d) search-signal choice section 7. A9 dependency checklist complete.'
- id: A10
  title: 'LabelOCRStrategy (source_id=label_ocr, medium): new OpenAIService.extract_nutrition_label vision method returning numbers+basis; per-100g/per-serving/per-package scaling branch; basis-ambiguous -> None (fall through to vision); distinct badge; unit tests. Never cached.'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on: []
  estimated_effort: 3h
  confidence: 0.97
  consult_session_id: 1068bf91-343d-4d1d-865e-a21f60bdcd0b
  specialist_session_id: d340ee45-e1bc-46e2-ae6f-7b0af9c2fa87
  retry_count: 0
  last_update:
    ts: '2026-07-08T18:42:20Z'
    executor: backend-dev
    note: 'LabelOCRStrategy implemented: extract_nutrition_label (vision, chat.completions, max_tokens=256), per-100g/per-serving/per-package scaling branches, basis-ambiguous->None policy documented, 2-food gate mirrors A8, distinct badge 🏷 с этикетки (проверь), never cached. analyze_and_log records label_ocr rows. ImageSignals carries telegram_id/input_ref for logging attribution. _build_pipeline now barcode_off->name_off->label_ocr->vision (A9 slot reserved). +21 new unit tests; 3 existing pipeline order assertions updated; autouse mocks prevent real API calls in both test files. 293 green (up from 276). QA note for A13: assert final 5-strategy order once A9 lands; extend autouse mocks to cover Responses-API web_search.'
  result:
    kind: commit
    ref: c18f96d
    verification: 293 tests green via ./scripts/test.sh; secret-scan pre-commit hook passed
- id: A9
  title: 'NameWebSearchStrategy (source_id=name_web, medium/low): OpenAIService.web_search_nutrition via Responses API; identify product then prefer re-query OFF for structured numbers (pure web prose = lowest confidence); dynamic confidence_tier; cautious distinct badge; non-blocking failure modes -> None; unit tests. Never cached.'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on:
  - A12
  estimated_effort: 5h
  confidence: 0.97
  consult_session_id: 1068bf91-343d-4d1d-865e-a21f60bdcd0b
  specialist_session_id: 2af3f502-6d9f-4022-8a84-a59bcf21f97e
  retry_count: 0
  last_update:
    ts: '2026-07-08T18:56:57Z'
    executor: backend-dev
    note: 'A9 complete. Implemented: OpenAIService.web_search_nutrition (Responses API, split-client per ADR-0002); _parse_web_nutrition_response parse callback with JSON extraction from prose/markdown; NameWebSearchStrategy with dual outcome paths (medium=OFF re-query, low=web prose); _build_pipeline() now final 5-strategy order barcode_off->name_off->label_ocr->name_web->vision. telegram._source_badge has name_web medium/low cases. +24 unit tests (317 total); autouse guards in both test files prevent real API calls. QA note for A13: assert final 5-strategy pipeline order and extend autouse mocks to cover web_search_nutrition in any remaining photo tests.'
  result:
    kind: commit
    ref: 1cf0447
    verification: 317 tests green via ./scripts/test.sh; pre-commit secret-scan passed
- id: A13
  title: 'Pipeline regression gate: _build_pipeline() final-order assertion (barcode_off->name_off->label_ocr->name_web->vision); extend autouse mocks to cover extract_nutrition_label + Responses-API web_search so existing photo tests make no real calls; full suite green via ./scripts/test.sh'
  specialist: qa
  priority: P2
  status: done
  depends_on:
  - A10
  - A9
  estimated_effort: 0.5h
  confidence: 1.0
  consult_session_id: 94b255bf-78a9-4afe-85d4-723c9f7490bf
  specialist_session_id: af592a8d-670d-4df4-8fe0-bf48df6e4b0a
  retry_count: 0
  last_update:
    ts: '2026-07-08T19:01:01Z'
    executor: qa
    note: 'All A13 deliverables confirmed present: (1) test_build_pipeline_order() in test_product_lookup_service.py asserts [barcode_off, name_off, label_ocr, name_web, vision]; (2) default_no_label_ocr + default_no_web_search autouse mocks in test_product_lookup_service.py; (3) label_ocr_mock + web_search_mock autouse mocks in test_meal_handler.py. Gap found and closed: test_barcode_integration.py was missing guards for extract_nutrition_label and NameWebSearchStrategy.resolve — added _default_no_label_ocr and _default_no_web_search autouse fixtures. 317/317 green via ./scripts/test.sh.'
  result:
    kind: file
    ref: tests/test_barcode_integration.py
    verification: 317 tests green via ./scripts/test.sh; pipeline order assertion passes; all photo-path tests now have explicit guards for A10 label_ocr and A9 web_search strategies
budget:
  max_usd_per_item: 6.0
  max_usd_per_plan: 20.0
  consumed_usd: 7.6626
review_gate:
  why: []
  approve_action: /execute-plan /Users/julia/my-projects/nutricore.photo-product-lookup-round2-wt/docs/plans/photo-product-lookup-round2.execution.md --resume
  reject_action: /plan-fixes docs/photo-product-lookup-round2.md --revise /Users/julia/my-projects/nutricore.photo-product-lookup-round2-wt/docs/plans/photo-product-lookup-round2.execution.md
safe_to_replay_from: null
clarifying_questions:
- 'A8 PRECONDITION (gates the whole plan): A8''s NameOFFStrategy + the shared _scale_off_nutrition helper are NOT on this worktree — verified: product_lookup_service.py L274 has NameOFFStrategy() commented out (''A8 (round 2, deferred)'') and _scale_off_nutrition is absent — despite the doc stating A8 shipped (it lives on branch feat/product-lookup-a8-name-off). Will the round-2 execute branch fork-from/merge that A8 branch BEFORE A10/A9 are dispatched? A10 reuses _scale_off_nutrition and both A10 and A9 assume name_off is already in the pipeline. All three specialists (architect, backend-dev, qa) flagged this independently.'
- 'STRATEGY ORDER (doc Q1): confirm final pipeline order barcode_off -> name_off -> label_ocr -> name_web -> vision. All three specialists concur (on-pack OCR is ground truth, so label_ocr precedes web inference; web is highest-latency/lowest-reliability so it sits last-but-one). Adopted as default and as the A13 order-test target.'
- 'A9 WEB NUMBERS (doc Q2): may web_search supply the KBJU numbers directly (at lowest confidence + most cautious badge), or only IDENTIFY the product so we re-query OFF for the structured numbers? Default adopted: identify -> re-query OFF where possible; a pure web number gets the lowest medium/low confidence. The A12 ADR settles this; confirm the default.'
- 'LABEL OCR BASIS (doc Q3): when the nutrition table is legible but the serving basis is ambiguous (e.g. ''per serving'' with no gram weight), fall through to vision (specialist-recommended: never surface confidently-wrong numbers) OR best-effort per-serving scaling with an ''uncertain'' flag + lowered confidence? Default adopted: basis unparseable -> return None -> fall through to vision.'
- 'BADGE = telegram.py TOUCH (specialist-surfaced; contradicts the doc): the doc says ''no telegram.py change beyond the badge string'', but _source_badge today dispatches on confidence_tier only and returns one generic medium badge for all non-barcode medium sources. Distinct badges for label_ocr (the doc''s example: label-tag emoji + ''с этикетки (проверь)'') and name_web (globe emoji + ''нашли в сети (сверь)'') require new per-source cases in telegram.py (a one-liner each, folded into A10/A9). Confirm distinct badges are wanted, or both intentionally share the generic medium badge. NOTE: doc Q4 (migration blast-radius) is not a separate question — the architect recommends the split-client approach; it is decided in the A12 ADR.'
human_feedback:
- ts: '2026-07-08T18:13:41Z'
  by: julia
  text: 'Decisions on the 5 clarifying questions (2026-07-08), grounded in the ratified target diagram docs/diagrams/input-processing-flow.md. (1) A8 precondition RESOLVED: A8 merged to main (4e8f458); plan branch rebased onto it. (2) Strategy order ACCEPTED: barcode_off -> name_off -> label_ocr -> name_web -> vision. (3) A9 web numbers: identify-then-requery-OFF for structured numbers; pure web prose = lowest confidence + most cautious badge (default accepted). (4) Label-OCR ambiguous basis -> return None -> fall through to vision (default accepted). (5) Distinct per-source badges for label and web, accepting the one-line telegram.py touch per strategy. All PM defaults accepted; nothing conflicts with the ratified diagram. APPROVED TO HOLD -- do not execute yet.'
  applies_to: null
  status: addressed
  addressed_at: '2026-07-08T18:13:52Z'
---






























# Plan — photo-product-lookup-round2

## Source

[`docs/photo-product-lookup-round2.md`](docs/photo-product-lookup-round2.md) (sha256 `af2448c4bc76...`).

## Synthesis

Round-2 plans **only** the two deferred packaged-food strategies (**A10** label-OCR, **A9**
web-search) plus the **enabling ADR** they need. All work is purely additive on the existing
pluggable resolution pipeline (ADR-0001): each strategy is a new `ResolutionStrategy` class +
one line in `_build_pipeline()`, mirroring A8. IDs preserve the doc's A-series; the new
migration ADR is minted **A12** (A11 was the round-1 pipeline ADR).

Three specialists were consulted live (architect, backend-dev, qa — session ids recorded per
item). Irrelevant roles skipped: **frontend-dev / accessibility** (no web UI — Telegram bot),
**designer** (badge copy is already specified verbatim in the doc).

**Strongest cross-cutting signal — the A8 gap (all three specialists, independently, verified
by grep):** the doc asserts A8 shipped, but `NameOFFStrategy` is commented out in
`product_lookup_service.py` (L274) and `_scale_off_nutrition` is absent on this worktree. A8
is on the un-merged `feat/product-lookup-a8-name-off` branch. A10 reuses `_scale_off_nutrition`
and both new strategies assume `name_off` in the pipeline order — so the execute branch must
carry A8 first. This is the top clarifying question and effectively gates dispatch.

## Dependency graph

```
A12 (ADR, architect) ──▶ A9 (web-search, backend-dev)
A10 (label-OCR, backend-dev) ─┐
A9 ──────────────────────────┴▶ A13 (pipeline regression gate, qa)

External precondition (not an item — a branch/merge decision for Julia):
  feat/product-lookup-a8-name-off  ──▶  A10, A9   (see clarifying Q1)
```

A12 and A10 have no interdependency and can run in parallel. A9 is strictly after A12 (needs
the blast-radius + `analyze_and_log` adapter + failure-mode decisions in writing). A13 is the
final gate after both strategies land.

## Items

### A12 — Responses-API migration ADR *(architect, P1, ~1h, conf 0.85)*

The enabling decision that **blocks A9**. Output: `docs/decisions/ADR-000X-responses-api-migration.md`.
- **Blast radius:** architect **recommends the split-client** approach — keep every existing
  `OpenAIService` call (`analyze_food_entry`, `analyze_food_image`, `extract_barcode_from_image`,
  `extract_nutrition_label`) on `chat.completions`; add **only** `web_search_nutrition()` on the
  Responses API. Rationale: full migration's blast radius includes the TD-005 self-heal path,
  `OPENAI_MAX_RETRIES`, and 250+ green tests — risk asymmetry strongly favors the split.
- **Must document:** (a) the `analyze_and_log` adapter — have `web_search_nutrition` return a
  plain string (same shape as existing text methods) so `analyze_and_log(kind="web_search",
  parse=...)` works unchanged; (b) explicit **enumeration** of `web_search` failure modes
  (rate-limit / no-result / malformed citation / timeout) each degrading to `None` — not a
  blanket `except Exception` that swallows real bugs; (c) whether **dynamic `confidence_tier`**
  (set at resolve time) is permitted for strategies with branching outcomes (A9 needs it); (d)
  the A9 search-signal choice (use `vision_result["foods"]` as the query — architect's
  recommended default — vs a dedicated brand-extraction Phase-1 call, deferred).

### A10 — LabelOCRStrategy *(backend-dev, P1, ~3h, conf 0.75)*

`source_id = "label_ocr"`, medium tier. **No dependency on A12** (pure `chat.completions`).
- New `OpenAIService.extract_nutrition_label` vision method returning the table numbers **with
  their basis** (per-100g / per-serving / per-package). Reuses the existing base64
  `image_data_url` signal.
- **Scaling branch:** per-100g → existing `_scale_off_nutrition`; per-serving/per-package →
  scale by servings the vision portion implies. **Basis-ambiguous → return `None`** (fall
  through to vision) — see clarifying Q4; document as intentional policy so no future dev adds
  a silent "just use 100g" fallback.
- **Guards:** `≤2 foods` single-item gate (mirror A8; reuse A8's helper if present); if
  `vision_result is None` (Phase-1 vision failed) short-circuit to `None` immediately.
- Distinct badge (doc example: label-tag emoji), **never cached**. Badge is a small
  `telegram.py` touch — see clarifying Q5. Runs after `name_off`, before `name_web`/`vision`.
- Tests mirror the A8 pattern: hit per-100g/per-serving/per-package, ambiguous→None, multi-item
  gate→None, illegible→None, raise→None (non-blocking).

### A9 — NameWebSearchStrategy *(backend-dev, P1, ~5h, conf 0.65, depends_on A12)*

`source_id = "name_web"`, medium/low tier. **Blocked until A12 lands** — the mock target and
adapter shape depend on the ADR's blast-radius decision.
- New `OpenAIService.web_search_nutrition` via the Responses API `web_search` tool.
- **Prefer structured numbers:** identify the product, then re-query OFF for the КБЖУ where
  possible; a pure web-prose number gets the **lowest** confidence + most cautious badge (see
  clarifying Q3). Two internal outcome paths ⇒ **dynamic `confidence_tier`** (medium if
  re-queried OFF, low if prose-only).
- **Non-blocking:** every `web_search` failure mode → `None`. Cost/latency higher than other
  paths — sits last-but-one (before `vision`). Distinct cautious badge (doc example: globe
  emoji), **never cached**.
- Tests: hit (OFF re-query) / hit (prose, low conf) / no-result→None / rate-limit→None; mock
  the Responses-API client via the extended autouse guard (A13).

### A13 — Pipeline regression gate *(qa, P2, ~0.5h, conf 0.8, depends_on A10, A9)*

- `_build_pipeline()` **final-order assertion**: `[s.source_id for s in _build_pipeline()] ==
  [barcode_off, name_off, label_ocr, name_web, vision]` (no such direct test exists today).
- **Extend the autouse mocks** so pre-existing photo tests never make real calls to the new
  `extract_nutrition_label` (A10) or the Responses-API `web_search` (A9).
- Full suite green via **`./scripts/test.sh`** (cache-venv python — TD-001, **not** `poetry run`).
- Note: per-strategy order/unit tests belong to A10/A9; A13 is the consolidated cross-cutting gate.

## Out of scope (per source — not planned)

Shipped barcode/name-search/pipeline/caching/badge plumbing; third-party reverse-image search;
additional product DBs (USDA/regional); inbound persistence/retention (TD-009/TD-010).
