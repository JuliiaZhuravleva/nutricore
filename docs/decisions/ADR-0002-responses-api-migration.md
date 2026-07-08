# ADR-0002 — OpenAI Responses-API Adoption: Split-Client Strategy for `web_search`

**Status:** Accepted  
**Date:** 2026-07-08  
**Author:** specialist-architect (photo-product-lookup round-2 A12)  
**Implements:** [photo-product-lookup-round2.md](../photo-product-lookup-round2.md), item A12 (enabling ADR for A9)  
**Consumed by:** A9 (`NameWebSearchStrategy`, backend-dev)  
**Cross-reference:** [ADR-0001](ADR-0001-pluggable-nutrition-resolution-pipeline.md) — pipeline contract these strategies plug into

---

## Context

`app/services/openai_service.py` centralises all OpenAI calls behind `OpenAIService._create()`,
which delegates to `client.chat.completions.create()`. Two cross-cutting mechanisms are layered
over this single gate:

1. **TD-005 model self-heal** — `ModelUnavailableError` is raised by `_create()` when the
   configured model is deprecated/unknown; the Telegram layer catches it, offers an in-chat model
   picker, and persists the replacement via `app_settings`.
2. **`OPENAI_MAX_RETRIES`** — injected into `AsyncOpenAI(max_retries=...)` at construction;
   applies to transient errors on all `client.*` calls.
3. **`ai_call_logs` audit trail** — `analyze_and_log()` in `ai_call_log_service.py` times the
   coroutine, records `status`, `raw_response`, `parsed_result`, and `latency_ms`; callers
   supply `kind=` and a `parse=` callable. ADR-0001 §8 assigned `kind="web_search"` for A9 calls.

A9 (`NameWebSearchStrategy`) requires the **Responses API** (`client.responses.create()` with
the built-in `web_search` tool) — a distinct endpoint with different request/response shapes from
`chat.completions`. The question is whether to:

- **Option A (split-client):** keep all existing `chat.completions` calls untouched; add
  `web_search_nutrition()` as the only method that goes through the Responses API path.
- **Option B (full migration):** move `analyze_food_entry`, `analyze_food_image`,
  `extract_barcode_from_image`, `extract_nutrition_label` (A10), and `web_search_nutrition`
  all to the Responses API.

---

## Decision

**Adopt Option A — the split-client approach.**

`web_search_nutrition()` is the **only** `OpenAIService` method that uses the Responses API.
All existing methods remain on `chat.completions` without modification. The "split" is at the
method level within the same `AsyncOpenAI` client instance (both `client.chat.completions.create`
and `client.responses.create` are on the single shared `self.client`) — no second client is
instantiated.

### Rationale

Full migration (Option B) blast radius is prohibitive for a single new feature:

| Risk area | Chat path today | Responses API delta |
|---|---|---|
| TD-005 self-heal | `ModelUnavailableError` raised by `_create()` | Responses API uses different exception shapes; self-heal logic would need a parallel rewrite |
| `OPENAI_MAX_RETRIES` | Inherited automatically via `AsyncOpenAI(max_retries=…)` | Same constructor arg applies to `client.responses.*` — **preserved for free** |
| `_create()` guard | 4 existing methods + A10's `extract_nutrition_label` | Touching `_create()` risks breaking 276 green tests |
| Error translation | `NotFoundError`/`BadRequestError` → `ModelUnavailableError` | Responses API: `NotFoundError` may have a different `.code` for web_search-specific issues |

Option A keeps the 276-test safety net intact; Option B can be revisited as a standalone
refactor once A9 is proven.

---

## Implementation contract for A9

### 1. New method: `OpenAIService.web_search_nutrition`

