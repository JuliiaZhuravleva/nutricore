"""Pluggable meal-nutrition resolution pipeline (A4 / ADR-0001).

Entry point: ``resolve_meal_nutrition(image_data_url, ...)`` — replaces the
direct ``_analyze_and_parse(kind="image")`` call in ``_run_meal_analysis`` for
photo inputs.  Text inputs are unchanged and still go through ``_analyze_and_parse``.

Phase 1 — concurrent signal extraction (barcode + vision).
Phase 2 — ordered strategy resolution; first non-None result wins.

Round-1 strategies:
  BarcodeOFFStrategy   (high confidence, A4)
  VisionFallbackStrategy  (low confidence, always last)

Round-2 strategies (A8/A10/A9 — all additive, one line each in _build_pipeline):
  NameOFFStrategy      (medium confidence, A8)
  LabelOCRStrategy     (medium confidence, A10)
  NameWebSearchStrategy (medium/low confidence, A9)
"""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.crud.crud_personal_food import crud_personal_food
from app.crud.crud_user import crud_user
from app.db.session import SessionLocal
from app.models.personal_food import PersonalFood
from app.services.ai_call_log_service import analyze_and_log
from app.services.open_food_facts_service import OFFLookupResult, OpenFoodFactsService
from app.services.openai_service import get_openai_service

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared parse helper (also used by telegram.py for text/reprocess paths)
# ---------------------------------------------------------------------------

_REQUIRED_NUTRITION_KEYS = ("calories", "protein", "fats", "carbs", "portion")


def parse_nutrition(raw: Any) -> dict:
    """OpenAI analysis result → validated dict with a guaranteed list ``foods``.

    Both analysis methods return the raw JSON string; the model may also give
    ``foods`` as a bare string.  Normalises both, and raises ``ValueError`` on a
    None/non-object payload or one missing the fields the reply needs — so a
    malformed response is recorded as an error and surfaced, not turned into a
    downstream KeyError.
    """
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(data, dict):
        raise ValueError(f"expected a JSON object, got {type(data).__name__}")
    foods = data.get("foods", [])
    foods = [foods] if isinstance(foods, str) else (foods or [])
    # Coerce elements to str: models the owner swaps in (o-series, etc.) may
    # return foods as dicts/numbers, which would blow up ", ".join(...) later.
    data["foods"] = [str(x) for x in foods]
    missing = [k for k in _REQUIRED_NUTRITION_KEYS if k not in data]
    if missing:
        raise ValueError(f"nutrition response missing keys: {missing}")
    return data


# ---------------------------------------------------------------------------
# Pipeline data types
# ---------------------------------------------------------------------------


@dataclass
class ImageSignals:
    """All signals extracted from the raw image; shared by all strategies."""

    image_data_url: str  # base64 data URL (never exposed to 3rd parties)
    barcode: Optional[str]  # A3 result — digits-only string or None
    vision_result: Optional[dict]  # parsed nutrition dict from analyze_food_image
    portion_grams: Optional[float]  # extracted from vision_result["portion"]
    # Logging context for strategies that make their own OpenAI calls (A10+).
    telegram_id: Optional[int] = None
    input_ref: Optional[str] = None


@dataclass
class ResolutionResult:
    """The final nutrition numbers + metadata for one meal entry."""

    source: str  # "barcode_off" | "name_off" | "label_ocr" | "name_web" | "vision"
    confidence_tier: str  # "high" | "medium" | "low"
    # Nutrition keys match the existing schema: calories, protein, fats, carbs,
    # portion, foods — so _nutrition_reply and confirm_meal need no changes.
    nutrition: dict
    description: Optional[str]  # human-readable name for the confirm reply
    portion_grams: Optional[float]  # gram basis used for scaling (shown in reply)
    signals: dict = field(default_factory=dict)  # transparency + misprediction payload


# ---------------------------------------------------------------------------
# Strategy protocol (structural subtyping — Python 3.8+ Protocol)
# ---------------------------------------------------------------------------


