---
schema_version: 3
plan_id: personal-food-db
source_artifact:
  path: docs/personal-food-db.md
  sha256: 94734442c7f49ca93b37a9c71e0a6ebc01c2ccb27efdf3a445457c71e21ac26d
  type: feature-prd
created_at: '2026-07-08T23:23:06Z'
approved_at: '2026-07-10T13:54:29Z'
approved_by: julia
specialist_roster_source: ~/.claude/agents/specialist-*.md + <project>/.claude/agents/specialist-*.md
execution:
  status: partial
  started_at: '2026-07-10T14:42:51Z'
  completed_at: null
  current_batch: null
  task_list_id: personal-food-db
items:
- id: B0
  title: 'ADR-0003 vector-store decision: pgvector-vs-Qdrant, embeddings model/dims, personal_foods schema (incl alias-embedding strategy + mandatory user_id ANN filter), SavedFoodRAGStrategy contract (threshold->tier, text/image paths), and a Deploy-delta section (former B7: pgvector image swap + CREATE EXTENSION release note). OWNER DECISIONS 2026-07-09: (1) pgvector confirmed. (2) similarity threshold is a CONFIG PARAMETER (name it, e.g. SAVED_FOOD_SIM_THRESHOLD, with a sensible default), tuned experimentally, NOT a hardcoded constant; B2/B3 read it from config. (3) aliases embedded separately (each alias its own vector pointing to the same personal_food_id). (4) ALSO design an exact-barcode reuse short-circuit: personal_foods carries a barcode key, so a barcoded item already saved is served from the personal base (no OFF call) ahead of barcode_off; a not-yet-saved barcode still goes to barcode_off/OFF and is saved on confirm; fuzzy embedding RAG remains the text / no-barcode path. (5) a saved match is medium tier and ALWAYS shown as a draft at the confirm step (no silent auto-save).'
  specialist: architect
  priority: P1
  status: done
  depends_on: []
  estimated_effort: 2h
  confidence: 0.92
  consult_session_id: f87c01e6-7de2-4205-95aa-467ce973026d
  specialist_session_id: 51780299-2ed4-4ce6-b405-f3496de946bb
  retry_count: 0
  last_update:
    ts: '2026-07-10T14:48:42Z'
    executor: architect
    note: 'ADR-0003 written covering all five B0 contract sections: (1) pgvector confirmed over Qdrant; (2) text-embedding-3-small VECTOR(1536), OPENAI_EMBEDDING_MODEL/DIMS config params, ai_call_logs kind=embedding audit; (3) two-table schema personal_foods+personal_food_embeddings, alias-per-row strategy, UNIQUE dedup on (user_id,lower(canonical_name)), barcode column+index, HNSW ANN index with vector_cosine_ops, mandatory user_id join on all ANN queries; (4) SavedFoodRAGStrategy: position FIRST in pipeline (before barcode_off), two-phase resolve (exact-barcode Phase A then fuzzy ANN Phase B), SAVED_FOOD_SIM_THRESHOLD config default 0.15, source_id=saved_rag, confidence_tier=medium always, badge=from-saved-base, resolution_signals payload; (5) find_similar as mockable B6 seam; deploy delta: pgvector/pgvector:pg15 image swap manual step for openclaw-setup, no new required env vars. Pipeline order rationale: saved_rag before barcode_off per owner decision 4 (2026-07-09) superseding Q2 default. Full implementation contracts for B1/B2/B3/B4.'
  result:
    kind: file
    ref: docs/decisions/ADR-0003-vector-store-personal-food-db.md
    verification: ADR covers all five sections required by B0 item contract and encodes all five owner decisions (2026-07-09). Downstream implementation contracts for B1/B2/B3/B4 specify exactly what each must implement against this ADR.
