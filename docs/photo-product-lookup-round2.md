# Product lookup — round 2 (label OCR + web-search)

> **Scope:** the remaining, deliberately-deferred strategies of the packaged-food
> accuracy upgrade (TD-011). Round-1 (barcode → Open Food Facts) and A8 (packaging name →
> OFF name-search) are **already shipped** — see [`photo-product-lookup.md`](photo-product-lookup.md)
> and [ADR-0001](decisions/ADR-0001-pluggable-nutrition-resolution-pipeline.md). This doc
> plans **only** the two remaining items, un-deferred by owner decision (2026-07-08):
> **A10 label-OCR** and **A9 web-search**, plus the migration ADR that A9 needs.
>
> **Do not re-plan shipped work.** The barcode and name-search strategies, the pipeline,
> the `product_caches` table, the `meals.resolution_*` columns, and the confidence-badge
> plumbing all exist and must not be recreated. Items below are **purely additive** on the
> existing pipeline: each is a new `ResolutionStrategy` class + one line in
> `_build_pipeline()`, exactly as A8 was (zero `telegram.py`/schema/config change for the
> strategy itself — the badge path is generic on `confidence_tier`).

## Background (what already exists — read, don't rebuild)

`app/services/product_lookup_service.py` runs an **ordered, first-non-None-wins** strategy
pipeline over shared `ImageSignals` (base64 image, vision result, vision-read barcode,
portion grams). Current order in `_build_pipeline()`:

```
barcode_off  (high)   → name_off  (medium)   → vision  (low, always returns a result)
```

Each strategy implements the `ResolutionStrategy` protocol: `async resolve(signals, db)
-> Optional[ResolutionResult]`, **non-blocking** (all network/parse/not-found → return
`None`, never raise), and sets `source`, `confidence_tier`, scaled `nutrition`,
`description`, `portion_grams`, and a `signals` dict for transparency. The Telegram layer
renders a source/confidence badge via `_source_badge` keyed on `confidence_tier` (generic —
a new medium-tier source needs only a badge string, no handler change). Per-100g → eaten
portion scaling is the shared `_scale_off_nutrition` helper (generalize if label/web return
per-serving instead of per-100g).

## The two remaining strategies

### A10 — Label OCR  ⟶  `LabelOCRStrategy` (`source_id = "label_ocr"`, medium)

Many packages **print the nutrition table**. When the barcode is unreadable/unknown and
OFF name-search misses, vision can read the КБЖУ table directly off the image — no external
call, no API migration.