class ResolutionStrategy:
    """Interface all pipeline strategies must satisfy.

    ``source_id``       → value written to meals.resolution_source.
    ``confidence_tier`` → "high" | "medium" | "low".
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
        and parsing failures → return None (don't raise).  The pipeline runner
        will try the next strategy.
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Shared strategy helpers
# ---------------------------------------------------------------------------


def _scale_off_nutrition(
    off_result: OFFLookupResult,
    portion_grams: Optional[float],
    *,
    fallback_food: str,
) -> dict:
    """Scale an OFF per-100g result to the eaten portion (ADR-0001 §7).

    Shared by every OFF-backed strategy (barcode, name-search, …). When
    ``portion_grams`` is known and positive, multiply the per-100g macros by
    ``portion/100`` and label the portion in grams; otherwise keep the per-100g
    numbers as-is with a ``"100г"`` portion (the reply layer warns the user).
    ``fallback_food`` names the item when OFF has no ``product_name``.
    """
    foods = [off_result.product_name] if off_result.product_name else [fallback_food]
    if portion_grams is not None and portion_grams > 0:
        factor = portion_grams / 100.0
        return {
            "calories": round((off_result.calories_per_100g or 0) * factor, 1),
            "protein": round((off_result.proteins_per_100g or 0) * factor, 1),
            "fats": round((off_result.fats_per_100g or 0) * factor, 1),
            "carbs": round((off_result.carbohydrates_per_100g or 0) * factor, 1),
            "portion": f"{portion_grams:.0f}г",
            "foods": foods,
        }
    return {
        "calories": off_result.calories_per_100g or 0,
        "protein": off_result.proteins_per_100g or 0,
        "fats": off_result.fats_per_100g or 0,
        "carbs": off_result.carbohydrates_per_100g or 0,
        "portion": "100г",
        "foods": foods,
    }


# ---------------------------------------------------------------------------
# Label-OCR helpers (A10)
# ---------------------------------------------------------------------------


def _parse_label_json(raw: Any) -> dict:
    """Parse the JSON returned by ``extract_nutrition_label``.

    Used as the ``parse=`` callback for :func:`analyze_and_log`.  Raises
    ``ValueError`` on a non-object payload so the call-log records the parse
    error rather than silently ignoring it.  Returns an empty dict when the
    model signals illegibility by returning JSON ``null`` — the strategy treats
    ``basis=None`` (empty dict) as a fall-through to vision.
    """
    if raw is None:
        return {}
    data = json.loads(raw) if isinstance(raw, str) else raw
    if data is None:  # JSON null — model couldn't read the label
        return {}
    if not isinstance(data, dict):
        raise ValueError(
            f"label_ocr: expected a JSON object, got {type(data).__name__}"
        )
    # Coerce model-supplied numerics to float-or-None so a JSON-valid but
    # non-numeric value (units like "120 kcal", a comma decimal, "n/a") can't
    # raise a ValueError later in the strategy — the strategy's None-guards then
    # fall through to vision cleanly.  Parity with the web path, which sanitises
    # the same fields via _safe_float in _parse_web_nutrition_response.
    for _k in (
        "calories",
        "protein",
        "fats",
        "carbs",
        "serving_grams",
        "package_grams",
    ):
        if _k in data:
            data[_k] = _safe_float(data.get(_k))
    return data


def _scale_label_nutrition(
    *,
    calories: float,
    protein: float,
    fats: float,
    carbs: float,
    basis_grams: float,
    portion_grams: Optional[float],
    foods: List[str],
) -> dict:
    """Scale per-basis-weight label numbers to the eaten portion.

    When ``portion_grams`` is known and positive, multiplies all macros by
    ``portion_grams / basis_grams`` and labels the portion in grams; otherwise
    returns the label's numbers as-is with a basis-gram string as the portion
    (the reply layer warns the user that the gram basis is unconfirmed).
    """
    if portion_grams is not None and portion_grams > 0:
        factor = portion_grams / basis_grams
        return {
            "calories": round(calories * factor, 1),
            "protein": round(protein * factor, 1),
            "fats": round(fats * factor, 1),
            "carbs": round(carbs * factor, 1),
            "portion": f"{portion_grams:.0f}г",
            "foods": foods,
        }
    return {
        "calories": round(calories, 1),
        "protein": round(protein, 1),
        "fats": round(fats, 1),
        "carbs": round(carbs, 1),
        "portion": f"{basis_grams:.0f}г",
        "foods": foods,
    }


# ---------------------------------------------------------------------------
# Round-1 strategies
# ---------------------------------------------------------------------------


class BarcodeOFFStrategy(ResolutionStrategy):
    """Use A3 barcode + A2 OFF client.  Returns high-confidence result or None."""

    source_id = "barcode_off"
    confidence_tier = "high"

    async def resolve(
        self,
        signals: ImageSignals,
        db: Session,
    ) -> Optional[ResolutionResult]:
        if not signals.barcode:
            return None

        started = time.perf_counter()
        try:
            svc = OpenFoodFactsService(db)
            off_result = await svc.lookup(signals.barcode)
        except Exception as exc:
            # The OFF client swallows network/parse/cache errors to None, so this
            # is a genuinely unexpected failure — log the stack trace (an infra
            # bug here would otherwise masquerade as a routine "no match").
            logger.warning(
                "BarcodeOFFStrategy: lookup raised for %s: %s",
                signals.barcode,
                exc,
                exc_info=True,
            )
            off_result = None
        latency_ms = int((time.perf_counter() - started) * 1000)

        if off_result is None:
            return None

        # --- Portion scaling (ADR §7) ------------------------------------
        portion_grams = signals.portion_grams
        nutrition = _scale_off_nutrition(
            off_result, portion_grams, fallback_food=signals.barcode
        )

        signals_dict: Dict[str, Any] = {
            "barcode_raw": signals.barcode,
            "barcode_detected": True,
            "product_name": off_result.product_name,
            "brand": off_result.brand,
            "off_code": off_result.off_code,
            "off_from_cache": off_result.from_cache,
            "off_latency_ms": latency_ms,
            "portion_grams": portion_grams,
            "confidence_tier": self.confidence_tier,
            "strategy_tried": [self.source_id],  # runner updates this
            "strategy_chosen": self.source_id,
            "vision_foods": (signals.vision_result or {}).get("foods", []),
            "vision_portion_raw": (signals.vision_result or {}).get("portion"),
        }

        return ResolutionResult(
            source=self.source_id,
            confidence_tier=self.confidence_tier,
            nutrition=nutrition,
            description=off_result.product_name or signals.barcode,
            portion_grams=portion_grams,
            signals=signals_dict,
        )


# A name search only makes sense for a *packaged* single product. A plate with
# several distinct foods would match some arbitrary packaged item at medium
# confidence, so above this many vision foods we skip name search and let vision
# handle the plate (honest low confidence beats a confident-wrong medium).
_NAME_SEARCH_MAX_FOODS = 2


class NameOFFStrategy(ResolutionStrategy):
    """Vision food name → OFF full-text search (A8).  Medium confidence.

    Runs only when the barcode strategy did not resolve (no barcode visible, or
    the scanned product carried no nutrition).  Uses the vision-read food
    name(s) as a search query and takes OFF's best relevance-ranked match that
    has real macros.  Returns ``None`` (fall through to vision) when there is no
    usable food name, the plate has too many distinct foods, or OFF finds
    nothing — so a miss degrades gracefully to the vision estimate.
    """

    source_id = "name_off"
    confidence_tier = "medium"

    async def resolve(
        self,
        signals: ImageSignals,
        db: Session,
    ) -> Optional[ResolutionResult]:
        vision = signals.vision_result or {}
        foods = [
            f for f in (vision.get("foods") or []) if isinstance(f, str) and f.strip()
        ]
        if not foods:
            return None
        if len(foods) > _NAME_SEARCH_MAX_FOODS:
            logger.debug(
                "NameOFFStrategy: %d foods (> %d) — skipping name search "
                "(looks like a multi-item plate)",
                len(foods),
                _NAME_SEARCH_MAX_FOODS,
            )
            return None

        query = " ".join(foods).strip()
        started = time.perf_counter()
        try:
            svc = OpenFoodFactsService(db)
            off_result = await svc.search_by_name(query)
        except Exception as exc:
            # search_by_name swallows network/parse errors to None, so a raise
            # here is genuinely unexpected — log the stack trace.
            logger.warning(
                "NameOFFStrategy: search raised for %r: %s", query, exc, exc_info=True
            )
            off_result = None
        latency_ms = int((time.perf_counter() - started) * 1000)

        if off_result is None:
            return None

        portion_grams = signals.portion_grams
        nutrition = _scale_off_nutrition(off_result, portion_grams, fallback_food=query)

        signals_dict: Dict[str, Any] = {
            # This result came from a name search, not a barcode — keep the
            # reply's "Штрих-код:" line off (barcode_raw=None) so a detected but
            # unresolved barcode isn't misattributed to a name-matched product.
            "barcode_raw": None,
            "barcode_detected": signals.barcode is not None,
            "product_name": off_result.product_name,
            "brand": off_result.brand,
            "off_code": off_result.off_code,
            "off_from_cache": False,  # name search is never cached
            "off_latency_ms": latency_ms,
            "portion_grams": portion_grams,
            "confidence_tier": self.confidence_tier,
            "strategy_tried": [self.source_id],  # runner updates this
            "strategy_chosen": self.source_id,
            "vision_foods": vision.get("foods", []),
            "vision_portion_raw": vision.get("portion"),
            "name_query": query,  # what we searched — for misprediction analysis
        }
        # Preserve a detected-but-unresolved barcode for analytics without
        # surfacing it in the reply (see barcode_raw note above).
        if signals.barcode is not None:
            signals_dict["barcode_unresolved"] = signals.barcode

        return ResolutionResult(
            source=self.source_id,
            confidence_tier=self.confidence_tier,
            nutrition=nutrition,
            description=off_result.product_name or query,
            portion_grams=portion_grams,
            signals=signals_dict,
        )


class LabelOCRStrategy(ResolutionStrategy):
    """On-pack nutrition label OCR (A10).  Medium confidence.

    Reads the nutrition facts table directly from the product's packaging using
    the vision model.  Handles per-100g / per-serving / per-package tables; when
    the gram basis cannot be determined the strategy returns ``None`` and falls
    through to vision.  This is documented intentional policy: never surface
    confidently-wrong numbers (ADR-0001 §7 / A10 clarifying Q4).  Never cached.

    Runs after ``NameOFFStrategy``, before ``NameWebSearchStrategy``/``VisionFallbackStrategy``.
    """

    source_id = "label_ocr"
    confidence_tier = "medium"

    async def resolve(
        self,
        signals: ImageSignals,
        db: Session,
    ) -> Optional[ResolutionResult]:
        # Short-circuit: if Phase-1 vision failed there is no food context and
        # no gram-portion estimate for scaling — fall through immediately.
        if signals.vision_result is None:
            return None

        vision = signals.vision_result
        foods = [
            f for f in (vision.get("foods") or []) if isinstance(f, str) and f.strip()
        ]

        # Single-item gate (mirrors A8): a multi-item plate is unlikely to be a
        # packaged product with a legible label → skip to avoid a confident-wrong
        # match.
        if len(foods) > _NAME_SEARCH_MAX_FOODS:
            logger.debug(
                "LabelOCRStrategy: %d foods (> %d) — skipping label OCR "
                "(looks like a multi-item plate)",
                len(foods),
                _NAME_SEARCH_MAX_FOODS,
            )
            return None

        from app.services.openai_service import ModelUnavailableError

        svc = get_openai_service()
        started = time.perf_counter()
        try:
            label_data = await analyze_and_log(
                svc.extract_nutrition_label(signals.image_data_url),
                kind="label_ocr",
                input_ref=signals.input_ref,
                telegram_id=signals.telegram_id,
                model=svc.model,
                parse=_parse_label_json,
            )
        except ModelUnavailableError:
            # extract_nutrition_label goes through _create() (chat.completions),
            # so a deprecated/missing model must reach _run_meal_analysis's
            # self-heal handler — not be masked as a routine label-OCR miss
            # (parity with _extract_signals).
            raise
        except Exception as exc:
            # analyze_and_log re-raises after logging — swallow here so the
            # pipeline can continue to the next strategy.
            logger.warning(
                "LabelOCRStrategy: extract_nutrition_label raised: %s",
                exc,
                exc_info=True,
            )
            return None
        latency_ms = int((time.perf_counter() - started) * 1000)

        if not label_data:
            return None

        basis = label_data.get("basis")
        if not basis:
            # Label absent or illegible — fall through to vision.
            logger.debug(
                "LabelOCRStrategy: basis=null — label illegible, falling through"
            )
            return None

        calories = label_data.get("calories")
        protein = label_data.get("protein")
        fats = label_data.get("fats")
        carbs = label_data.get("carbs")

        if any(v is None for v in (calories, protein, fats, carbs)):
            logger.debug(
                "LabelOCRStrategy: incomplete macro values (%s) — falling through",
                {
                    k: label_data.get(k)
                    for k in ("calories", "protein", "fats", "carbs")
                },
            )
            return None

        # Determine the gram basis for scaling.
        portion_grams = signals.portion_grams
        if basis == "per_100g":
            basis_grams = 100.0
        elif basis == "per_serving":
            serving_grams = label_data.get("serving_grams")
            if not serving_grams or serving_grams <= 0:
                # "per serving" with no gram weight → basis-ambiguous → None.
                # Documented intentional policy: never surface confidently-wrong
                # numbers (ADR-0001 §7 / A10 clarifying Q4).
                logger.debug(
                    "LabelOCRStrategy: basis=per_serving but serving_grams=%r "
                    "— ambiguous, falling through to vision",
                    serving_grams,
                )
                return None
            basis_grams = serving_grams
        elif basis == "per_package":
            package_grams = label_data.get("package_grams")
            if not package_grams or package_grams <= 0:
                logger.debug(
                    "LabelOCRStrategy: basis=per_package but package_grams=%r "
                    "— ambiguous, falling through to vision",
                    package_grams,
                )
                return None
            basis_grams = package_grams
        else:
            logger.debug("LabelOCRStrategy: unknown basis=%r — falling through", basis)
            return None

        food_list = foods or ["продукт с этикетки"]
        # calories/protein/fats/carbs are already float (coerced in
        # _parse_label_json) and guaranteed non-None by the guard above.
        nutrition = _scale_label_nutrition(
            calories=calories,
            protein=protein,
            fats=fats,
            carbs=carbs,
            basis_grams=basis_grams,
            portion_grams=portion_grams,
            foods=food_list,
        )

        signals_dict: Dict[str, Any] = {
            # No barcode surface — this result came from label OCR, not a barcode
            # scan.  Keep barcode_raw=None so the reply doesn't show a stray EAN.
            "barcode_raw": None,
            "barcode_detected": signals.barcode is not None,
            "product_name": None,  # no OFF product name
            "brand": None,
            "off_code": None,
            "off_from_cache": False,  # never cached
            "off_latency_ms": None,
            "portion_grams": portion_grams,
            "confidence_tier": self.confidence_tier,
            "strategy_tried": [self.source_id],  # runner updates this
            "strategy_chosen": self.source_id,
            "vision_foods": vision.get("foods", []),
            "vision_portion_raw": vision.get("portion"),
            "label_basis": basis,
            "label_basis_grams": basis_grams,
            "label_ocr_latency_ms": latency_ms,
        }
        # Preserve a detected-but-unresolved barcode for analytics without
        # surfacing it in the reply (mirrors NameOFFStrategy).
        if signals.barcode is not None:
            signals_dict["barcode_unresolved"] = signals.barcode

        return ResolutionResult(
            source=self.source_id,
            confidence_tier=self.confidence_tier,
            nutrition=nutrition,
            description=", ".join(food_list),
            portion_grams=portion_grams,
            signals=signals_dict,
        )


# ---------------------------------------------------------------------------
# Web-search helpers (A9)
# ---------------------------------------------------------------------------


def _extract_json_from_text(text: str) -> dict:
    """Extract the first JSON object from a text string.

    Handles: clean JSON, markdown code blocks (```json … ```), and JSON
    embedded in prose.  Raises ``ValueError`` if no valid JSON object is found.
    """
    stripped = text.strip()
    # 1. Try direct parse (cleanest case).
    try:
        data = json.loads(stripped)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    # 2. Try to extract from ```json ... ``` or ``` ... ``` markdown blocks.
    block_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, re.DOTALL)
    if block_match:
        try:
            data = json.loads(block_match.group(1))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    # 3. Find first '{' and last '}' (handles JSON embedded in prose).
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and start < end:
        try:
            data = json.loads(stripped[start : end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    raise ValueError(
        f"web_search: could not extract a JSON object from response: {text[:200]!r}"
    )


def _safe_float(value: Any) -> Optional[float]:
    """Convert a value to float, returning ``None`` for null/non-numeric values."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_web_nutrition_response(raw: str) -> dict:
    """Parse the web search response into a structured dict.

    Used as the ``parse=`` callback for :func:`analyze_and_log` (``kind="web_search"``).
    Raises ``ValueError`` if the response is empty, contains no product identification,
    or is structurally unparseable, so ``analyze_and_log`` records ``status="error"``
    and the strategy falls through cleanly.

    Returns a dict with the ADR-0002 §5 shape plus caller-convenience macro keys:

    .. code-block:: python

        {
            "identification": str,
            "off_query": str,           # same as identification; for OFF re-lookup
            "nutrition_prose": str,     # JSON-encoded macro snapshot for audit
            "confidence_path": "off_requery",
            # convenience extras used by NameWebSearchStrategy:
            "calories_per_100g": float | None,
            "protein_per_100g": float | None,
            "fats_per_100g": float | None,
            "carbs_per_100g": float | None,
        }

    ``confidence_path`` is always ``"off_requery"`` when identification is found —
    the strategy tries OFF first and falls to web numbers on a miss.
    """
    if not raw or not raw.strip():
        raise ValueError("web_search: empty response")

    data = _extract_json_from_text(raw)

    identification = data.get("identification")
    if not identification or not str(identification).strip():
        raise ValueError("web_search: response contains no product identification")
    identification = str(identification).strip()

    calories = _safe_float(data.get("calories_per_100g"))
    protein = _safe_float(data.get("protein_per_100g"))
    fats = _safe_float(data.get("fats_per_100g"))
    carbs = _safe_float(data.get("carbs_per_100g"))

    return {
        "identification": identification,
        "off_query": identification,
        # Serialised macro snapshot — logged by analyze_and_log as parsed_result.
        "nutrition_prose": json.dumps(
            {
                "calories_per_100g": calories,
                "protein_per_100g": protein,
                "fats_per_100g": fats,
                "carbs_per_100g": carbs,
            }
        ),
        # Primary path: try OFF re-query with the identified product name.
        "confidence_path": "off_requery",
        # Convenience: let resolve() use these directly for the prose fallback path.
        "calories_per_100g": calories,
        "protein_per_100g": protein,
        "fats_per_100g": fats,
        "carbs_per_100g": carbs,
    }