- id: B1
  title: 'personal_foods domain + pgvector enablement: extension-enable migration, PersonalFood model/schema/CRUD (+ alias handling per ADR), VECTOR(N) embedding column + ANN index, created_at NOT NULL server_default (TD-006 lesson). High-effort/atomic migration; expose CRUDPersonalFood.find_similar(db, embedding, threshold, user_id) as the mockable ANN seam. OWNER 2026-07-09: also add a nullable barcode column + index on personal_foods (for the exact-barcode reuse short-circuit designed in B0), plus a by-barcode lookup on CRUDPersonalFood.'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on:
  - B0
  estimated_effort: 4h
  confidence: 0.97
  consult_session_id: 53bff0b9-a716-481d-b87c-fa047fea47a9
  specialist_session_id: 9da7f922-ec86-4cec-a537-899c1c6e5172
  retry_count: 0
  last_update:
    ts: '2026-07-10T15:01:35Z'
    executor: backend-dev
    note: 'B1 fully implemented: pgvector-dependent seam (find_similar) uses raw SQL <=> and returns None gracefully on SQLite — B6 should mock it. upsert() uses Python-side lower() for Unicode-correct dedup (SQLite lower() is ASCII-only). pgvector conditional import fallback keeps SQLite tests working when pgvector package is not installed. OWNER: poetry install needed to pull in pgvector>=0.3.0 (added to pyproject.toml). Migration a0b1c2d3e4f5 requires pgvector/pgvector:pg15 image swap before first run (documented in ADR-0003 §Deploy delta). B2 (embed_text on OpenAIService) and B3 (SavedFoodRAGStrategy) can now proceed in parallel.'
  result:
    kind: commit
    ref: f3770b9
    verification: 21 new unit tests pass (346 total, +21 vs 325 baseline); full suite green; no regressions
- id: B2
  title: 'embed_text() on OpenAIService (+ optional batch): reuses split-client + OPENAI_API_KEY (no new required env); records ai_call_logs kind=embedding; OPENAI_EMBEDDING_MODEL/OPENAI_EMBEDDING_DIMS in config with defaults; dimension must match ADR-pinned VECTOR(N). Swappable to a local model later'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on:
  - B0
  estimated_effort: 2h
  confidence: 0.98
  consult_session_id: 53bff0b9-a716-481d-b87c-fa047fea47a9
  specialist_session_id: 59ae756c-6d47-43c0-a8e5-8fa025122ae1
  retry_count: 0
  last_update:
    ts: '2026-07-10T15:08:01Z'
    executor: backend-dev
    note: embed_text() and embed_texts() added to OpenAIService. Uses client.embeddings.create (not _create/chat.completions) per ADR-0002 split-client pattern. Reads OPENAI_EMBEDDING_MODEL/OPENAI_EMBEDDING_DIMS from config (already present from B1). Records ai_call_logs kind=embedding for success and error paths; propagates exceptions so B3/B4 handle them in their own try/except. embed_texts() batches in one API call sorted by index. No new config params. B3 and B4 can proceed.
  result:
    kind: commit
    ref: b7678612ba01d641d2d2387cc7c448fcf136c034
    verification: 22/22 new tests in test_openai_service.py pass; full suite 355 green (+9 vs 346 baseline)
- id: B3
  title: 'SavedFoodRAGStrategy (source_id=saved_rag): embed query -> user-scoped ANN over personal_foods -> ResolutionResult; threshold->confidence_tier per ADR (threshold read from config, not hardcoded); text-query + image (vision foods) paths; non-blocking (miss/below-threshold->None); record matched personal_food_id+distance in signals; register in _build_pipeline() right after barcode_off; add saved_rag case to telegram _source_badge (former F-2, badge e.g. star / iz-vashey-bazy). OWNER 2026-07-09: when signals.barcode is present, do an EXACT-barcode lookup in personal_foods BEFORE the fuzzy embedding path (repeat barcoded item served from the base, no OFF); the fuzzy path is for text / no-barcode. The saved_rag result is always shown as a draft at the existing confirm step, never auto-written.'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on:
  - B0
  - B1
  - B2
  estimated_effort: 4h
  confidence: 0.97
  consult_session_id: 53bff0b9-a716-481d-b87c-fa047fea47a9
  specialist_session_id: 6c91bf8d-b79e-46d0-a3e9-81f42ce36327
  retry_count: 0
  last_update:
    ts: '2026-07-10T15:19:53Z'
    executor: backend-dev
    note: 'B3 fully implemented. SavedFoodRAGStrategy: Phase A exact-barcode short-circuit (get_by_barcode, no OFF call), Phase B fuzzy ANN (embed_text -> find_similar with SAVED_FOOD_SIM_THRESHOLD from config). Non-blocking wrapper: all exceptions → None (ADR-0003 §4f). Signals payload: saved_food_id/saved_food_name/saved_match_distance/saved_match_source/query_text (ADR-0003 §4e). Nutrition scaled per-100g to portion_grams (same pattern as _scale_off_nutrition). Macro imports at top level (no circular risk: crud/models don''t import from services). _build_pipeline reordered: saved_rag first (ADR-0003 §4a). _source_badge: ''⭐ из вашей базы'' for saved_rag (ADR-0003 §4b). 17 new unit tests: Phase A/B hit/miss, no-telegram_id, non-blocking, nutrition scaling, query-text helpers, badge. All 6 strategy_tried exact-equality assertions updated to prepend saved_rag. QA note for B6: integration coverage needed for real ANN threshold query (SQLite/pgvector gap per ADR-0003 §4d) — mock-based branch-logic tests are here; B6 should add the pipeline-order assertion update (already done here as prerequisite) and idempotency/learning-loop tests.'
  result:
    kind: commit
    ref: bc28313
    verification: 372 tests green (+17 vs 355 baseline); pre-commit secrets hook passed; no regressions in existing pipeline tests
