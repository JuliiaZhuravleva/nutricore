# Nutricore — Meal-Input Processing Flow

> **Two formats, one content.** [`input-processing-flow.html`](./input-processing-flow.html) is the
> **human-facing** visual diagram (open in a browser). **This Markdown mirrors it for LLMs / quick
> reading** — read this to save tokens, don't parse the HTML. A thinking tool about the capture
> surface, **not** a spec.

One spine of **4 phases** — `Capture → Resolve → Confidence → Draft` — shown at **3 maturity tiers**:
**Now** (shipped) → **Next** (realistic nutricore core) → **North-star** (someday, from research).

Read it two ways: **rows** = maturity, **columns** = the four phases of one flow.

---

## Tier 1 — Now (shipped, `main` `4e8f458`)

*What actually runs in the bot today.*

- **Capture** — Photo **or** text: one message, either/or (time is picked first, then input).
  - ⚠️ **Gap ①** — a photo's caption is stored in `inbound_messages` but is **not** sent to the model.
- **Resolve** — Ordered pipeline, first-non-None wins: `barcode_off → name_off → vision`.
  Phase 1 extracts barcode + vision concurrently; phase 2 walks the pipeline.
  - ⚠️ **Gap ②** — a fresh OpenAI call every time; past meals are never reused.
- **Confidence** — A single source badge (📦 / ✅ / 🔍 / 📷); the only check is human **Yes / No**.
  - ⚠️ **Gap ③** — one per-strategy tier, not separate identity / portion / nutrition; no clarification step.
- **Draft** — `confirm → meals` (writes `resolution_source` / `resolution_signals`); "No" discards the draft.
  - ⚠️ **Gap ④** — "correct at confirm" is text only; there is no real portion/gram edit control (reject → resend).

**Verified against code:** `app/services/telegram.py` (`process_meal_input`, `_run_meal_analysis`,
`confirm_meal`, `_source_badge`) and `app/services/product_lookup_service.py` (`_build_pipeline`,
`resolve_meal_nutrition`).

## Tier 2 — Next (designed — realistic core for nutricore, an OpenAI bot)

*Julia's 4-step target vision. Purely additive on the ADR-0001 pluggable pipeline.*

- **Capture** — Photo **+** text **+** barcode **+** label combined as one signal. Plus a **quick-pick
  from saved / recent** meals → change the quantity, skipping analysis entirely.
- **Resolve** — Source ranking: `saved/RAG → barcode → name/RAG → label_ocr → web → vision`
  (`label_ocr`, `web` = round-2, planned in `plan/photo-product-lookup-round2`). Free text →
  structured JSON → retrieval over the personal + product DB (RAG).
- **Confidence** — Three **separate** scores (`identity` / `portion` / `nutrition`) + reconciliation
  rules → **confident: auto-accept**; **else: minimal clarification** via quick buttons (portion S/M/L,
  candidate pick). Principle: *the smallest question that resolves the largest uncertainty.*
- **Draft** — Final draft → save. **Learning loop:** user edits persist to the personal DB as
  confirmed aliases, so the same food resolves instantly next time.

## Tier 3 — North-star (someday — from research, far from the current OpenAI bot)

*Enterprise / on-prem orientation from
`docs/researches/on-premises-calorie-tracking-for-a-medical-and-activity-data-ecosystem.md`. A compass, not a plan.*

- **Capture** — Local VLM (Qwen2.5-VL / Mistral Small 3.1) as the intake brain; EXIF/GPS strip;
  policy-gated egress; on-prem by default.
- **Resolve** — Composite pipeline: pgvector RAG, local USDA FDC / Open Food Facts mirrors, ZXing
  barcode, SAM2 / FoodSeg segmentation, PaddleOCR. Recognition / matching / nutrient-resolution as
  **separate services**.
- **Confidence** — Portion-estimation ladder (depth / geometry), source-prior scoring, deterministic
  reconciliation thresholds, full provenance on every number.
- **Draft** — FHIR export (`NutritionIntake` / `NutritionProduct` / `Observation` / `Provenance`);
  retention/deletion policies, audit logs, link into the my-health ecosystem.

---

## Legend — source badges (real bot strings)

| Badge | Meaning | Source (`source_id`) |
|-------|---------|----------------------|
| 📦 по штрих-коду | exact | `barcode_off` (round-1) |
| ✅ из базы | exact — high tier | high-confidence generic |
| 🔍 нашли в базе | check it — name search | `name_off` (A8) |
| 🏷 с этикетки | check it — label OCR | `label_ocr` (round-2, planned) |
| 🌐 нашли в сети | verify — web search | `name_web` (round-2, planned) |
| 📷 оценка по фото | low — vision estimate | `vision` (always-last fallback) |

## Legend — confidence (target: three separate scores)

| Score | Question it answers |
|-------|---------------------|
| `identity` | Is the right product/food identified? |
| `portion`  | Is the amount estimate reliable? |
| `nutrition`| Are the final calories/macros trustworthy? |

---

*Sources: Now tier verified against `telegram.py` + `product_lookup_service.py`. Next/North-star from the
research doc and the round-2 plan (`plan/photo-product-lookup-round2`). Regenerate the HTML from the same
content if this file changes.*