class NameWebSearchStrategy(ResolutionStrategy):
    """Vision food name → Responses-API web_search → OFF re-query (A9).

    Two internal outcome paths (ADR-0002 §6):

    * **Primary / medium confidence**: web search identifies the product by name;
      OFF re-query fetches structured per-100g macros → scaled to portion.
    * **Fallback / low confidence**: OFF re-query misses but web search returned
      usable prose numbers → scaled per-100g, lowest trust.

    Both paths are non-blocking: all failures → ``None`` (fall through to vision).
    Never cached.  Runs after ``LabelOCRStrategy``, before ``VisionFallbackStrategy``.

    ``confidence_tier`` class attribute is the **nominal** tier used for pipeline
    introspection; the actual tier is set dynamically on :class:`ResolutionResult`
    at resolve time (ADR-0002 §6).
    """

    source_id = "name_web"
    confidence_tier = "medium"  # nominal; actual set at resolve time

    async def resolve(
        self,
        signals: ImageSignals,
        db: Session,
    ) -> Optional[ResolutionResult]:
        # Short-circuit: vision result needed for food names + portion estimate.
        if signals.vision_result is None:
            return None

        vision = signals.vision_result
        foods = [
            f for f in (vision.get("foods") or []) if isinstance(f, str) and f.strip()
        ]

        if not foods:
            return None

        # Multi-item gate (mirrors A8/A10): a multi-food plate is unlikely to be
        # a single packaged product → skip to avoid a confident-wrong match.
        if len(foods) > _NAME_SEARCH_MAX_FOODS:
            logger.debug(
                "NameWebSearchStrategy: %d foods (> %d) — skipping web search "
                "(looks like a multi-item plate)",
                len(foods),
                _NAME_SEARCH_MAX_FOODS,
            )
            return None

        name_query = " ".join(foods).strip()
        svc = get_openai_service()

        # --- Phase 1: web search to identify the product + optionally get numbers ---
        try:
            parsed = await analyze_and_log(
                svc.web_search_nutrition(foods),
                kind="web_search",
                input_ref=", ".join(foods),
                telegram_id=signals.telegram_id,
                model=svc.model,
                parse=_parse_web_nutrition_response,
            )
        except Exception as exc:
            # analyze_and_log re-raises after logging; swallow here so the
            # pipeline continues to the next strategy (non-blocking contract).
            logger.warning(
                "NameWebSearchStrategy: web_search_nutrition failed for %r: %s",
                name_query,
                exc,
                exc_info=True,
            )
            return None

        off_query = parsed.get("off_query")
        identification = parsed.get("identification")
        portion_grams = signals.portion_grams

        # --- Phase 2a: OFF re-query for structured numbers (medium confidence) ---
        if off_query:
            try:
                off_svc = OpenFoodFactsService(db)
                off_result = await off_svc.search_by_name(off_query)
            except Exception as exc:
                logger.warning(
                    "NameWebSearchStrategy: OFF re-query raised for %r: %s",
                    off_query,
                    exc,
                    exc_info=True,
                )
                off_result = None

            if off_result is not None:
                nutrition = _scale_off_nutrition(
                    off_result, portion_grams, fallback_food=off_query
                )
                signals_dict: Dict[str, Any] = {
                    "barcode_raw": None,
                    "barcode_detected": signals.barcode is not None,
                    "product_name": off_result.product_name or identification,
                    "brand": off_result.brand,
                    "off_code": off_result.off_code,
                    "off_from_cache": False,  # name search is never cached
                    "off_latency_ms": None,
                    "portion_grams": portion_grams,
                    "confidence_tier": "medium",
                    "strategy_tried": [self.source_id],  # runner updates this
                    "strategy_chosen": self.source_id,
                    "vision_foods": vision.get("foods", []),
                    "vision_portion_raw": vision.get("portion"),
                    "name_query": name_query,
                    "web_identification": identification,
                    "web_off_requery": off_query,
                }
                if signals.barcode is not None:
                    signals_dict["barcode_unresolved"] = signals.barcode
                return ResolutionResult(
                    source=self.source_id,
                    confidence_tier="medium",
                    nutrition=nutrition,
                    description=off_result.product_name or off_query,
                    portion_grams=portion_grams,
                    signals=signals_dict,
                )

        # --- Phase 2b: prose fallback — web numbers at low confidence ---
        calories = parsed.get("calories_per_100g")
        protein = parsed.get("protein_per_100g")
        fats = parsed.get("fats_per_100g")
        carbs = parsed.get("carbs_per_100g")

        if any(v is None for v in (calories, protein, fats, carbs)):
            logger.debug(
                "NameWebSearchStrategy: OFF miss and incomplete prose macros "
                "for %r — falling through to vision",
                name_query,
            )
            return None

        # Web prose numbers are per-100g; reuse _scale_label_nutrition with
        # basis_grams=100 to avoid constructing a fake OFFLookupResult.
        food_list = foods or [identification or name_query]
        nutrition = _scale_label_nutrition(
            calories=float(calories),
            protein=float(protein),
            fats=float(fats),
            carbs=float(carbs),
            basis_grams=100.0,
            portion_grams=portion_grams,
            foods=food_list,
        )

        signals_dict = {
            "barcode_raw": None,
            "barcode_detected": signals.barcode is not None,
            "product_name": identification,
            "brand": None,
            "off_code": None,
            "off_from_cache": False,
            "off_latency_ms": None,
            "portion_grams": portion_grams,
            "confidence_tier": "low",
            "strategy_tried": [self.source_id],  # runner updates this
            "strategy_chosen": self.source_id,
            "vision_foods": vision.get("foods", []),
            "vision_portion_raw": vision.get("portion"),
            "name_query": name_query,
            "web_identification": identification,
            "web_prose_macros": {
                "calories": calories,
                "protein": protein,
                "fats": fats,
                "carbs": carbs,
            },
        }
        if signals.barcode is not None:
            signals_dict["barcode_unresolved"] = signals.barcode

        return ResolutionResult(
            source=self.source_id,
            confidence_tier="low",
            nutrition=nutrition,
            description=identification or name_query,
            portion_grams=portion_grams,
            signals=signals_dict,
        )