- id: B4
  title: 'Learning-loop write-back (incl former F-3 confirm-handler glue): on confirm and TD-015 correction path, upsert confirmed food+alias into personal_foods with provenance (meal_id/resolution_source), embed canonical name at write time; idempotent dedup key = lowercased canonical name within user_id; no duplicate rows. OWNER 2026-07-09: do the embed+upsert as a Celery FIRE-AND-FORGET background task so the confirm reply is instant; the task must be retry-safe (idempotent on the dedup key). Also persist the barcode on the row when the confirmed item had one (feeds the B0/B1 exact-barcode reuse).'
  specialist: backend-dev
  priority: P1
  status: done
  depends_on:
  - B1
  - B2
  estimated_effort: 3h
  confidence: 0.97
  consult_session_id: 53bff0b9-a716-481d-b87c-fa047fea47a9
  specialist_session_id: 75ccca54-00cc-4a5b-b59c-6536d397ced3
  retry_count: 0
  last_update:
    ts: '2026-07-10T15:37:26Z'
    executor: backend-dev
    note: 'B4 fully implemented. embed_and_save_personal_food Celery task: upserts food (idempotent on lower(canonical_name)), embeds canonical name (skips re-embed if text_embedded already exists for this food). _schedule_personal_food_save in telegram.py: canonical_name priority saved_food_name>product_name>foods[0]; barcode from barcode_raw; per-100g reverse-scaled via portion_grams (signals or _parse_portion_grams fallback). Celery include registered. QA note for B6: add pipeline-level integration test for confirm->personal_food DB write round-trip and idempotency over multiple confirms.'
  result:
    kind: commit
    ref: 97413553a0545e2272e9735476efb06c54a8e141
    verification: 22 new unit tests in test_personal_food_task.py pass; full suite 394 green; secrets hook passed; no regressions
- id: B6
  title: 'Tests: SavedFoodRAGStrategy hit/miss/threshold by mocking CRUDPersonalFood.find_similar (SQLite has no pgvector operators - branch logic only); also cover the exact-barcode-before-fuzzy branch; update _build_pipeline order assertion to include saved_rag; learning-loop persistence + idempotency; embed_text mocked. Real ANN/threshold query stays a manual deploy-gate verification. OWNER 2026-07-09: the write-back is a Celery task (B4) - test the task function directly (eager) for persistence + idempotency; no real vector tests for now (owner-confirmed). ./scripts/test.sh green'
  specialist: qa
  priority: P2
  status: done
  depends_on:
  - B3
  - B4
  estimated_effort: 2h
  confidence: null
  consult_session_id: be13a2f2-211c-4cf0-8613-51426a42b095
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-07-11T01:22:00Z'
    executor: julia
    note: 'Work complete + committed (449762d: 4 on-scope tests — threshold-from-config, embed-query, confirm->DB Celery-eager roundtrip, idempotency). Full suite green (398 passed). The qa claude -p dropped ("Connection closed mid-response") AFTER committing, so the verdict was lost, not the work; evaluator substituted by human inspection of the diff + green suite. Closed manually 2026-07-11.'
  result: null
- id: B5
  title: 'Quick-pick from saved/recent (Telegram UX): recency/frequency buttons -> change quantity -> skip analysis pipeline (direct DB query, not RAG). DEFERRABLE - B1-B4 deliver the accuracy/cost win alone. Needs UX decision: ReplyKeyboard (consistent, low-risk) vs InlineKeyboard+CallbackQueryHandler (new pattern, +risk). Recommend confirming scope before dispatch (see clarifying Qs)'
  specialist: frontend-dev
  priority: P2
  status: blocked
  depends_on:
  - B1
  - B4
  estimated_effort: 3.5h
  confidence: null
  consult_session_id: b6806c75-031a-4164-acda-0462cfdc9678
  specialist_session_id: null
  retry_count: 0
  last_update:
    ts: '2026-07-09T07:51:17Z'
    executor: julia
    note: DEFERRED to a follow-up plan per owner decision 2026-07-09 (quick-pick UX; all specialists recommended deferring). Parked as blocked so it is never dispatched by /execute-plan; B1-B4 deliver the reuse win alone. Re-plan as its own small plan later (ReplyKeyboard-vs-Inline decision belongs there).
  result: null