- **Trigger / gate:** only for packaged-looking single items (mirror A8's `≤2 foods` gate —
  don't OCR a multi-item plate). Runs after `name_off`, before `vision`.
- **Mechanism:** a dedicated vision extraction that returns the table's numbers **with their
  basis** (per-100g vs per-serving vs per-package) — the basis is essential or the scaling is
  wrong. Reuse the existing base64 `image_data_url` signal; a new focused prompt/parser on
  `OpenAIService` (e.g. `extract_nutrition_label`). Returns `None` when no table is legible.
- **Scaling:** if per-100g → existing `_scale_off_nutrition`; if per-serving/per-package →
  scale by the servings the vision portion implies (needs a small scaling branch — call it
  out, don't hand-wave). Portion basis must surface in the reply (A6 pattern).
- **Confidence:** medium, but distinct badge text from `name_off` (e.g.
  `🏷 с этикетки (проверь)`), so the user knows the number came from OCR of the pack.
- **Never cached** (image-specific), like A8.
- **No `telegram.py`/schema/config change** beyond the badge string.

### A9 — Web search  ⟶  `NameWebSearchStrategy` (`source_id = "name_web"`, medium/low)

When brand+name are readable but the product isn't in OFF, use OpenAI's **`web_search`**
tool to identify the product and find its КБЖУ. **Blocked on the migration ADR below** —
`web_search` is a **Responses-API** tool; the bot's OpenAI calls are `chat.completions`
today.

- **Prefer structured numbers.** The core principle (see round-1 doc): a nutrition app lives
  or dies on the КБЖУ being right, and free-text web prose is easy to paraphrase wrong.
  Decide (in the ADR / open questions) whether web_search may supply the **numbers**
  directly, or only **identify** the product so we re-query a structured source (OFF) for the
  numbers. Default lean: use it to find → still pull numbers from OFF where possible; a pure
  web number gets the **lowest** medium/low confidence and the most cautious badge.
- **Ordering:** after `name_off` (OFF is more trustworthy than web prose). Whether it sits
  before or after `label_ocr` is an open question — label OCR is on-pack ground truth, web is
  inference; likely `name_off → label_ocr → name_web → vision`. Confirm in the plan.
- **Confidence must surface loudly.** Never present a web-search number with barcode-like
  certainty. Distinct, cautious badge (e.g. `🌐 нашли в сети (сверь)`).
- **Cost/latency:** higher than the other paths — gate behind the opt-in/auto-trigger and
  keep it last-but-one. Note the added per-call cost in the ADR.

## The enabling decision — Responses-API migration ADR (blocks A9)

Before A9 can be built, an **architect ADR** must decide the `chat.completions` →
**Responses API** migration:

- **Blast radius:** migrate *only* the new web_search call, or *all* `OpenAIService` calls
  (`analyze_food_entry`, `analyze_food_image`, `extract_barcode_from_image`,
  `extract_nutrition_label`)? A split client is simpler to land but leaves two API styles;
  a full migration is cleaner but touches the self-heal + retry + logging paths.
- **Interactions to preserve:** the TD-005 model self-heal (`_create` → `ModelUnavailableError`,
  in-chat model picker, `app_settings` persistence), `OPENAI_MAX_RETRIES`, and the
  `ai_call_logs` recording all wrap the current chat call. The ADR must say how each maps onto
  the Responses API (or that the split client keeps them on the chat path untouched).
- **Cost/latency + failure modes** of `web_search` (rate limits, no-result, citation
  handling) and how they degrade to `None` (non-blocking contract).
- **Output:** `docs/decisions/ADR-000X-responses-api-migration.md`. A9 items `depends_on` it.

## Explicitly out of scope (do not plan)

- Any change to shipped barcode / name-search / pipeline / caching / badge plumbing.
- Third-party reverse-image search (Google Vision / SerpAPI) — see round-1 doc "Correction".
- Additional product DBs (USDA, regional) — future, not this round.
- Inbound persistence / retention / deletion — owned by TD-009/TD-010, not re-solved here.

## Verification (per item)

- Unit tests for each new strategy in isolation (hit / miss / malformed / not-single-item),
  mirroring the A8 test pattern in `tests/test_product_lookup_service.py` and
  `tests/test_open_food_facts_service.py`.
- `_build_pipeline()` order test updated to the final agreed order.
- Autouse mocks so pre-existing photo tests don't make real vision/web calls (A8 added the
  same guards — extend them).
- Full suite green via `./scripts/test.sh` (cache-venv python — TD-001, **not** `poetry run`).
- Manual: drive the bot in polling mode (`/run`) with a labelled package (A10) and, once the
  ADR lands + A9 built, a named-but-not-in-OFF product (A9); confirm the correct badge + gram
  basis appears and each path degrades to the vision estimate on failure.

## Open questions for the plan

1. **Strategy order** — confirm `name_off → label_ocr → name_web → vision`.
2. **Web numbers** — may `web_search` supply the КБЖУ directly, or only identify → re-query OFF?
3. **Label OCR basis** — how aggressively to trust per-serving/per-package scaling vs falling
   through to vision when the basis is ambiguous.
4. **Migration blast radius** — web_search-only split client vs full Responses-API migration.

## Relation to other work

- [ADR-0001](decisions/ADR-0001-pluggable-nutrition-resolution-pipeline.md) — the pipeline
  contract these plug into.
- [`photo-product-lookup.md`](photo-product-lookup.md) — the full multi-strategy vision;
  round-1 + A8 shipped there.
- `_tech-debt.md` **TD-011** — the tracking item this un-defers.