class VisionFallbackStrategy(ResolutionStrategy):
    """Wrap the already-computed vision result.  Always last in the pipeline.

    Returns ``None`` only if vision analysis failed (so the runner raises
    RuntimeError — treated as a hard failure by ``_run_meal_analysis``).
    """

    source_id = "vision"
    confidence_tier = "low"

    async def resolve(
        self,
        signals: ImageSignals,
        db: Session,
    ) -> Optional[ResolutionResult]:
        if signals.vision_result is None:
            return None  # pipeline exhausted — caller raises

        nutrition = signals.vision_result  # already parse_nutrition'd

        signals_dict: Dict[str, Any] = {
            "barcode_raw": signals.barcode,
            "barcode_detected": signals.barcode is not None,
            "product_name": None,
            "brand": None,
            "off_code": None,
            "off_from_cache": None,
            "off_latency_ms": None,
            "portion_grams": signals.portion_grams,
            "confidence_tier": self.confidence_tier,
            "strategy_tried": [self.source_id],  # runner updates this
            "strategy_chosen": self.source_id,
            "vision_foods": nutrition.get("foods", []),
            "vision_portion_raw": nutrition.get("portion"),
        }

        return ResolutionResult(
            source=self.source_id,
            confidence_tier=self.confidence_tier,
            nutrition=nutrition,
            description=", ".join(nutrition.get("foods", [])) or None,
            portion_grams=signals.portion_grams,
            signals=signals_dict,
        )