```python
async def web_search_nutrition(
    self,
    query_foods: list[str],
) -> str:
    """Call the Responses API web_search tool to retrieve product nutrition text.

    Returns the raw text content from the model's response (plain string), so
    analyze_and_log(kind="web_search", parse=...) can consume it unchanged.

    Does NOT catch exceptions — all network / API / parse errors propagate to
    the caller (NameWebSearchStrategy.resolve → try/except → return None).
    Does NOT trigger the TD-005 self-heal mechanism (that path is chat.completions
    only).
    """
    query = ", ".join(query_foods)
    response = await self.client.responses.create(
        model=self.model,
        tools=[{"type": "web_search_preview"}],
        input=f"Find the exact КБЖУ (calories, protein, fat, carbs per 100g) for: {query}",
    )
    # Return the text content as a plain string — shape matches existing text methods.
    return response.output_text  # str | raises if response has no text output
```

**Return shape:** a plain `str` (the model's web-search-augmented reply). This matches the
`raw` type expected by `analyze_and_log` (`raw_response=raw if isinstance(raw, str)`), so the
audit trail adapter works without modification.

### 2. `analyze_and_log` usage in `NameWebSearchStrategy`

`NameWebSearchStrategy.resolve()` calls `analyze_and_log` exactly as existing strategies do:

```python
# Inside NameWebSearchStrategy.resolve():
try:
    parsed = await analyze_and_log(
        svc.web_search_nutrition(query_foods),
        kind="web_search",
        input_ref=", ".join(query_foods),
        telegram_id=telegram_id,
        model=svc.model,
        parse=_parse_web_nutrition_response,
    )
except Exception:
    logger.warning("web_search_nutrition failed; falling through", exc_info=True)
    return None
```

`analyze_and_log` re-raises on any failure; the `except Exception` in `resolve()` satisfies the
**non-blocking contract** from ADR-0001 §3. No special exception subtype handling is needed
inside `resolve()` — all failures produce the same `return None` outcome and are already recorded
by `analyze_and_log` before re-raising.

The `parse=` callable (`_parse_web_nutrition_response`) is defined in
`product_lookup_service.py`. See §5 below for its expected input/output contract.

### 3. TD-005 self-heal: explicitly excluded for Responses API calls

`web_search_nutrition` does **not** use `_create()` and does **not** raise `ModelUnavailableError`.
If the Responses API rejects the configured model, the exception propagates as a plain
`openai.NotFoundError` (or similar), caught by the strategy's `except Exception` → `None`.
This is correct: the in-chat model picker is a chat.completions UX; surfacing it for a background
web_search strategy would confuse the user (they'd see a model warning while confirming a
meal photo).

### 4. Failure-mode enumeration

Every failure mode below degrades to `return None` from `NameWebSearchStrategy.resolve()`.
`analyze_and_log` writes `status="error"` to `ai_call_logs` before re-raising; the strategy's
`except Exception` catches and suppresses. **No blanket `except Exception` inside
`web_search_nutrition` itself** — the method lets exceptions propagate cleanly.

| Failure mode | Exception class | Logged by | Result |
|---|---|---|---|
| Rate limit (429) | `openai.RateLimitError` | `analyze_and_log` status="error" | `None` |
| Request timeout | `openai.APITimeoutError` | `analyze_and_log` status="error" | `None` |
| Auth / key error | `openai.AuthenticationError` | `analyze_and_log` status="error" | `None` |
| Model not found / deprecated | `openai.NotFoundError` | `analyze_and_log` status="error" | `None` |
| Empty web_search output (no `output_text`) | `AttributeError` / `ValueError` in method | `analyze_and_log` status="error" | `None` |
| No useful nutrition in the response text | `ValueError` raised by `_parse_web_nutrition_response` | `analyze_and_log` status="error" | `None` |
| Malformed / missing citation structure | `ValueError` raised by `_parse_web_nutrition_response` | `analyze_and_log` status="error" | `None` |
| OFF re-query fails (see §6) | caught internally within OFF path | logged via `signals` key | falls back to prose path (or `None`) |

> **Rule:** new failure modes discovered during A9 implementation must be added to this table
> (or to the ADR addendum if the shape is surprising). A blanket `except Exception` inside
> `web_search_nutrition` itself is prohibited — it would swallow real bugs silently.

### 5. `_parse_web_nutrition_response` contract

A9 must implement `_parse_web_nutrition_response(raw: str) -> dict` with the following output
shape (the `dict` returned by `analyze_and_log`):

```python
{
    "identification": str | None,    # product name / brand identified by the web search
    "off_query": str | None,         # suggested query for the OFF re-lookup step, or None
    "nutrition_prose": str | None,   # raw nutrition text from web (present only for prose path)
    "confidence_path": "off_requery" | "web_prose",  # which outcome branch
}
```

`parse` must raise `ValueError` (not return a partial dict) if the response is empty, contains
no product identification, or is structurally unparseable. This lets `analyze_and_log` record
`status="error"` and the strategy fall through cleanly.

### 6. Dynamic `confidence_tier` (A9 has two outcome paths)

ADR-0001 §3 defines `confidence_tier: str` as a class attribute on the strategy protocol — a
**discovery hint** for the pipeline runner. The actual tier is carried by `ResolutionResult`,
which has its own `confidence_tier: str` field (§2 of ADR-0001). These two are intentionally
decoupled:

- **Class attribute** (`NameWebSearchStrategy.confidence_tier = "medium"`) — nominal/default,
  used only for introspection (e.g. A13 pipeline-order tests).
- **Result field** (`ResolutionResult.confidence_tier`) — the authoritative value for the
  Telegram badge and `meals.resolution_source`/`resolution_signals` persistence.

**A9 is permitted to set `ResolutionResult.confidence_tier` dynamically** at resolve time:

| Outcome | `confidence_tier` | Badge |
|---|---|---|
| OFF re-query succeeded (structured numbers) | `"medium"` | `🌐 нашли в сети (проверь)` |
| Pure web prose (no OFF re-query) | `"low"` | `🌐 нашли в сети (сверь — веб)` |

This is the only strategy in the current pipeline with branching confidence; the pattern is
deliberately not generalised in ADR-0001 to avoid over-engineering. If more strategies need it,
a future ADR may add `min_confidence_tier` / `max_confidence_tier` hints to the protocol.

### 7. Search-signal choice for A9

`NameWebSearchStrategy` uses `signals.vision_result["foods"]` (the vision-extracted food-name
list from Phase 1) as the web search query. This list is already present in `ImageSignals` and
incurs no additional API call.

The alternative (a dedicated brand-extraction Phase-1 call — a separate vision prompt focused on
product brand + exact name) would improve search precision for multi-word brand names but adds
latency and cost. This is **deferred**: implement with `signals.vision_result["foods"]` first;
add a brand-extraction Phase-1 sub-call only if real-world recall data (misprediction logs) shows
systematic miss-identification at the query-construction step.

`name_query` (the joined string sent to `web_search`) is recorded in `ResolutionResult.signals`
for misprediction analysis, consistent with the `name_query` field introduced by A8.

---

## `ai_call_logs` audit trail (ADR-0001 §8 extension)

ADR-0001 §8 reserved `kind="web_search"` for A9. Confirmed:

| Call | `kind` value | Notes |
|---|---|---|
| Web search nutrition (A9) | `"web_search"` | Plain string returned from `web_search_nutrition()`; parsed by `_parse_web_nutrition_response` |
| Label OCR (A10) | `"label_ocr"` | Per ADR-0001 §8; uses `chat.completions` via `_create()` as normal |

No new `ai_call_logs` columns are needed. `model` is recorded as `svc.model` (the currently
configured chat model) for both calls — this may differ from the Responses API model in the
future but is sufficient for current auditing.

---

## `_build_pipeline()` final order

The ratified strategy order (confirmed in `human_feedback` 2026-07-08):

```
barcode_off → name_off → label_ocr → name_web → vision
```

Rationale for `label_ocr` before `name_web`:
- `label_ocr` reads on-pack ground truth (the printed nutrition table) — medium confidence but
  directly from the product.
- `name_web` infers from the internet — one more remove from ground truth, higher latency.
- Placing `label_ocr` earlier means a legible label always beats a web lookup.

After A10 and A9 both land, `_build_pipeline()` must be:

```python
def _build_pipeline(db: Session) -> list[ResolutionStrategy]:
    return [
        BarcodeOFFStrategy(),      # high  — A4 (round 1)
        NameOFFStrategy(),         # medium — A8 (round 2)
        LabelOCRStrategy(),        # medium — A10 (round 2)
        NameWebSearchStrategy(),   # medium/low — A9 (round 2)
        VisionFallbackStrategy(),  # low   — always last
    ]
```

A13 adds a `_build_pipeline()` order-assertion test that fails if the order drifts.

---

## What is explicitly NOT changed

- `OpenAIService._create()` — untouched. Chat path methods are unchanged.
- `ModelUnavailableError` and the TD-005 self-heal flow — Responses API calls are outside
  this guard by design.
- `OPENAI_MAX_RETRIES` — the constructor-level retry count applies automatically to
  `client.responses.*` calls via the shared `AsyncOpenAI` instance.
- `OpenAIService.__init__`, `set_model`, `list_suitable_models` — no changes.
- `analyze_and_log` — no changes; it accepts any `Awaitable[Any]` and `parse` callable.
- `telegram.py` / `_source_badge` — A9/A10 each add one badge-string case (confirmed in
  `human_feedback` 2026-07-08, Q5). This is a one-liner per strategy, folded into A9/A10
  implementation — not an architect concern.

---

## Consequences

**Good:**
- 276-test safety net is undisturbed.
- TD-005 self-heal, `OPENAI_MAX_RETRIES`, and `ai_call_logs` work unchanged for all existing
  and future `chat.completions` methods (including A10's `extract_nutrition_label`).
- `web_search_nutrition()` returns a plain string — the existing `analyze_and_log` adapter
  requires no modification; `kind="web_search"` is the only new registration.
- Failure modes are enumerated explicitly; no blanket swallowing of real bugs.
- Dynamic `confidence_tier` is justified and scoped to `ResolutionResult` (no protocol change).
- The deferred brand-extraction refinement is logged as a concrete future option with a
  trigger condition (misprediction log evidence).

**Costs/risks:**
- Two API endpoint styles coexist in `OpenAIService`. A future full-migration ADR can
  consolidate; the split-client pattern makes that migration surgical (swap `web_search_nutrition`
  to a shared `_create_response()` call if the rest migrate).
- The Responses API `output_text` attribute is assumed stable; if OpenAI changes the response
  shape, `web_search_nutrition` will raise `AttributeError` → `analyze_and_log` logs the error
  → strategy returns `None`. No silent failure.
- `web_search` calls incur higher latency (500–2000 ms typical) and per-call cost ($0.002–0.01
  estimated at current Responses API pricing). A9 sits second-to-last in the pipeline precisely
  to minimise frequency — most photos resolve at `barcode_off`, `name_off`, or `label_ocr`.

---

## Dependency on this ADR

A9 (`NameWebSearchStrategy`, `backend-dev`) **must not start** until this ADR is accepted and
the following are settled:

- [x] Split-client decision (§ Decision above)
- [x] `web_search_nutrition` method signature (§1)
- [x] `analyze_and_log` compatibility (§2)
- [x] Failure-mode enumeration (§4)
- [x] `_parse_web_nutrition_response` contract (§5)
- [x] Dynamic `confidence_tier` approval (§6)
- [x] Search-signal choice (§7)

A10 (`LabelOCRStrategy`) has **no dependency on this ADR** — it uses `chat.completions`
via the existing `_create()` path and can proceed in parallel.
