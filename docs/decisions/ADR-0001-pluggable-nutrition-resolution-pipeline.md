# ADR-0001 — Pluggable Meal-Nutrition Resolution Pipeline

**Status:** Accepted  
**Date:** 2026-07-07  
**Author:** specialist-architect (photo-product-lookup A11)  
**Implements:** [photo-product-lookup.md](../photo-product-lookup.md), item A11  
**Consumed by:** A4 (pipeline integration), A5 (Telegram UX), A6 (portion scaling), A8/A9/A10 (future strategies)

---

## Context

Today every meal photo goes to `OpenAIService.analyze_food_image` (vision estimate).
The photo-product-lookup feature adds higher-confidence paths for packaged products:

| Strategy | Confidence | Round |
|---|---|---|
| Barcode → Open Food Facts | high | 1 (A4) |
| Packaging name → OFF name-search | medium | 2 (A8) |
| Packaging name → web_search (Responses API) | medium | 2 (A9) |
| Label OCR (vision reads nutrition table) | medium | 2 (A10) |
| Vision estimate (current default) | low | already shipped |

The risk of **hard-wiring the barcode branch into `process_meal_input`** is that adding
A8/A9/A10 later requires touching the same handler again, creating merge conflicts and
making the ordering implicit in `if/elif` logic. Instead: a pluggable, ordered pipeline
that A4 implements as the first strategy and A8/A9/A10 extend by dropping in a new class.

### North-star constraint (product principle, 2026-07-07)

> Give the user the **best-guess result seamlessly** — never nag with extra questions
> when the system can decide — but keep every step **transparent and correctable**.
> Surface the resolution path + key intermediate values in/near the reply. Log where
> the pipeline mispredicts (wrong product, wrong grams, wrong path) as data to improve
> the system.

This means the pipeline must always produce a result (no new blocking prompts),
always record how it got there, and expose enough signals that the Telegram layer
(A5) can surface them without re-querying the DB.

---

## Decision

### 1. Two-phase pipeline in `app/services/product_lookup_service.py`

**Phase 1 — Signal extraction (concurrent):**  
Run two OpenAI calls in parallel for every image, collecting signals that
all strategies share:

| Signal | Source | Notes |
|---|---|---|
| `barcode` | `OpenAIService.extract_barcode_from_image` (A3) | `None` if no barcode visible |
| `vision_result` | `OpenAIService.analyze_food_image` (existing) | Nutrition + food names + portion |
| `portion_grams` | parsed from `vision_result["portion"]` | Used by all strategies for scaling |

Running them concurrently with `asyncio.gather` keeps latency at max(barcode_call,
vision_call) rather than sum. The barcode extraction uses `max_tokens=64` and is cheap.

**Phase 2 — Strategy resolution (ordered, first-win):**  
Iterate the registered strategy list; return the first `ResolutionResult` that is
not `None`. The last strategy in the list is always the vision fallback (never
returns `None`), so phase 2 always produces a result.

### 2. Canonical data types

```python
# app/services/product_lookup_service.py

@dataclass
class ImageSignals:
    """All signals extracted from the raw image; shared by all strategies."""
    image_data_url: str          # base64 data URL (never exposed to 3rd parties)
    barcode: Optional[str]       # A3 result — digits-only string or None
    vision_result: Optional[dict]  # parsed nutrition dict from analyze_food_image
    portion_grams: Optional[float]  # extracted from vision_result["portion"]


@dataclass
class ResolutionResult:
    """The final nutrition numbers + metadata for one meal entry."""
    source: str                  # "barcode_off" | "name_off" | "label_ocr" | "vision"
    confidence_tier: str         # "high" | "medium" | "low"
    nutrition: dict              # keys: calories, proteins, fats, carbs (scaled to eaten portion)
    description: Optional[str]   # human-readable product/food name for the confirm reply
    portion_grams: Optional[float]  # the gram basis used for scaling (shown in reply per A6)
    signals: dict                # see §5 — full transparency + misprediction payload
```