# ---------------------------------------------------------------------------
# SavedFoodRAGStrategy helpers (B3 / ADR-0003)
# ---------------------------------------------------------------------------


def _build_rag_query_text(signals: "ImageSignals") -> Optional[str]:
    """Build the query text for SavedFoodRAGStrategy's embedding call.

    Image path: join vision-read food names (already available in signals).
    Returns None if no usable food names are available (no vision result or
    empty food list) — the strategy then returns None and falls through.
    """
    vision = signals.vision_result or {}
    foods = [
        f for f in (vision.get("foods") or []) if isinstance(f, str) and f.strip()
    ]
    if foods:
        return ", ".join(foods)
    return None


# ---------------------------------------------------------------------------
# B3 strategy: SavedFoodRAGStrategy
# ---------------------------------------------------------------------------


class SavedFoodRAGStrategy(ResolutionStrategy):
    """Personal food DB lookup via embedding ANN (B3 / ADR-0003 §4).

    Two-phase resolve (ADR-0003 §4c):

    Phase A — exact-barcode short-circuit:
      If ``signals.barcode`` is already in ``personal_foods`` for this user,
      serve it directly — no OFF call needed.  Cost: one indexed SQL lookup.

    Phase B — fuzzy ANN over ``personal_food_embeddings``:
      Embed the vision food names and find the nearest neighbour within the
      config-driven cosine-distance threshold (``SAVED_FOOD_SIM_THRESHOLD``).

    Always returns ``medium`` confidence (ADR-0003 §4b); the result is always
    shown as a draft at the confirm step — never silently auto-saved.
    Non-blocking: all failures → None (ADR-0003 §4f).
    """

    source_id = "saved_rag"
    confidence_tier = "medium"

    async def resolve(
        self,
        signals: ImageSignals,
        db: Session,
    ) -> Optional[ResolutionResult]:
        try:
            return await self._resolve(signals, db)
        except Exception:
            logger.warning(
                "SavedFoodRAGStrategy failed; falling through",
                exc_info=True,
            )
            return None

    async def _resolve(
        self,
        signals: ImageSignals,
        db: Session,
    ) -> Optional[ResolutionResult]:
        # Resolve telegram_id → internal DB user_id (mandatory for user-scoped ANN).
        if signals.telegram_id is None:
            return None
        user = crud_user.get_by_telegram_id(db, telegram_id=signals.telegram_id)
        if user is None:
            return None
        user_id: int = user.id

        # Phase A — exact-barcode short-circuit (ADR-0003 §4c)
        if signals.barcode:
            pf = crud_personal_food.get_by_barcode(
                db, barcode=signals.barcode, user_id=user_id
            )
            if pf is not None:
                logger.debug(
                    "SavedFoodRAGStrategy: barcode %r matched personal_food_id=%d",
                    signals.barcode,
                    pf.id,
                )
                return self._build_result(
                    pf=pf,
                    signals_input=signals,
                    distance=0.0,
                    match_source="saved_rag_barcode",
                    query_text=signals.barcode,
                )
            # Barcode not yet in the personal DB → fall through to Phase B / next strategy

        # Phase B — fuzzy ANN (text/vision foods path)
        query_text = _build_rag_query_text(signals)
        if not query_text:
            return None

        svc = get_openai_service()
        embedding = await svc.embed_text(query_text)

        ann_result = crud_personal_food.find_similar(
            db,
            embedding=embedding,
            threshold=settings.SAVED_FOOD_SIM_THRESHOLD,
            user_id=user_id,
        )
        if ann_result is None:
            logger.debug(
                "SavedFoodRAGStrategy: no ANN match above threshold for %r", query_text
            )
            return None

        pf, distance = ann_result
        logger.debug(
            "SavedFoodRAGStrategy: ANN matched personal_food_id=%d "
            "(distance=%.4f) for %r",
            pf.id,
            distance,
            query_text,
        )
        return self._build_result(
            pf=pf,
            signals_input=signals,
            distance=distance,
            match_source="saved_rag",
            query_text=query_text,
        )

    @staticmethod
    def _build_result(
        *,
        pf: PersonalFood,
        signals_input: ImageSignals,
        distance: float,
        match_source: str,
        query_text: str,
    ) -> ResolutionResult:
        """Build a ResolutionResult from a matched PersonalFood row.

        Scales per-100g macros to the vision-estimated portion (same
        pattern as ``_scale_off_nutrition``). Confidence is always ``medium``
        (ADR-0003 §4b).
        """
        portion_grams = signals_input.portion_grams
        vision = signals_input.vision_result or {}

        if portion_grams is not None and portion_grams > 0:
            factor = portion_grams / 100.0
            nutrition: Dict[str, Any] = {
                "calories": round((pf.per_100g_calories or 0) * factor, 1),
                "protein": round((pf.per_100g_proteins or 0) * factor, 1),
                "fats": round((pf.per_100g_fats or 0) * factor, 1),
                "carbs": round((pf.per_100g_carbs or 0) * factor, 1),
                "portion": f"{portion_grams:.0f}г",
                "foods": [pf.canonical_name],
            }
        else:
            nutrition = {
                "calories": pf.per_100g_calories or 0,
                "protein": pf.per_100g_proteins or 0,
                "fats": pf.per_100g_fats or 0,
                "carbs": pf.per_100g_carbs or 0,
                "portion": "100г",
                "foods": [pf.canonical_name],
            }

        signals_dict: Dict[str, Any] = {
            # ADR-0003 §4e — saved-match provenance keys
            "saved_food_id": pf.id,
            "saved_food_name": pf.canonical_name,
            "saved_match_distance": distance,
            "saved_match_source": match_source,
            "query_text": query_text,
            # Standard pipeline fields (mirrors other strategies)
            "barcode_raw": None,  # don't show EAN in reply for personal-DB results
            "barcode_detected": signals_input.barcode is not None,
            "product_name": pf.canonical_name,
            "brand": pf.brand,
            "off_code": None,
            "off_from_cache": False,
            "off_latency_ms": None,
            "portion_grams": portion_grams,
            "confidence_tier": "medium",
            "strategy_tried": ["saved_rag"],  # runner updates this
            "strategy_chosen": "saved_rag",
            "vision_foods": vision.get("foods", []),
            "vision_portion_raw": vision.get("portion"),
        }

        return ResolutionResult(
            source="saved_rag",
            confidence_tier="medium",
            nutrition=nutrition,
            description=pf.canonical_name,
            portion_grams=portion_grams,
            signals=signals_dict,
        )


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------


