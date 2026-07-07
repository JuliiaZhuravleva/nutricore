"""Pluggable meal-nutrition resolution pipeline (A4 / ADR-0001).

Entry point: ``resolve_meal_nutrition(image_data_url, ...)`` — replaces the
direct ``_analyze_and_parse(kind="image")`` call in ``_run_meal_analysis`` for
photo inputs.  Text inputs are unchanged and still go through ``_analyze_and_parse``.

Phase 1 — concurrent signal extraction (barcode + vision).
Phase 2 — ordered strategy resolution; first non-None result wins.

Round-1 strategies:
  BarcodeOFFStrategy   (high confidence, A4)
  VisionFallbackStrategy  (low confidence, always last)

Future strategies (A8/A9/A10) plug in by uncommenting one line in
``_build_pipeline()`` — no other code changes required.
"""

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.services.ai_call_log_service import analyze_and_log, record_ai_call
from app.services.open_food_facts_service import OpenFoodFactsService
from app.db.session import SessionLocal

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


@dataclass
class ResolutionResult:
    """The final nutrition numbers + metadata for one meal entry."""

    source: str  # "barcode_off" | "name_off" | "label_ocr" | "vision"
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
        except Exception as exc:  # pragma: no cover — OFF client already swallows
            logger.warning("BarcodeOFFStrategy: lookup raised: %s", exc)
            off_result = None
        latency_ms = int((time.perf_counter() - started) * 1000)

        if off_result is None:
            return None

        # --- Portion scaling (ADR §7) ------------------------------------
        portion_grams = signals.portion_grams
        if portion_grams is not None and portion_grams > 0:
            factor = portion_grams / 100.0
            nutrition = {
                "calories": round((off_result.calories_per_100g or 0) * factor, 1),
                "protein": round((off_result.proteins_per_100g or 0) * factor, 1),
                "fats": round((off_result.fats_per_100g or 0) * factor, 1),
                "carbs": round((off_result.carbohydrates_per_100g or 0) * factor, 1),
                "portion": f"{portion_grams:.0f}г",
                "foods": (
                    [off_result.product_name]
                    if off_result.product_name
                    else [signals.barcode]
                ),
            }
        else:
            # No vision portion estimate — use per-100g as-is; flagged in signals.
            nutrition = {
                "calories": off_result.calories_per_100g or 0,
                "protein": off_result.proteins_per_100g or 0,
                "fats": off_result.fats_per_100g or 0,
                "carbs": off_result.carbohydrates_per_100g or 0,
                "portion": "100г",
                "foods": (
                    [off_result.product_name]
                    if off_result.product_name
                    else [signals.barcode]
                ),
            }

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
# Pipeline runner
# ---------------------------------------------------------------------------


def _build_pipeline() -> List[ResolutionStrategy]:
    """Ordered strategy list.  A8/A9/A10 insert before VisionFallbackStrategy."""
    return [
        BarcodeOFFStrategy(),  # A4 (round 1)
        # NameOFFStrategy(),         # A8 (round 2, deferred)
        # LabelOCRStrategy(),        # A10 (round 2, deferred)
        # WebSearchStrategy(),       # A9 (round 2, blocked on ADR)
        VisionFallbackStrategy(),  # always last
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
    from app.services.telegram import telegram_service  # lazy — breaks circular import
    from app.services.openai_service import ModelUnavailableError

    svc = telegram_service.openai_service

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
        logger.warning(
            "Barcode extraction failed (non-fatal): %s", barcode_log_result
        )
        barcode: Optional[str] = None
    else:
        barcode = (barcode_log_result or {}).get("barcode")

    # --- Vision --------------------------------------------------------
    if isinstance(vision_result, ModelUnavailableError):
        raise vision_result  # propagate → _run_meal_analysis offers model picker
    if isinstance(vision_result, Exception):
        logger.error(
            "Vision analysis failed: %s", vision_result, exc_info=True
        )
        vision_parsed: Optional[dict] = None
    else:
        vision_parsed = vision_result  # already parse_nutrition'd by analyze_and_log

    return ImageSignals(
        image_data_url=image_data_url,
        barcode=barcode,
        vision_result=vision_parsed,
        portion_grams=_parse_portion_grams(vision_parsed),
    )


# ---------------------------------------------------------------------------
# Portion helper
# ---------------------------------------------------------------------------


def _parse_portion_grams(vision_result: Optional[dict]) -> Optional[float]:
    """Extract a gram value from the vision result's portion string.

    Examples that should match:
      "1 serving (300g)"  → 300.0
      "200г"              → 200.0
      "150 g"             → 150.0
      "2 cups (480 ml)"   → None  (ml, not grams)
    """
    if not vision_result:
        return None
    portion = vision_result.get("portion") or ""
    if not portion:
        return None
    portion_str = str(portion)
    # Match a number immediately followed by 'г' or 'g' (with optional space before unit).
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:г|g)\b", portion_str, re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None