budget:
  max_usd_per_item: 6.0
  max_usd_per_plan: 20.0
  consumed_usd: 11.4607
review_gate:
  why: []
  approve_action: /execute-plan /Users/julia/my-projects/nutricore.personal-food-db-wt/docs/plans/personal-food-db.execution.md --resume
  reject_action: /plan-fixes docs/personal-food-db.md --revise /Users/julia/my-projects/nutricore.personal-food-db-wt/docs/plans/personal-food-db.execution.md
safe_to_replay_from: null
clarifying_questions:
- 'Open Q1 — pgvector vs Qdrant: all four specialists and the author lean pgvector. DEFAULT ADOPTED: swap the DB image to pgvector/pgvector:pg15 + CREATE EXTENSION vector (one datastore, one backup surface, north-star-aligned, a SQL join with meals rather than a cross-store lookup). Confirm, or prefer standalone Qdrant?'
- 'Open Q2 — strategy position: does a saved-DB match beat a barcode hit? DEFAULT ADOPTED (architect + backend concur): barcode_off stays objective ground truth and wins; saved_rag sits immediately after it and ahead of name_off. Final order: barcode_off, saved_rag, name_off, label_ocr, name_web, vision. B6 pipeline-order assertion encodes exactly this — confirm before B3/B6 run.'
- 'Open Q4 — confidence tier of a saved match: architect strongly recommends medium (NOT high/auto-apply) under the current single confidence_tier — identity is high but portion may drift from the last log, so keep the badge for a correction chance. Auto-accept-no-badge is a TD-013 (three-score gate) follow-up. DEFAULT ADOPTED: medium + a distinct saved_rag badge. Confirm.'
- 'Similarity threshold value: B3 (threshold->tier mapping) and B6 (boundary test) both need a concrete distance cutoff (e.g. cosine <= 0.15 -> hit, else None). This is an ADR-0003 (B0) output — B0 MUST pin a specific number before B3/B6 execute. Flagged so the ADR does not omit it.'
- 'Open Q3 + B5 scope: architect, backend and frontend all recommend DEFERRING B5 (quick-pick UX) — it is blocked on B1+B4, and telegram.py has NO InlineKeyboard/CallbackQueryHandler precedent today (all ReplyKeyboardMarkup). B1-B4 deliver the accuracy/cost win alone. B5 is included here at P2 (lowest actual priority; the schema has no P3). DECISION NEEDED: keep B5 in this plan or split to a follow-up? If kept: ReplyKeyboard (consistent, low-risk, ~3.5h) or InlineKeyboard (richer, new pattern, ~5h/high-risk)?'
- 'B4 write-back latency: embed_text() on the user-facing confirm path adds ~150-300 ms synchronously. Celery already runs — fire-and-forget the embed as a background task, or block the reply for simpler idempotency? DEFAULT ADOPTED: synchronous upsert for correctness simplicity; revisit if latency is felt. Confirm or prefer Celery.'
- 'SQLite/pgvector test gap (qa flagged CRITICAL): the unit suite runs on SQLite, which has no pgvector <=> / <-> operators, so the acceptance line "similarity query returns the match above threshold and None below" CANNOT be a deterministic unit test. RESOLUTION ADOPTED: B3 exposes a mockable CRUDPersonalFood.find_similar(...); B6 mocks it and tests only strategy branch logic; the real ANN/threshold check becomes a manual post-deploy verification (already in the doc acceptance list). This slightly weakens that one automated criterion — confirm acceptable.'
- 'Alias-embedding strategy (architect; schema-locked, gates B1): embed only the canonical name (simpler, lower recall) vs embed each alias as its own vector pointing to the same personal_food_id (more recall, cheap). DEFAULT ADOPTED: embed aliases separately; canonical is the display name; the ANN result carries personal_food_id so dedup is trivial. B0/ADR must ratify before B1 locks VECTOR(N). Confirm the lean.'
---










































# Plan — personal-food-db

## Owner decisions (2026-07-09) — folded into the item contracts above