def _build_pipeline() -> List[ResolutionStrategy]:
    """Ordered strategy list: saved_rag → barcode_off → name_off → … → vision.

    Final order ratified in ADR-0003 §4a (2026-07-10): SavedFoodRAGStrategy
    runs first so a barcoded item already in personal_foods is served without
    an OFF call (exact-barcode short-circuit, owner decision 2026-07-09).
    A barcoded item NOT yet in personal_foods passes through to BarcodeOFFStrategy
    unchanged.
    """
    return [
        SavedFoodRAGStrategy(),  # B3 — personal DB (barcode short-circuit + fuzzy ANN)
        BarcodeOFFStrategy(),  # A4 (round 1) — high confidence
        NameOFFStrategy(),  # A8 (round 2) — medium confidence
        LabelOCRStrategy(),  # A10 (round 2) — medium confidence
        NameWebSearchStrategy(),  # A9 (round 2) — medium/low confidence
        VisionFallbackStrategy(),  # always last — low confidence
    ]


async def resolve_meal_nutrition(
    image_data_url: str,
    *,
    telegram_id: Optional[int] = None,
    input_ref: Optional[str] = None,
) -> ResolutionResult:
    """Entry point for A4.  Returns the best-confidence result available.

    1. Extract image signals (barcode + vision) concurrently.
    2. Run registered strategies in order; return first non-None result.

    Raises ``ModelUnavailableError`` if vision analysis hits a deprecated/missing
    model (so ``_run_meal_analysis`` can offer the owner a replacement).  All
    other failures produce a ``RuntimeError``.

    Parameters
    ----------
    image_data_url:
        Base64 data URL for the photo.
    telegram_id:
        For ai_call_logs attribution.
    input_ref:
        Telegram file_id (or similar) for ai_call_logs ``input_ref`` column.
    """
    with SessionLocal() as db:
        signals = await _extract_signals(
            image_data_url,
            telegram_id=telegram_id,
            input_ref=input_ref,
        )
        pipeline = _build_pipeline()
        tried: List[str] = []
        for strategy in pipeline:
            result = await strategy.resolve(signals, db)
            tried.append(strategy.source_id)
            if result is not None:
                # Annotate with the full tried list (runner has the full picture).
                result.signals["strategy_tried"] = tried
                return result

    # Unreachable — VisionFallbackStrategy always returns a result unless vision
    # itself failed (in which case _extract_signals re-raises ModelUnavailableError
    # or resolve_meal_nutrition raises below).
    raise RuntimeError(
        "Pipeline exhausted without a result (VisionFallbackStrategy returned None — "
        "vision analysis must have failed)"
    )


