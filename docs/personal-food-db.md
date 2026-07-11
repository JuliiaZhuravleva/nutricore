# Personal Food DB + RAG reuse (TD-014) — and the enabling vector-store ADR (ADR-0003)

> **Scope:** the "quick-pick / reuse" half of the input-processing **Next** tier
> ([`diagrams/input-processing-flow.md`](diagrams/input-processing-flow.md)) — a **personal food
> database** the bot builds from confirmed meals, plus **RAG retrieval** over it so a repeat meal
> resolves instantly (and cheaply) instead of a fresh OpenAI call every time (input-processing
> **Gap ②**). Plus the **enabling architecture decision** — a **vector-store capability**, which
> the bot does not have today — captured as **ADR-0003**.
>
> **In scope:** ADR-0003 (vector-store decision), the `personal_foods` domain, an embeddings
> method, a `SavedFoodRAGStrategy` at the front of the pipeline, the learning-loop write-back, the
> quick-pick capture shortcut, tests, and the deploy delta.
>
> **Explicitly OUT — do not plan here:**
> - **TD-013 three-score confidence gate** (identity/portion/nutrition + clarification) — a
>   *separate, later* plan; it wants the personal-DB match as its strongest *identity* signal, so
>   it comes **after** this. This plan keeps the existing single `confidence_tier`.
> - **Round-2 `label_ocr` / `name_web`** strategies — independent, planned in
>   `plan/photo-product-lookup-round2`; they plug into the same pipeline and do not gate on this.
> - **North-star** pieces — local VLM, USDA mirrors, FHIR/medical export, my-health linkage. The
>   **medical boundary is unchanged**: a personal *food* DB is nutricore's capture surface getting
>   smarter about food; no medical logic/data enters the bot ([`product-philosophy.md`](product-philosophy.md)).

## Background (what already exists — read, don't rebuild)

Verified against `app/services/product_lookup_service.py` and the models tree (2026-07-09):

- **Pluggable resolution pipeline** (ADR-0001): `_extract_signals` → ordered `_build_pipeline()`
  of `ResolutionStrategy` classes, **first-non-None wins**. Current order:
  `barcode_off → name_off → label_ocr → name_web → vision`. Each strategy is
  `async resolve(signals, db) -> Optional[ResolutionResult]`, **non-blocking** (all
  network/parse/not-found → `None`, never raise), and sets `source`, `confidence_tier` (single
  string high/medium/low), scaled `nutrition`, `description`, `portion_grams`, and a transparency
  `signals` dict. The Telegram badge (`_source_badge`) is generic on `confidence_tier`.
- **`ResolutionResult` carries a single `confidence_tier: str`.** TD-013 will later split it into
  three scores; **this plan does not** — the new strategy uses the existing single tier.
- **Persistence today:** `meals.resolution_source` + `meals.resolution_signals` (JSON);
  `product_caches` (OFF product cache, keyed by barcode/name — **not** vector); `inbound_messages`
  (capture persistence, TD-009); `ai_call_logs` (audit, has a `kind=` field); `app_settings` (KV).
  Models present: `user, meal, body_metric, activity, analysis_report, product_cache,
  inbound_message, ai_call_log, app_setting, subscription, stats`.
- **OpenAI is behind `OpenAIService`** with a split-client pattern (ADR-0002): `chat.completions`
  for most calls, `responses` for web_search. `analyze_and_log(kind=…, parse=…)` wraps calls for
  the audit trail. `get_openai_service()` is the process-wide singleton.
- **NOT present today (verified — this is the genuinely new capability):** no vector column/index,
  no embeddings call in use, **no `pgvector`**, no saved/personal-food/favourites model. Postgres
  is the stock **`postgres:15`** image in both `docker-compose.yml` and `docker-compose.prod.yml`.

## The enabling decision — ADR-0003 (vector store) — blocks B1 & B3

An **architect ADR** (mirroring how ADR-0001/0002 preceded their implementations) must decide,
before any code:

1. **Where the vectors live — pgvector-in-Postgres vs standalone Qdrant.**
   - *pgvector* — one datastore, one migration, co-located with `meals`/`personal_foods` (a SQL
     join, not a cross-store lookup), and the choice the **north-star** research already names.
     Deploy delta: swap the DB image to `pgvector/pgvector:pg15` + `CREATE EXTENSION vector` (a
     **required infra change** for openclaw-setup — a manual step). Data stays on the existing host
     bind-mount, surviving the disposable Colima VM.
   - *Qdrant* — purpose-built ANN, familiar in the wider tooling, but a **second** service to
     run/back-up/monitor on the mini and a cross-store consistency problem (rows in Postgres,
     vectors in Qdrant).
   - **Author's lean: pgvector** (single owner, modest row counts, north-star-aligned, one backup
     surface). The ADR owns the final call + the tradeoff writeup.