1. **Vector store:** pgvector (not Qdrant) — swap the DB image to `pgvector/pgvector:pg15` + `CREATE EXTENSION vector`.
2. **Pipeline order:** `barcode_off → saved_rag → name_off → …` confirmed. **Refinement:** `personal_foods`
   also carries a **barcode key** — a barcoded item already in the personal base is served from the base
   (no OFF call) *before* `barcode_off`; a not-yet-saved barcode still goes to OFF and is saved on confirm.
   Fuzzy embedding RAG stays the text / no-barcode path. (B0 designs it; B1 adds the barcode column; B3 does
   the exact-barcode check before the fuzzy path.)
3. **Saved-match confidence:** `medium` + **always shown as a draft at the confirm step** — no silent auto-save
   (full auto-accept waits for TD-013).
4. **Similarity threshold:** a **config parameter** (e.g. `SAVED_FOOD_SIM_THRESHOLD` + a sensible default),
   tuned experimentally — not a hardcoded constant.
5. **Write-back (B4):** **Celery** fire-and-forget background task so the confirm reply is instant; the task
   must be retry-safe (idempotent on the dedup key).
6. **Vector tests:** none for now — mock `find_similar`, test branch logic only; the real ANN/threshold check
   is a manual post-deploy verification.
7. **Aliases:** each alias is its own embedding vector pointing to the same `personal_food_id`.
8. **B5 quick-pick:** **deferred** to a follow-up plan (parked as `blocked` — not dispatched); B1–B4 deliver
   the reuse win alone.

## Source

[`docs/personal-food-db.md`](docs/personal-food-db.md) (sha256 `94734442c7f4...`).
Feature-PRD: Personal Food DB + RAG reuse (TD-014) with the enabling vector-store ADR (ADR-0003).

## Synthesis

The source doc self-decomposes into **B0–B7**; those IDs are preserved (minus B7, folded — see
below). Four specialists were consulted live (sessions recorded per item): **architect** (B0),
**backend-dev** (B1–B4), **qa** (B6), **frontend-dev** (B5). Skipped: **designer** (the saved_rag
badge copy is already specified in the doc) and **accessibility** (Telegram bot, no web UI).

**Cross-cutting signals resolved silently from evidence:**

1. **B7 folded into B0.** The pgvector image-swap + `CREATE EXTENSION` release note is ~5 lines
   that belong as a **§ Deploy delta** section of ADR-0003, not a separate architect dispatch
   (architect; backend/qa both rate B7 ~0 relevance). No standalone B7 item.
2. **F-2 (saved_rag badge) folded into B3; F-3 (confirm-handler write-back glue) folded into B4.**
   frontend-dev confirmed both are backend work (`_source_badge` one-liner; a `crud_personal_food`
   call in the existing confirm branch). Only the genuine quick-pick UX (B5) stays frontend-dev.
3. **Corrected dependency graph.** The doc states only "B0 blocks B1 and B3." Architect caught the
   missing edges: **B2 must land before B3 and B4** (the learning loop must `embed_text()` at write
   time; the strategy must embed the query). Final graph below.
4. **Mandatory `user_id` filter on the ANN query** (architect): a correctness issue *now*, not just
   future-proofing — dev/staging users would bleed cross-user without `WHERE user_id = …`. Baked
   into B0's contract and B1's `find_similar` seam.

## Dependency graph

```
B0 (ADR-0003, architect) ──┬─▶ B1 (personal_foods + pgvector, backend)
                           └─▶ B2 (embed_text, backend)

B0, B1, B2 ─▶ B3 (SavedFoodRAGStrategy, backend) ─┐
B1, B2 ─────▶ B4 (learning-loop write-back, backend) ─┴─▶ B6 (tests, qa)

B1, B4 ─▶ B5 (quick-pick UX, frontend — P3, deferrable)
```

B1 and B2 can run in parallel once B0 lands. B3 is the join of B0+B1+B2. B4 needs B1+B2. B6 is the
consolidated test/regression gate after B3+B4. B5 is convenience UX, blocked on B1+B4, and is the
main scope question for Julia (see clarifying Qs).

## Items

### B0 — ADR-0003 vector-store decision *(architect, P1, ~2h)*