# ---------------------------------------------------------------------------
# Signal extraction
# ---------------------------------------------------------------------------


async def _extract_signals(
    image_data_url: str,
    *,
    telegram_id: Optional[int],
    input_ref: Optional[str],
) -> ImageSignals:
    """Run barcode extraction and vision analysis concurrently.

    Both calls go through ``analyze_and_log`` so every OpenAI call appears in
    ``ai_call_logs``.  Vision failures that are ``ModelUnavailableError`` are
    re-raised; other exceptions → vision_result=None (pipeline falls to vision
    fallback, which then also returns None → RuntimeError in the runner).
    """
    from app.services.openai_service import ModelUnavailableError

    # Shared process-wide singleton — same instance the bot uses, so a runtime
    # model switch (TD-005) is honoured here too. No import of the handler layer.
    svc = get_openai_service()

    barcode_coro = analyze_and_log(
        svc.extract_barcode_from_image(image_data_url),
        kind="barcode_extraction",
        input_ref=input_ref,
        telegram_id=telegram_id,
        model=svc.model,
        parse=lambda raw: {"barcode": raw},
    )
    vision_coro = analyze_and_log(
        svc.analyze_food_image(image_data_url),
        kind="image",
        input_ref=input_ref,
        telegram_id=telegram_id,
        model=svc.model,
        parse=parse_nutrition,
    )

    barcode_log_result, vision_result = await asyncio.gather(
        barcode_coro, vision_coro, return_exceptions=True
    )

    # --- Barcode -------------------------------------------------------
    if isinstance(barcode_log_result, Exception):
        logger.warning("Barcode extraction failed (non-fatal): %s", barcode_log_result)
        barcode: Optional[str] = None
    else:
        barcode = (barcode_log_result or {}).get("barcode")

    # --- Vision --------------------------------------------------------
    if isinstance(vision_result, ModelUnavailableError):
        raise vision_result  # propagate → _run_meal_analysis offers model picker
    if isinstance(vision_result, Exception):
        logger.error("Vision analysis failed: %s", vision_result, exc_info=True)
        vision_parsed: Optional[dict] = None
    else:
        vision_parsed = vision_result  # already parse_nutrition'd by analyze_and_log

    return ImageSignals(
        image_data_url=image_data_url,
        barcode=barcode,
        vision_result=vision_parsed,
        portion_grams=_parse_portion_grams(vision_parsed),
        telegram_id=telegram_id,
        input_ref=input_ref,
    )