2. **Embeddings — provider, model, dimensions, text-vs-image.** Lean: OpenAI text embeddings now
   (fits the split-client, reuses `OPENAI_API_KEY` — **no new required env**), model + dimension
   pinned in config; **text-only** first (embed the food name/description), image embeddings
   deferred. Must be swappable to a local model later (the same interface logic ADR-0002 used).
   Record embedding calls in `ai_call_logs` (`kind="embedding"`).
3. **`personal_foods` schema** — canonical food (name, brand, per-100g КБЖУ, provenance:
   originating `meal_id` / `resolution_source`), an **alias** notion (many surface forms → one
   canonical), the **embedding** column + ANN index, `user_id` (single-owner now, but scope it),
   `times_used` / `last_used_at`, timestamps with `created_at NOT NULL server_default` (the TD-006
   lesson). Retention / deletion policy (ties to the deferred `/forget`, TD-010).
4. **`SavedFoodRAGStrategy` contract** — pipeline position (see Open Q2), the similarity-threshold
   → `confidence_tier` mapping, the **text-query path** (free text → embed → ANN over
   `personal_foods`) vs the **image path** (reuse vision `foods` names → embed → ANN), how it fills
   `ResolutionResult` + `signals` (record matched `personal_food_id` + distance for misprediction
   analysis), and the non-blocking rule (miss / below-threshold → `None`).
5. **Deploy delta** — the pgvector image swap + extension + migration ordering (migrate-before-start
   already holds), and an explicit **release note** for openclaw-setup flagging the new *required*
   infra. Confirm **no new required env** (embeddings reuse the OpenAI key).

## What to build (proposed decomposition — pm/architect to refine)

- **B0 — ADR-0003** *(architect)* — resolve §1–§5 above. Blocks B1, B3.
- **B1 — `personal_foods` domain + pgvector enablement** *(backend-dev)* — extension-enable
  migration, `PersonalFood` model/schema/CRUD (+ alias handling), embedding column + ANN index.
- **B2 — embeddings method on `OpenAIService`** *(backend-dev)* — `embed_text(...)` (+ optional
  batch), `ai_call_logs kind="embedding"`, config for model/dimension.
- **B3 — `SavedFoodRAGStrategy`** *(backend-dev)* — implement the ADR-0003 contract; register in
  `_build_pipeline()`; new `source_id="saved_rag"` + a badge string (e.g. `⭐ из вашей базы`).
- **B4 — learning-loop write-back** *(backend-dev)* — on **confirm** (and on the TD-015
  *correction* path) upsert the confirmed food + alias into `personal_foods` with provenance, so
  the same food resolves instantly next time. Idempotent; no duplicate rows.
- **B5 — quick-pick from saved/recent** *(frontend-dev / telegram UX)* — a capture-side shortcut
  (buttons for recent/frequent foods → change quantity → skip analysis entirely). May split to a
  follow-up if the plan grows; **B1–B4 deliver the accuracy/cost win on their own.**
- **B6 — tests** *(qa)* — strategy unit tests (hit / miss / threshold), the pipeline-order
  assertion updated, learning-loop persistence + idempotency, embeddings method (mocked).
  `./scripts/test.sh` green.
- **B7 — deploy note** *(architect/backend)* — the openclaw-setup release note: pgvector image +
  `CREATE EXTENSION` as the manual step; migration runs via the existing migrate-before-start gate.

## Open questions (surface to Julia in the plan)

1. **pgvector vs Qdrant** — confirm the pgvector lean, or prefer the standalone store?
2. **Strategy position** — should a saved-DB match beat even a **barcode** hit? (A barcode is
   objective ground truth; a personal-DB match is a prior. Lean: `barcode_off` still wins on an
   exact barcode; `saved_rag` sits right after it and ahead of `name_off`.)
3. **Quick-pick UX (B5) in this plan, or a follow-up?** The reuse win (B1–B4) stands without it.
4. **Confidence of a saved match** — a high-similarity personal-DB hit is arguably *high* tier (the
   owner confirmed it before). May it be auto-applied like a barcode, or always `medium`
   (show + let correct)?

## Boundary & non-goals

- **Medical boundary unchanged** — personal *food* DB + food RAG = nutricore's capture surface
  ([`product-philosophy.md`](product-philosophy.md), [`stages` boundary rule](stages/README.md)).
  Medical reasoning/data stays in my-health; the north-star's FHIR/medical export is not here.
- **Not** the TD-013 confidence gate, **not** round-2 label/web, **not** multi-user product polish.

## Verification / acceptance

- `./scripts/test.sh` green (canonical cache-venv runner; allowlisted for specialists).
- Pipeline-order assertion updated and passing with `saved_rag` inserted.
- A confirmed meal creates/updates a `personal_foods` row (provenance recorded); logging the *same*
  food again resolves via `saved_rag` with **no vision/OFF call** (assert via `ai_call_logs` /
  `resolution_source`).
- pgvector extension + ANN index present after migration; a similarity query returns the saved
  match above threshold and `None` below it.
- The deploy note names the pgvector image swap as the single required manual infra step.
