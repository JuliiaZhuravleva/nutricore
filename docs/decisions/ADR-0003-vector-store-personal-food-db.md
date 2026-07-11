# ADR-0003 — Vector-Store Decision: pgvector + Personal Food DB + RAG Strategy

**Status:** Accepted  
**Date:** 2026-07-10  
**Author:** specialist-architect (personal-food-db B0)  
**Implements:** [docs/personal-food-db.md](../personal-food-db.md), item B0 (enables B1–B4)  
**Consumed by:** B1 (personal_foods domain), B2 (embed_text), B3 (SavedFoodRAGStrategy), B4 (learning-loop write-back), B6 (tests)  
**Cross-reference:** [ADR-0001](ADR-0001-pluggable-nutrition-resolution-pipeline.md) — pipeline contract; [ADR-0002](ADR-0002-responses-api-migration.md) — split-client pattern

---

## Context

Nutricore currently has no persistent per-user food memory. Every recognised meal goes through
the ADR-0001 pipeline fresh — a photo → barcode/OFF/vision chain — paying OpenAI and OFF call
costs on every repeated log of the same food. The goal is a **personal food DB** the bot builds
from confirmed meals so a repeat food resolves instantly (no NEW vision/OFF call, lower cost,
higher accuracy for the owner's habitual diet).

This requires **two genuinely new capabilities** that did not exist before:

1. A **vector store** for semantic (embedding-based) similarity lookup over saved food names.
2. An **embedding call** on `OpenAIService` (no embedding code exists anywhere today).

All five decisions below are **schema-locked** — they gate B1's migration and cannot be changed
without a new migration. They must be ratified before any code is written.

---

## Decisions

### §1 — Vector store: pgvector-in-Postgres (not standalone Qdrant)

**Decision:** extend the existing `postgres:15` database with the `pgvector` extension.

| Factor | pgvector | Qdrant |
|---|---|---|
| Infra footprint | +1 Postgres extension (existing data volume, existing backup) | +1 new service (second backup surface, cross-store consistency risk) |
| Join with `meals` | direct SQL join (`meals.user_id = personal_foods.user_id`) | requires a cross-store lookup (vector ID ↔ Postgres row) |
| Row counts | personal tool; expected <1 000 rows per user lifetime | purpose-built ANN shines at millions of rows |
| North-star alignment | named explicitly in the north-star research | not named |
| Operational risk | one DB to operate/restore on the Mac mini | two services, two backups, two potential split-brain states |

For a single-owner personal tool with modest row counts and an existing Postgres deployment the
pgvector trade-off is unambiguously correct. Qdrant's ANN performance advantage does not
materialise below tens of thousands of rows.

**Deploy delta (former B7):** openclaw-setup must perform one manual step before the first run
of the new migrations:

```yaml
# docker-compose.prod.yml  (and docker-compose.yml for local dev)
services:
  db:
    image: pgvector/pgvector:pg15   # was: postgres:15
```

The `pgvector/pgvector:pg15` image is a drop-in replacement for `postgres:15` — same data
directory format, same init scripts, same bind-mount survives the swap. No data migration needed.
After the image swap the first migration creates the extension:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

**No new required environment variable.** The POSTGRES_* connection settings are unchanged.

---

### §2 — Embeddings: OpenAI text-embedding-3-small, VECTOR(1536)

**Decision:**

| Parameter | Value | Config key |
|---|---|---|
| Provider | OpenAI Embeddings API | reuses `OPENAI_API_KEY` |
| Model | `text-embedding-3-small` | `OPENAI_EMBEDDING_MODEL` (default: `text-embedding-3-small`) |
| Output dimension | 1536 | `OPENAI_EMBEDDING_DIMS` (default: `1536`) |
| Column type | `VECTOR(1536)` | migration-locked (see §3) |
| Index op-class | `vector_cosine_ops` | cosine distance (0 = identical, 2 = opposite) |
| Audit trail | `ai_call_logs kind="embedding"` | ADR-0001 §8 pattern |

**Rationale for `text-embedding-3-small`:**

- The `text-embedding-3-small` model (1536 dims at default) outperforms the legacy
  `text-embedding-ada-002` on retrieval benchmarks at lower cost (~5× cheaper per token).
- 1536 dimensions gives good recall for short food-name text (typically 1–8 tokens); the
  dimension is migration-locked so over-provisioning is safer than under-provisioning.
- `text-embedding-3-small` supports custom dimension reduction (OpenAI `dimensions` param) — if
  storage becomes a concern, a future ADR can shrink the column, but that requires a data
  migration. Pin 1536 now.

**Interface swappability (ADR-0002 pattern):** `OPENAI_EMBEDDING_MODEL` and
`OPENAI_EMBEDDING_DIMS` are config params. If a local embedding model (e.g. `ollama`) is adopted
later, `embed_text()` can delegate to it without changing `personal_food_embeddings` or any query
code — as long as the output dimension matches the migration-locked `VECTOR(N)`.

> ⚠️  **Migration lock:** `OPENAI_EMBEDDING_DIMS` default `1536` determines `VECTOR(1536)` in
> the migration. If the default is changed after the migration runs, existing rows become
> incompatible. Do NOT change the default after first deploy without a full re-embed + `ALTER
> COLUMN` migration.

**No new required env variable.** `OPENAI_EMBEDDING_MODEL` and `OPENAI_EMBEDDING_DIMS` are
optional with safe defaults; `OPENAI_API_KEY` already exists.

---

### §3 — `personal_foods` schema and alias-embedding strategy

The personal food DB uses **two tables**: a canonical food record (`personal_foods`) and a
separate embedding table (`personal_food_embeddings`) holding one row per embedded text (canonical
name + each alias), all pointing back to the same `personal_food_id`.

#### `personal_foods` table

```sql
CREATE TABLE personal_foods (
    id               BIGSERIAL       PRIMARY KEY,
    user_id          BIGINT          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    canonical_name   TEXT            NOT NULL,          -- display name shown at confirm step
    brand            TEXT,                              -- nullable
    per_100g_calories NUMERIC(8,2),
    per_100g_proteins NUMERIC(8,2),
    per_100g_fats     NUMERIC(8,2),
    per_100g_carbs    NUMERIC(8,2),
    meal_id          BIGINT          REFERENCES meals(id) ON DELETE SET NULL,  -- provenance
    resolution_source TEXT,                             -- e.g. "barcode_off", "vision"
    barcode          TEXT,                              -- nullable; for exact-barcode short-circuit (§4c)
    times_used       INTEGER         NOT NULL DEFAULT 1,
    last_used_at     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ     NOT NULL DEFAULT NOW(),  -- NOT NULL + server_default (TD-006)
    updated_at       TIMESTAMPTZ     NOT NULL DEFAULT NOW()
);

-- Dedup key: one canonical entry per (user, lowercased canonical name)
CREATE UNIQUE INDEX personal_foods_user_name_uq
    ON personal_foods (user_id, lower(canonical_name));

-- Fast barcode lookup (exact short-circuit)
CREATE INDEX personal_foods_user_barcode_idx
    ON personal_foods (user_id, barcode)
    WHERE barcode IS NOT NULL;
```

#### `personal_food_embeddings` table

```sql
CREATE TABLE personal_food_embeddings (
    id               BIGSERIAL   PRIMARY KEY,
    personal_food_id BIGINT      NOT NULL REFERENCES personal_foods(id) ON DELETE CASCADE,
    text_embedded    TEXT        NOT NULL,   -- the exact string that was embedded (for debugging)
    embedding        VECTOR(1536) NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ANN index: cosine distance (HNSW for forward compatibility with larger datasets)
CREATE INDEX personal_food_embeddings_hnsw_idx
    ON personal_food_embeddings
    USING hnsw (embedding vector_cosine_ops);
```

#### Alias-embedding strategy

**Decision:** each alias is embedded as its own vector row in `personal_food_embeddings`,
all pointing to the same `personal_food_id`. The canonical name is also embedded as one of
these rows (it is not special at query time; it is special only for display).

```
personal_foods row: id=42, canonical_name="Греческий йогурт FAGE 2%"
personal_food_embeddings rows:
  (personal_food_id=42, text_embedded="Греческий йогурт FAGE 2%",  embedding=<1536 floats>)
  (personal_food_id=42, text_embedded="йогурт фаге",               embedding=<1536 floats>)
  (personal_food_id=42, text_embedded="Greek yogurt",              embedding=<1536 floats>)
```

At ANN query time the join returns `personal_food_id` (not `embedding.id`), so dedup is trivial:
the closest `personal_food_id` wins, regardless of which alias row was the nearest neighbour.

**Rationale:** embedding aliases separately maximises recall at near-zero extra cost (food names
are short; embedding is cheap). The alternative (embed only the canonical name) misses
alternate surface forms (e.g. "kefir" vs "кефир", brand abbreviations, OCR noise). The
`personal_food_id` foreign key handles dedup with no extra application logic.

#### ANN query contract (mandatory user_id filter)

Every ANN query over `personal_food_embeddings` **must** join with `personal_foods` and filter
by `user_id`. Omitting this filter would cross-contaminate personal data between users (a
correctness bug now, even with one owner — it would silently fail on a fresh multi-user deploy).

```sql
SELECT
    pfe.personal_food_id,
    pfe.embedding <=> $1 AS cosine_distance
FROM personal_food_embeddings pfe
JOIN personal_foods pf ON pf.id = pfe.personal_food_id
WHERE pf.user_id = $2
ORDER BY cosine_distance
LIMIT 1;
```

This query is the body of `CRUDPersonalFood.find_similar(db, embedding, threshold, user_id)` —
the **mockable ANN seam** B6 tests against (see §4d).

---

### §4 — `SavedFoodRAGStrategy` contract

#### §4a — Pipeline position

**Decision:** `SavedFoodRAGStrategy` is **first** in `_build_pipeline()`, before `BarcodeOFFStrategy`.

```python
def _build_pipeline(db: Session) -> list[ResolutionStrategy]:
    return [
        SavedFoodRAGStrategy(),    # B3 — personal base (barcode short-circuit + fuzzy ANN)
        BarcodeOFFStrategy(),      # high — A4 (round 1)
        NameOFFStrategy(),         # medium — A8 (round 2)
        LabelOCRStrategy(),        # medium — A10 (round 2)
        NameWebSearchStrategy(),   # medium/low — A9 (round 2)
        VisionFallbackStrategy(),  # low — always last
    ]
```

**Rationale:** the owner decision (2026-07-09) requires that a barcoded item already in
`personal_foods` is served from the personal base *without* making an OFF call. This is only
achievable if `SavedFoodRAGStrategy` runs **before** `BarcodeOFFStrategy`. The strategy
short-circuits internally (§4c), so a barcoded item not yet in `personal_foods` passes through
to `BarcodeOFFStrategy` unchanged.

A footnote on Q2 (strategy ordering): the original Q2 default placed `saved_rag` after
`barcode_off`. That was set before the exact-barcode short-circuit decision (owner decision 4).
The short-circuit supersedes it: the overall intent — "a fresh OFF lookup is ground truth for
new items" — is preserved (new barcodes still go to `barcode_off`), while the optimisation
"skip OFF for already-saved barcoded items" is also satisfied.

#### §4b — `source_id` and `confidence_tier`

| Attribute | Value |
|---|---|
| `source_id` | `"saved_rag"` |
| Nominal `confidence_tier` (class attribute) | `"medium"` |
| Result `confidence_tier` (always) | `"medium"` |
| Telegram badge | `"⭐ из вашей базы"` |

A saved match is **always `medium` tier** and **always presented as a draft at the confirm step**
(never silently auto-saved). This is the strongest safeguard against stale portion data: the
owner confirmed the food before, but the quantity may differ. Full auto-accept with no badge
is deferred to TD-013 (three-score gate).

#### §4c — Two-phase resolve logic

`SavedFoodRAGStrategy.resolve(signals, db)` runs two phases in order; the first hit returns:

**Phase A — exact-barcode short-circuit (if `signals.barcode` is set):**

```python
if signals.barcode:
    pf = crud_personal_food.get_by_barcode(db, barcode=signals.barcode, user_id=user.id)
    if pf:
        return _build_result(pf, source="saved_rag_barcode", distance=0.0)
    # else: fall through to Phase B
```

`get_by_barcode` uses the `personal_foods_user_barcode_idx` index (§3). Cost: one indexed SQL
lookup, no embedding call. This is the "no OFF call for a repeat barcoded item" optimisation.

**Phase B — fuzzy ANN over `personal_food_embeddings` (text/image path):**

```python
# Text path: query is the raw text input
# Image path: query is the joined vision food names (signals.vision_result["foods"])
query_text = _build_query_text(signals)
if not query_text:
    return None

embedding = await openai_svc.embed_text(query_text)
result = crud_personal_food.find_similar(
    db, embedding=embedding, threshold=settings.SAVED_FOOD_SIM_THRESHOLD, user_id=user.id
)
if result is None:
    return None

pf, distance = result
return _build_result(pf, source="saved_rag", distance=distance)
```

**Query text construction:**
- Image input: use `", ".join(signals.vision_result["foods"])` (reusing vision output already
  available in signals; no extra API call).
- Text input: **future scope, not implemented in B3.** Text (`kind="text"`) inputs currently
  bypass `resolve_meal_nutrition()` entirely and go straight to `analyze_food_entry`
  (ADR-0001), so `SavedFoodRAGStrategy` only runs for image inputs and `ImageSignals` carries
  no raw-text field. Wiring text inputs through the pipeline (so the personal DB is consulted
  for text logs too) is a follow-up; until then this branch is intentionally unreachable.

#### §4d — `CRUDPersonalFood.find_similar` seam

```python
def find_similar(
    self,
    db: Session,
    *,
    embedding: list[float],
    threshold: float,
    user_id: int,
) -> Optional[tuple[PersonalFood, float]]:
    """ANN lookup with mandatory user_id filter.

    Returns (PersonalFood, cosine_distance) if the closest match is within threshold,
    else None.  threshold is cosine distance (0=identical, 2=opposite); a typical
    default is 0.15.

    This method is the mockable ANN seam for B6 tests — SQLite has no pgvector
    operators; B6 patches this method directly.
    """
```

**Why this is the seam:** the pgvector `<=>` operator is Postgres-only. Unit tests run on SQLite.
B6 mocks `CRUDPersonalFood.find_similar` and tests only `SavedFoodRAGStrategy` branch logic
(hit / miss / below-threshold / above-threshold). The real ANN/threshold correctness check is a
**manual post-deploy verification** (not an automated test).

#### §4e — `resolution_signals` payload additions

`SavedFoodRAGStrategy` adds these keys to `ResolutionResult.signals` (per ADR-0001 §5 contract):

```json
{
  "saved_food_id":       42,
  "saved_food_name":     "Греческий йогурт FAGE 2%",
  "saved_match_distance": 0.08,
  "saved_match_source":  "saved_rag",
  "query_text":          "греческий йогурт"
}
```

For the barcode short-circuit path, `saved_match_distance` is `0.0` and `saved_match_source` is
`"saved_rag_barcode"`. These values are persisted in `meals.resolution_signals` and are the
primary signal for future misprediction analysis.

#### §4f — Non-blocking contract (ADR-0001 §3)

All network errors, DB errors, embedding API failures, and not-found cases must return `None`
(not raise). The pipeline runner catches nothing — `SavedFoodRAGStrategy` is responsible for
its own exception boundary:

```python
try:
    ...
except Exception:
    logger.warning("SavedFoodRAGStrategy failed; falling through", exc_info=True)
    return None
```

---

### §5 — Similarity threshold config parameter

**Decision:** the similarity threshold is a **config parameter**, not a hardcoded constant.

| Config key | Type | Default | Meaning |
|---|---|---|---|
| `SAVED_FOOD_SIM_THRESHOLD` | `float` | `0.15` | cosine distance cutoff; ≤ threshold → hit |

**Default rationale:** cosine distance ≤ 0.15 corresponds to cosine similarity ≥ 0.85 for
normalized OpenAI embeddings. Empirically, food names within the same canonical cluster (e.g.
"Greek yogurt", "Греческий йогурт FAGE", "yogurt grec") fall well within 0.15; unrelated foods
(e.g. "apple" vs "chicken breast") score ≥ 0.5. The 0.15 default is deliberately conservative —
false positives (wrong food suggested) are more harmful than false negatives (user re-describes
the food). **The owner should tune this experimentally after a few weeks of use via `.env`.**

B2 reads `settings.OPENAI_EMBEDDING_MODEL` and `settings.OPENAI_EMBEDDING_DIMS`.
B3 reads `settings.SAVED_FOOD_SIM_THRESHOLD`.
Neither constant is hardcoded in application code.

---

## Summary of new config parameters

All optional (safe defaults provided); none are required for deploy:

| Key | Default | Notes |
|---|---|---|
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | Embedding model; must match VECTOR(N) dims |
| `OPENAI_EMBEDDING_DIMS` | `1536` | Migration-locked; do not change post-deploy |
| `SAVED_FOOD_SIM_THRESHOLD` | `0.15` | Cosine distance cutoff; tune experimentally |

---

## `_build_pipeline()` final order

```
saved_rag → barcode_off → name_off → label_ocr → name_web → vision
```

```python
def _build_pipeline(db: Session) -> list[ResolutionStrategy]:
    return [
        SavedFoodRAGStrategy(),    # medium — B3 (personal base; barcode short-circuit first)
        BarcodeOFFStrategy(),      # high   — A4 (round 1)
        NameOFFStrategy(),         # medium — A8 (round 2)
        LabelOCRStrategy(),        # medium — A10 (round 2)
        NameWebSearchStrategy(),   # medium/low — A9 (round 2)
        VisionFallbackStrategy(),  # low    — always last
    ]
```

B6 must update the `_build_pipeline` order-assertion test to include `saved_rag` at position 0.

---

## Deploy delta (former B7)

openclaw-setup release note for this feature:

### Required infra change (manual step before first migration run)

```yaml
# In docker-compose.prod.yml, change:
#   image: postgres:15
# to:
image: pgvector/pgvector:pg15
```

This is a **drop-in image swap** — same data directory, same bind-mount, no pg_upgrade needed.
The migration `0001_enable_vector_extension` then runs `CREATE EXTENSION IF NOT EXISTS vector;`
as its first statement.

### Migration ordering

The existing **migrate-before-start** gate handles ordering automatically:
1. `CREATE EXTENSION IF NOT EXISTS vector;` (new — must be first migration in this feature branch)
2. `CREATE TABLE personal_foods (…)` + indexes
3. `CREATE TABLE personal_food_embeddings (…)` + HNSW index

### No new required env variables

`OPENAI_API_KEY` already exists. `OPENAI_EMBEDDING_MODEL`, `OPENAI_EMBEDDING_DIMS`, and
`SAVED_FOOD_SIM_THRESHOLD` are all optional with safe defaults.

---

## Dependency graph

```
B0 (this ADR) ─────────────────┬─▶ B1 (personal_foods domain + pgvector migration)
                               └─▶ B2 (embed_text() on OpenAIService)

B0 + B1 + B2 ──────────────────────▶ B3 (SavedFoodRAGStrategy)
B0 + B1 + B2 ──────────────────────▶ B4 (learning-loop write-back)

B3 + B4 ────────────────────────────▶ B6 (tests)
```

B1 and B2 may run in parallel once this ADR is accepted. B3 is the join of B0+B1+B2.

---

## What each downstream item must implement

### B1 — personal_foods domain + pgvector

- Alembic migration: `CREATE EXTENSION IF NOT EXISTS vector` + both tables + all indexes.
- `PersonalFood` SQLAlchemy model (maps to `personal_foods`); `PersonalFoodEmbedding` model
  (maps to `personal_food_embeddings`; `embedding` column type = `pgvector.sqlalchemy.Vector(1536)`
  or `sqlalchemy_utils` equivalent — match the library chosen in the migration).
- `CRUDPersonalFood` with:
  - `upsert(db, *, user_id, canonical_name, ...) -> PersonalFood` — idempotent on `(user_id, lower(canonical_name))`.
  - `get_by_barcode(db, *, barcode, user_id) -> Optional[PersonalFood]`.
  - `find_similar(db, *, embedding, threshold, user_id) -> Optional[tuple[PersonalFood, float]]` — the B6 mock seam.
  - `add_embedding(db, *, personal_food_id, text_embedded, embedding) -> PersonalFoodEmbedding`.
  - `get_embeddings_for_food(db, *, personal_food_id) -> list[PersonalFoodEmbedding]`.
- `created_at NOT NULL server_default=func.now()` on both tables (TD-006 lesson).

### B2 — embed_text() on OpenAIService

- `async def embed_text(self, text: str) -> list[float]` using `self.client.embeddings.create(...)`.
- Reads `settings.OPENAI_EMBEDDING_MODEL` and `settings.OPENAI_EMBEDDING_DIMS`.
- Records an `ai_call_logs` row with `kind="embedding"` for audit trail. Implemented by calling
  `record_ai_call(kind="embedding", ...)` directly (status ok/error + latency) rather than via
  `analyze_and_log`: the embeddings call returns a `list[float]`, not the text/`parse=` shape
  `analyze_and_log` is built around, so the direct call is the cleaner fit. Same audit outcome.
- Optional: `async def embed_texts(self, texts: list[str]) -> list[list[float]]` (batch call for B4 alias embedding).
- Does NOT use `_create()` (that is `chat.completions` only — ADR-0002 split-client pattern).
- Must NOT raise on rate-limit etc. — callers (B3, B4) wrap in their own try/except.

### B3 — SavedFoodRAGStrategy

- Implements the two-phase resolve (§4c): barcode short-circuit → fuzzy ANN.
- Reads `settings.SAVED_FOOD_SIM_THRESHOLD`.
- Adds `"⭐ из вашей базы"` case to `_source_badge` in `telegram.py` (one-liner, folded here).
- Records `saved_food_id`, `saved_match_distance`, `query_text` in signals (§4e).
- `source_id = "saved_rag"`, `confidence_tier = "medium"` (class attribute).
- Entire resolve wrapped in `try/except Exception → logger.warning + return None` (§4f).

### B4 — learning-loop write-back

- On confirm (and TD-015 correction path): Celery fire-and-forget task that:
  1. Calls `crud_personal_food.upsert(...)` with the confirmed food's canonical name + macros + provenance (meal_id, resolution_source).
  2. Persists the barcode on the row when the confirmed item had one (`signals.barcode`).
  3. Calls `openai_svc.embed_text(canonical_name)` and `crud_personal_food.add_embedding(...)`.
  4. Optionally embeds aliases if the correction path produced alternate forms.
- Idempotent: the `upsert` dedup key is `(user_id, lower(canonical_name))` — re-running the task
  on the same food updates `times_used`, `last_used_at` without inserting duplicates.
- Task is retry-safe: the upsert + add_embedding must tolerate being called twice on the same
  `(user_id, canonical_name)` (e.g. from Celery retry on transient DB error).

---

## Consequences

**Good:**

- One datastore, one backup surface, one migration pipeline. No cross-store consistency risk.
- `meals JOIN personal_foods ON user_id` is a plain SQL join — trivial to query for analytics.
- Alias-per-row strategy maximises recall for alternate surface forms at near-zero cost.
- The mandatory `user_id` filter is enforced at the CRUD layer (`find_similar` signature) — it
  cannot be accidentally omitted by B3.
- `find_similar` is the only pgvector-dependent seam; B6 can test all branch logic by mocking it
  on SQLite without any pgvector dependency.
- `SAVED_FOOD_SIM_THRESHOLD` in config means the owner can tune precision/recall after deploy
  without a code change.
- `SavedFoodRAGStrategy` before `BarcodeOFFStrategy` eliminates the OFF call for repeat barcoded
  items — saves latency and OFF API quota.

**Costs/risks:**

- **Image swap is a manual infra step** — if openclaw-setup deploys without swapping the image,
  `CREATE EXTENSION vector` fails and the migration halts. The deploy delta section above is the
  release note; it must be communicated clearly.
- **Migration lock on `VECTOR(1536)`** — changing `OPENAI_EMBEDDING_DIMS` after first deploy
  requires a full re-embed + schema migration. The 1536 default is safe for `text-embedding-3-small`.
- **ANN index not effective below ~100 rows** — HNSW falls back to sequential scan at small row
  counts (pgvector does this automatically). This is fine; performance improves naturally as the
  personal DB grows.
- **No automated ANN/threshold test** — the similarity query correctness check is manual post-deploy
  (SQLite gap). The B6 mock covers branch logic; the threshold value is validated experimentally.
- **Celery task failure on first confirm** — if the embed+upsert task fails silently, the food
  is logged but not saved to `personal_foods`. The next confirm of the same food will retry (a
  new task is enqueued). This is acceptable: the personal DB is an optimisation layer, not the
  source of truth for `meals`.

**Not decided here:**

- Local embedding model adoption (e.g. ollama) — `OPENAI_EMBEDDING_MODEL` is the hook; a future
  ADR can substitute the provider without changing `personal_food_embeddings`.
- Retention / deletion policy for `personal_foods` rows — deferred to TD-010 (`/forget` command).
- Multi-user scoping hardening — `user_id` column + ANN filter is present; quota enforcement and
  per-user deletion are future work.
- TD-013 three-score gate — when that lands, `SavedFoodRAGStrategy` may be promoted to `"high"`
  tier for the identity score while portion score remains medium. No contract change needed here.