# ---------------------------------------------------------------------------
# Portion helper
# ---------------------------------------------------------------------------


def _parse_portion_grams(vision_result: Optional[dict]) -> Optional[float]:
    """Extract a gram value from the vision result's portion string.

    Examples that should match:
      "1 serving (300g)"  → 300.0
      "200г"              → 200.0
      "около 250 граммов" → 250.0
      "1,5 кг"            → 1500.0
      "150 g"             → 150.0
      "2 cups (480 ml)"   → None  (ml, not grams)
    """
    if not vision_result:
        return None
    portion = vision_result.get("portion") or ""
    if not portion:
        return None
    portion_str = str(portion)

    # Kilograms FIRST, so 'кг'/'kg' isn't consumed by the grams pattern below.
    kg = re.search(
        r"(\d+(?:[.,]\d+)?)\s*(?:кг|kg|килограмм\w*|kilogram\w*)",
        portion_str,
        re.IGNORECASE,
    )
    if kg:
        return float(kg.group(1).replace(",", ".")) * 1000.0

    # Grams: full words ('граммов'/'грамм'/'гр', 'grams'/'gram') and the bare
    # unit ('г'/'g'). The negative lookahead replaces a plain \b (which failed
    # for Cyrillic 'граммов' — 'г' is followed by another word char) and keeps a
    # bare 'г'/'g' from matching inside an unrelated word.
    g = re.search(
        r"(\d+(?:[.,]\d+)?)\s*(?:граммов|грамм|гр|г|grams|gram|g)(?![а-яёa-z])",
        portion_str,
        re.IGNORECASE,
    )
    if g:
        return float(g.group(1).replace(",", "."))
    return None