### 3. Strategy protocol

```python
from typing import Protocol, Optional
from sqlalchemy.orm import Session

class ResolutionStrategy(Protocol):
    """Contract all pipeline strategies must satisfy.

    `source_id` is the value written to meals.resolution_source.
    `confidence_tier` is "high" | "medium" | "low".
    """
    source_id: str
    confidence_tier: str

    async def resolve(
        self,
        signals: ImageSignals,
        db: Session,
    ) -> Optional[ResolutionResult]:
        """Return ResolutionResult if this strategy can supply nutrition, else None.

        Must be non-blocking to the caller: all network errors, not-found cases,
        and parsing failures → return None (don't raise). The pipeline runner
        will try the next strategy.
        """
        ...
```

Strategies are **pure over `ImageSignals`**: they read the pre-extracted signals and
(optionally) make additional DB or network calls (e.g. OFF HTTP lookup). They do not
call `analyze_food_image` themselves — that is phase 1's job.

### 4. Pipeline runner

```python
# app/services/product_lookup_service.py

async def resolve_meal_nutrition(
    image_data_url: str,
    db: Session,
    *,
    telegram_id: Optional[int] = None,
) -> ResolutionResult:
    """Entry point for A4.  Returns the best-confidence result available.

    1. Extract image signals (barcode + vision) concurrently.
    2. Run registered strategies in order; return first non-None result.
    """
    signals = await _extract_signals(image_data_url, telegram_id=telegram_id)
    pipeline = _build_pipeline(db)
    for strategy in pipeline:
        result = await strategy.resolve(signals, db)
        if result is not None:
            return result
    # Unreachable — VisionFallbackStrategy always returns a result.
    raise RuntimeError("Pipeline exhausted without a result (VisionFallbackStrategy missing?)")


def _build_pipeline(db: Session) -> list[ResolutionStrategy]:
    """Ordered strategy list.  A8/A9/A10 insert before VisionFallbackStrategy."""
    return [
        BarcodeOFFStrategy(),          # A4 (round 1)
        # NameOFFStrategy(),           # A8 (round 2, deferred)
        # LabelOCRStrategy(),          # A10 (round 2, deferred)
        # WebSearchStrategy(),         # A9 (round 2, blocked on ADR)
        VisionFallbackStrategy(),      # always last
    ]


async def _extract_signals(
    image_data_url: str,
    *,
    telegram_id: Optional[int],
) -> ImageSignals:
    """Run barcode extraction and vision analysis concurrently."""
    svc = openai_service_instance()
    barcode_coro = svc.extract_barcode_from_image(image_data_url)
    vision_coro = _run_vision_analysis(image_data_url, telegram_id=telegram_id)
    barcode, vision_result = await asyncio.gather(
        barcode_coro, vision_coro, return_exceptions=True
    )
    # Treat extraction exceptions as None (not a fatal error).
    if isinstance(barcode, Exception):
        logger.warning("Barcode extraction failed: %s", barcode)
        barcode = None
    if isinstance(vision_result, Exception):
        logger.error("Vision analysis failed: %s", vision_result, exc_info=True)
        vision_result = None
    return ImageSignals(
        image_data_url=image_data_url,
        barcode=barcode,
        vision_result=vision_result,
        portion_grams=_parse_portion_grams(vision_result),
    )
```

> **Integration note for A4:** `resolve_meal_nutrition` replaces the
> `_analyze_and_parse(kind="image", …)` call inside `_run_meal_analysis` for photo
> inputs. Text inputs (`kind="text"`) are unchanged — they still go directly to
> `analyze_food_entry`. The function signature of `_run_meal_analysis` grows a
> `resolution_result: Optional[ResolutionResult]` parameter so the meal draft carries
> `resolution_source` + `resolution_signals` for persistence (A1 columns).

### 5. `resolution_signals` payload contract