The enabling decision — **blocks B1, B2, B3**. Must resolve, before any code: (1) pgvector vs
Qdrant (lean pgvector); (2) embeddings provider/model/**dimension** (lean OpenAI text-embedding
now, reusing `OPENAI_API_KEY`, dimension pinned — it is migration-locked into `VECTOR(N)`);
(3) `personal_foods` schema incl. the **alias-embedding strategy** (schema-locked, see Q) and
`created_at NOT NULL server_default` (TD-006); (4) the `SavedFoodRAGStrategy` contract incl. the
**similarity-threshold → confidence_tier** cutoff (a concrete number), the **mandatory `user_id`
ANN filter**, and the text/image query paths; (5) a **§ Deploy delta** (former B7) naming the
`pgvector/pgvector:pg15` swap + `CREATE EXTENSION vector` as the single required manual infra step
for openclaw-setup, with `no new required env` confirmed.

### B1 — personal_foods domain + pgvector enablement *(backend-dev, P1, ~4h, depends B0)*

Extension-enable migration, `PersonalFood` model/schema/CRUD (+ alias handling per ADR),
`VECTOR(N)` embedding column + ANN index. **High-effort and internally sequential** (migration →
model → CRUD) — do NOT split the migration. Exposes `CRUDPersonalFood.find_similar(db, embedding,
threshold, user_id)` as the **mockable ANN seam** so B6 can unit-test without real pgvector.
Infra note: the test/CI DB image may also need the pgvector image if any test hits a real vector op.

### B2 — embed_text() on OpenAIService *(backend-dev, P1, ~2h, depends B0)*

`embed_text(...)` (+ optional batch) on the split-client, reusing `OPENAI_API_KEY` (**no new
required env**); records `ai_call_logs kind="embedding"`; adds `OPENAI_EMBEDDING_MODEL` /
`OPENAI_EMBEDDING_DIMS` to config with defaults. Output dimension **must match** the ADR-pinned
`VECTOR(N)`. Interface swappable to a local model later (the ADR-0002 pattern).

### B3 — SavedFoodRAGStrategy *(backend-dev, P1, ~4h, depends B0, B1, B2)*

`source_id="saved_rag"`. Embed query → **user-scoped** ANN over `personal_foods` → `ResolutionResult`;
threshold→`confidence_tier` per ADR; text-query path (free text → embed → ANN) + image path (reuse
vision `foods` names → embed → ANN); **non-blocking** (miss/below-threshold → `None`); record matched
`personal_food_id` + distance in `signals`. Register in `_build_pipeline()` immediately after
`barcode_off`. Includes the **`_source_badge` `saved_rag` case** (former F-2, e.g. `⭐ из вашей базы`).

### B4 — Learning-loop write-back *(backend-dev, P1, ~3h, depends B1, B2)*

On **confirm** (and the TD-015 **correction** path) upsert the confirmed food + alias into
`personal_foods` with provenance (`meal_id` / `resolution_source`), **embedding the canonical name
at write time**. Idempotent: dedup key = lowercased/stripped canonical name within `user_id`; no
duplicate rows; re-embed only when the canonical changes. Includes the confirm-handler glue (former
F-3). **Open latency Q:** synchronous embed (default) vs Celery fire-and-forget (see clarifying Qs).

### B6 — Tests *(qa, P2, depends B3, B4)*

`SavedFoodRAGStrategy` hit/miss/threshold by **mocking `find_similar`** (SQLite has no pgvector
operators — branch logic only); **update `_build_pipeline` order assertion** to include `saved_rag`
(it will otherwise fail); learning-loop persistence + idempotency; `embed_text` mocked. The real
ANN/threshold query stays a **manual deploy-gate verification** (see the SQLite/pgvector clarifying
Q). `./scripts/test.sh` green (cache-venv runner — TD-001, not `poetry run`).

### B5 — Quick-pick from saved/recent *(frontend-dev, P2, ~3.5h, depends B1, B4) — DEFERRABLE*

A capture-side shortcut: recency/frequency buttons → change quantity → **skip analysis entirely**
(a direct DB query, not RAG — so it needs only B1+B4, not B2/B3). All three relevant specialists
recommend **deferring this to a follow-up** — B1–B4 deliver the accuracy/cost win on their own, and
Telegram has no InlineKeyboard/CallbackQueryHandler precedent today. Kept here at P3 pending Julia's
scope + UX-pattern decision (see clarifying Qs).

## Out of scope (per source — not planned here)

TD-013 three-score confidence gate (this plan keeps the single `confidence_tier`); round-2
`label_ocr` / `name_web` strategies (separate plan); north-star pieces (local VLM, USDA mirrors,
FHIR/medical export, my-health linkage). **Medical boundary unchanged** — a personal *food* DB is
the capture surface getting smarter about food; no medical logic/data enters the bot.