Every `ResolutionResult.signals` dict **must** include these keys (use `None` for
values not available in a given strategy path). They are persisted verbatim to
`meals.resolution_signals` (JSON) and inspected by A5 for the reply badge and by
future analytics for misprediction analysis:

```json
{
  "barcode_raw":          "4607195501226",
  "barcode_detected":     true,
  "product_name":         "Чипсы Pringles Original 165g",
  "brand":                "Pringles",
  "off_code":             "4607195501226",
  "off_from_cache":       false,
  "off_latency_ms":       320,
  "portion_grams":        150.0,
  "confidence_tier":      "high",
  "strategy_tried":       ["barcode_off", "vision"],
  "strategy_chosen":      "barcode_off",
  "vision_foods":         ["chips", "Pringles"],
  "vision_portion_raw":   "1 serving (30g)"
}
```

Keys that don't apply to a strategy (e.g. `off_latency_ms` for the vision fallback)
are omitted or set to `null`. The key set may be extended by future strategies —
callers must treat unknown keys as opaque.

### 6. Confidence tiers and auto-trigger policy

| Tier | Value | Strategy | Telegram UX (A5) |
|---|---|---|---|
| `"high"` | barcode → OFF found | `barcode_off` | **Auto-apply** — no disambiguation button |
| `"medium"` | name → OFF / label OCR | `name_off`, `label_ocr` | Auto-apply **with badge** showing the match |
| `"low"` | vision estimate | `vision` | Auto-apply (existing behaviour) |
| `"ambiguous"` | (future) multiple candidates | TBD | Show **buttons** for user to pick |

`"ambiguous"` is reserved for cases where a future strategy finds multiple plausible
matches and cannot choose automatically. Round 1 does not produce this tier.

> **Auto-trigger rule (CQ1):** the Telegram layer (A5) inspects
> `result.confidence_tier`. A `"high"` or `"medium"` result is applied seamlessly
> (no new prompt). An `"ambiguous"` result (future) presents candidate buttons.
> The user can always correct the gram basis or any field at the existing confirm step.

### 7. Portion scaling (CQ2)

All strategies receive `signals.portion_grams` (from the vision analysis). A strategy
that returns per-100g numbers (e.g. `BarcodeOFFStrategy`) **must** scale them before
populating `ResolutionResult.nutrition`:

```python
if portion_grams is not None:
    factor = portion_grams / 100.0
    nutrition = {
        "calories": round(off_result.calories_per_100g * factor, 1),
        "proteins": round(off_result.proteins_per_100g * factor, 1),
        "fats":     round(off_result.fats_per_100g * factor, 1),
        "carbs":    round(off_result.carbohydrates_per_100g * factor, 1),
    }
else:
    # No portion estimate — use per-100g as-is; surface in signals so A5 can warn.
    nutrition = {
        "calories": off_result.calories_per_100g,
        ...
    }
```

`portion_grams` is always included in `signals` and surfaced in the Telegram reply
(A6) so the user can correct it at the confirm step.

### 8. `ai_call_logs` audit trail

No new columns needed. The existing `kind` field covers all pipeline calls:

| Call | `kind` value | Logged by |
|---|---|---|
| Barcode extraction (A3) | `"barcode_extraction"` | `analyze_and_log(…, kind="barcode_extraction")` |
| Vision food analysis | `"image"` | existing `_analyze_and_parse` |
| Label OCR (future A10) | `"label_ocr"` | same pattern |

For **OFF HTTP calls** (not OpenAI): `off_latency_ms` + `off_from_cache` are captured
in `resolution_signals` on the meal — no separate log table needed in round 1.
If OFF call volume grows, a dedicated `off_call_logs` table can be added without
changing the pipeline contract.

### 9. What A4 must implement

A4 creates `app/services/product_lookup_service.py` with:

- `ImageSignals` dataclass  
- `ResolutionResult` dataclass  
- `ResolutionStrategy` Protocol  
- `resolve_meal_nutrition()` entry point  
- `_build_pipeline()` + `_extract_signals()` helpers  
- `BarcodeOFFStrategy` (uses A3 barcode + A2 OFF client)  
- `VisionFallbackStrategy` (wraps existing vision result)  
- `_parse_portion_grams(vision_result)` — parses the portion string from vision output  

A4 also modifies `_run_meal_analysis` in `telegram.py` to:
- Call `resolve_meal_nutrition()` for `kind="image"` (replaces `_analyze_and_parse`)
- Persist `resolution_source` + `resolution_signals` on the meal draft
- Pass both to `_nutrition_reply` for badge/gram-basis display (A5/A6 changes)
- Verify `/reprocess` still works (same `inbound_id` lifecycle; text reprocess is unchanged)

### 10. What A8/A9/A10 must implement to plug in

Each deferred strategy creates a class implementing `ResolutionStrategy`:
- **A8** (`NameOFFStrategy`): reads `signals.vision_result["foods"]`, calls OFF name-search,
  returns `confidence_tier="medium"`.
- **A10** (`LabelOCRStrategy`): makes a separate vision call to read the nutrition table,
  parses the raw text; returns `confidence_tier="medium"`.
- **A9** (`WebSearchStrategy`): calls Responses API web_search; may only run after its own
  ADR is approved. Returns `confidence_tier="medium"`.

Each registers by being uncommented in `_build_pipeline()`. No other code changes needed.

---

## Dependency graph (updated)

```
A1 (DB schema)  ─── A2 (OFF client) ─┐
A3 (barcode extraction) ──────────────┼─ A11 (this ADR) ─── A4 (pipeline integration)
                                      │                       │
                                      │                       ├─ A5 (Telegram UX)
                                      │                       ├─ A6 (portion scaling)
                                      │                       └─ A7 (tests)
                                      │
                                      ├─ A8 (name→OFF, round 2)
                                      └─ A9 (web_search, round 2, blocked on ADR)
A1 ─────────────── A10 (label OCR, round 2)
```

A11 (this ADR) is a logical prerequisite for A4 — A4 implements against the interface
contracts defined here. A8/A9/A10 depend on A11 because they plug into the pipeline
framework defined here.

---

## Consequences

**Good:**
- A8/A9/A10 are purely additive: drop in a class, uncomment one line. No changes to
  `telegram.py` or `_run_meal_analysis` for future strategies.
- The two-phase design makes phase 1 latency overhead minimal (concurrent OpenAI calls).
- `resolution_signals` gives full transparency to the user reply (A5), the misprediction
  log (future analytics), and future automated tests (A7 can assert on signal values).
- Vision always runs, so the fallback is free (no extra latency penalty when barcode
  lookup fails).
- The `ai_call_logs` audit trail covers all OpenAI calls without schema changes.

**Costs/risks:**
- Every packaged product photo now makes **two** concurrent OpenAI calls (barcode
  extraction + vision analysis). The barcode call uses `max_tokens=64` and is cheap
  (~$0.0001), but it is a second call. Acceptable for a personal tool.
- `_run_meal_analysis` in `telegram.py` must be modified for A4 — it is an existing
  function carrying significant test coverage (TD-009 suite). A4 must run full
  regression (`pytest`) and verify `/reprocess` end-to-end.
- The `Protocol` pattern requires Python 3.8+; the project uses 3.12+, so no issue.

**Not decided here:**
- Whether to add a `product_lookup_enabled` feature flag. The PRD says "opt-in" but
  the human feedback (CQ1 answer) says auto-trigger when barcode is detected. Round 1
  treats the pipeline as always-on for image inputs — a flag can be added later if
  needed. A4 should not gate on a flag unless Julia explicitly requests one.
- The `"ambiguous"` confidence tier UX: left for A8/A9 (multiple candidates, fuzzy match).
- Multi-product-DB support (USDA FoodData Central etc.): can be added as new strategies
  in a future round.
