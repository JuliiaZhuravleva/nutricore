"""Unit tests for product_lookup_service.py (A4).

Tests the pipeline service in isolation: no real DB (SQLite in-memory),
no real HTTP calls (OFF client mocked), no real OpenAI calls (monkeypatched).

Coverage:
- parse_nutrition (formerly _parse_nutrition in telegram.py)
- _parse_portion_grams
- BarcodeOFFStrategy: barcode found (with/without portion), not found, no barcode
- VisionFallbackStrategy: happy path, vision_result=None
- Pipeline runner (resolve_meal_nutrition): barcode wins, vision fallback, both fail
- strategy_tried tracking in signals
- resolution_source / resolution_signals surfaced correctly
- /reprocess path: image inputs go through the pipeline
"""

import asyncio
import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.services import ai_call_log_service as ai_log
from app.services.open_food_facts_service import OFFLookupResult
from app.services.product_lookup_service import (
    BarcodeOFFStrategy,
    ImageSignals,
    NameOFFStrategy,
    ResolutionResult,
    VisionFallbackStrategy,
    _build_pipeline,
    _parse_portion_grams,
    parse_nutrition,
    resolve_meal_nutrition,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_NUTRITION = {
    "foods": ["Pringles Original"],
    "calories": 524,
    "protein": 6,
    "fats": 30,
    "carbs": 55,
    "portion": "1 tube (150g)",
}

_OFF_RESULT = OFFLookupResult(
    barcode="4607195501226",
    off_code="4607195501226",
    product_name="Чипсы Pringles Original",
    brand="Pringles",
    calories_per_100g=520.0,
    proteins_per_100g=5.5,
    fats_per_100g=31.0,
    carbohydrates_per_100g=53.0,
    raw_data={"code": "4607195501226"},
    from_cache=False,
)


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    with Session() as session:
        yield session
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(autouse=True)
def patch_ai_log_session(monkeypatch):
    """Point ai_call_log_service.SessionLocal at an in-memory SQLite DB."""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(ai_log, "SessionLocal", Session)
    yield
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(autouse=True)
def patch_product_lookup_session(monkeypatch):
    """Point product_lookup_service.SessionLocal at an in-memory SQLite DB."""
    import app.services.product_lookup_service as pls

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(pls, "SessionLocal", Session)
    yield
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(autouse=True)
def default_no_name_search(monkeypatch):
    """Default the A8 name-search to a miss so no test makes a real OFF search
    call; the NameOFFStrategy tests override this with an explicit monkeypatch."""
    monkeypatch.setattr(
        "app.services.product_lookup_service.OpenFoodFactsService.search_by_name",
        AsyncMock(return_value=None),
    )


# ---------------------------------------------------------------------------
# parse_nutrition
# ---------------------------------------------------------------------------


def test_parse_nutrition_accepts_json_string():
    result = parse_nutrition(json.dumps(_NUTRITION))
    assert result["calories"] == 524
    assert result["foods"] == ["Pringles Original"]


def test_parse_nutrition_accepts_dict():
    result = parse_nutrition(dict(_NUTRITION))
    assert result["carbs"] == 55


def test_parse_nutrition_normalizes_string_foods():
    raw = {**_NUTRITION, "foods": "chips"}
    assert parse_nutrition(raw)["foods"] == ["chips"]


def test_parse_nutrition_normalizes_empty_foods():
    raw = {**_NUTRITION, "foods": []}
    assert parse_nutrition(raw)["foods"] == []


def test_parse_nutrition_coerces_non_string_foods():
    raw = {**_NUTRITION, "foods": [{"name": "chips"}, 42]}
    result = parse_nutrition(raw)
    assert all(isinstance(x, str) for x in result["foods"])


def test_parse_nutrition_rejects_null():
    with pytest.raises(ValueError, match="expected a JSON object"):
        parse_nutrition("null")


def test_parse_nutrition_rejects_missing_keys():
    with pytest.raises(ValueError, match="missing keys"):
        parse_nutrition(json.dumps({"foods": []}))


# ---------------------------------------------------------------------------
# _parse_portion_grams
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "portion, expected",
    [
        ("1 serving (300g)", 300.0),
        ("200г", 200.0),
        ("150 g", 150.0),
        ("1 tube (150g)", 150.0),
        ("1 порция (75 г)", 75.0),
        # review M2: spelled-out Russian grams (the \b-based regex missed these)
        ("около 250 грамм", 250.0),
        ("250 граммов", 250.0),
        ("300 гр", 300.0),
        # review M2: kilograms (were unhandled → silently fell to per-100g)
        ("1 кг", 1000.0),
        ("1,5 кг", 1500.0),
        ("0.5 kg", 500.0),
        ("2 cups (480 ml)", None),  # ml, not grams
        ("", None),
        (None, None),
        ("some text without weight", None),
    ],
)
def test_parse_portion_grams(portion, expected):
    vision_result = {"portion": portion} if portion is not None else {"portion": None}
    assert _parse_portion_grams(vision_result) == expected


def test_parse_portion_grams_none_result():
    assert _parse_portion_grams(None) is None


# ---------------------------------------------------------------------------
# BarcodeOFFStrategy
# ---------------------------------------------------------------------------


def test_barcode_off_strategy_no_barcode(db_session):
    """If no barcode in signals → None (next strategy)."""
    signals = ImageSignals(
        image_data_url="data:image/jpeg;base64,abc",
        barcode=None,
        vision_result=_NUTRITION,
        portion_grams=150.0,
    )
    strategy = BarcodeOFFStrategy()
    result = asyncio.run(strategy.resolve(signals, db_session))
    assert result is None


def test_barcode_off_strategy_off_not_found(db_session, monkeypatch):
    """OFF returns None (product not found) → strategy returns None."""
    monkeypatch.setattr(
        "app.services.product_lookup_service.OpenFoodFactsService.lookup",
        AsyncMock(return_value=None),
    )
    signals = ImageSignals(
        image_data_url="data:image/jpeg;base64,abc",
        barcode="4607195501226",
        vision_result=_NUTRITION,
        portion_grams=150.0,
    )
    strategy = BarcodeOFFStrategy()
    result = asyncio.run(strategy.resolve(signals, db_session))
    assert result is None


def test_barcode_off_strategy_hit_with_portion(db_session, monkeypatch):
    """OFF found + vision gave portion → nutrition is scaled."""
    monkeypatch.setattr(
        "app.services.product_lookup_service.OpenFoodFactsService.lookup",
        AsyncMock(return_value=_OFF_RESULT),
    )
    signals = ImageSignals(
        image_data_url="data:image/jpeg;base64,abc",
        barcode="4607195501226",
        vision_result=_NUTRITION,
        portion_grams=150.0,
    )
    strategy = BarcodeOFFStrategy()
    result = asyncio.run(strategy.resolve(signals, db_session))

    assert result is not None
    assert result.source == "barcode_off"
    assert result.confidence_tier == "high"

    # 520 * 1.5 = 780; round(..., 1) applied so allow 0.1 tolerance
    assert result.nutrition["calories"] == pytest.approx(780.0, abs=0.2)
    assert result.nutrition["protein"] == pytest.approx(
        8.25, abs=0.1
    )  # round(8.25,1)→8.2
    assert result.nutrition["fats"] == pytest.approx(46.5, abs=0.2)
    assert result.nutrition["carbs"] == pytest.approx(79.5, abs=0.2)
    assert result.nutrition["portion"] == "150г"
    assert result.nutrition["foods"] == ["Чипсы Pringles Original"]
    assert result.portion_grams == 150.0


def test_barcode_off_strategy_hit_no_portion(db_session, monkeypatch):
    """No vision portion estimate → use per-100g values as-is."""
    monkeypatch.setattr(
        "app.services.product_lookup_service.OpenFoodFactsService.lookup",
        AsyncMock(return_value=_OFF_RESULT),
    )
    signals = ImageSignals(
        image_data_url="data:image/jpeg;base64,abc",
        barcode="4607195501226",
        vision_result=None,
        portion_grams=None,
    )
    strategy = BarcodeOFFStrategy()
    result = asyncio.run(strategy.resolve(signals, db_session))

    assert result is not None
    assert result.nutrition["calories"] == 520.0
    assert result.nutrition["portion"] == "100г"
    assert result.portion_grams is None


def test_barcode_off_strategy_signals_payload(db_session, monkeypatch):
    """Signals dict has all required keys for transparency/misprediction."""
    monkeypatch.setattr(
        "app.services.product_lookup_service.OpenFoodFactsService.lookup",
        AsyncMock(return_value=_OFF_RESULT),
    )
    signals = ImageSignals(
        image_data_url="data:image/jpeg;base64,abc",
        barcode="4607195501226",
        vision_result=_NUTRITION,
        portion_grams=100.0,
    )
    result = asyncio.run(BarcodeOFFStrategy().resolve(signals, db_session))

    s = result.signals
    assert s["barcode_raw"] == "4607195501226"
    assert s["barcode_detected"] is True
    assert s["product_name"] == "Чипсы Pringles Original"
    assert s["brand"] == "Pringles"
    assert s["off_code"] == "4607195501226"
    assert s["off_from_cache"] is False
    assert isinstance(s["off_latency_ms"], int)
    assert s["confidence_tier"] == "high"
    assert s["strategy_chosen"] == "barcode_off"
    assert "vision_foods" in s


def test_barcode_off_strategy_off_exception_returns_none(db_session, monkeypatch):
    """If OFF lookup raises, strategy swallows and returns None."""
    monkeypatch.setattr(
        "app.services.product_lookup_service.OpenFoodFactsService.lookup",
        AsyncMock(side_effect=RuntimeError("network failure")),
    )
    signals = ImageSignals(
        image_data_url="data:image/jpeg;base64,abc",
        barcode="1234567890",
        vision_result=_NUTRITION,
        portion_grams=100.0,
    )
    result = asyncio.run(BarcodeOFFStrategy().resolve(signals, db_session))
    assert result is None


# ---------------------------------------------------------------------------
# NameOFFStrategy (A8)
# ---------------------------------------------------------------------------

_SINGLE_FOOD = {**_NUTRITION, "foods": ["Pringles Original"]}


def _patch_search(monkeypatch, return_value=None, side_effect=None):
    monkeypatch.setattr(
        "app.services.product_lookup_service.OpenFoodFactsService.search_by_name",
        AsyncMock(return_value=return_value, side_effect=side_effect),
    )


def test_name_off_no_vision_foods_returns_none(db_session, monkeypatch):
    """No usable food name → None without touching OFF."""
    search = AsyncMock(return_value=_OFF_RESULT)
    monkeypatch.setattr(
        "app.services.product_lookup_service.OpenFoodFactsService.search_by_name",
        search,
    )
    signals = ImageSignals(
        image_data_url="data:image/jpeg;base64,abc",
        barcode=None,
        vision_result={**_NUTRITION, "foods": []},
        portion_grams=150.0,
    )
    result = asyncio.run(NameOFFStrategy().resolve(signals, db_session))
    assert result is None
    search.assert_not_called()


def test_name_off_too_many_foods_skips_search(db_session, monkeypatch):
    """A multi-item plate (too many foods) is not a packaged product → skip."""
    search = AsyncMock(return_value=_OFF_RESULT)
    monkeypatch.setattr(
        "app.services.product_lookup_service.OpenFoodFactsService.search_by_name",
        search,
    )
    signals = ImageSignals(
        image_data_url="data:image/jpeg;base64,abc",
        barcode=None,
        vision_result={**_NUTRITION, "foods": ["rice", "chicken", "broccoli"]},
        portion_grams=300.0,
    )
    result = asyncio.run(NameOFFStrategy().resolve(signals, db_session))
    assert result is None
    search.assert_not_called()


def test_name_off_hit_scales_and_is_medium(db_session, monkeypatch):
    _patch_search(monkeypatch, return_value=_OFF_RESULT)
    signals = ImageSignals(
        image_data_url="data:image/jpeg;base64,abc",
        barcode=None,
        vision_result=_SINGLE_FOOD,
        portion_grams=150.0,
    )
    result = asyncio.run(NameOFFStrategy().resolve(signals, db_session))

    assert result is not None
    assert result.source == "name_off"
    assert result.confidence_tier == "medium"
    # 520 * 1.5 = 780 (scaled via the shared helper, same as barcode path)
    assert result.nutrition["calories"] == pytest.approx(780.0, abs=0.2)
    assert result.nutrition["portion"] == "150г"
    assert result.nutrition["foods"] == ["Чипсы Pringles Original"]
    assert result.description == "Чипсы Pringles Original"


def test_name_off_search_miss_returns_none(db_session, monkeypatch):
    _patch_search(monkeypatch, return_value=None)
    signals = ImageSignals(
        image_data_url="data:image/jpeg;base64,abc",
        barcode=None,
        vision_result=_SINGLE_FOOD,
        portion_grams=150.0,
    )
    assert asyncio.run(NameOFFStrategy().resolve(signals, db_session)) is None


def test_name_off_search_exception_returns_none(db_session, monkeypatch):
    _patch_search(monkeypatch, side_effect=RuntimeError("network"))
    signals = ImageSignals(
        image_data_url="data:image/jpeg;base64,abc",
        barcode=None,
        vision_result=_SINGLE_FOOD,
        portion_grams=150.0,
    )
    assert asyncio.run(NameOFFStrategy().resolve(signals, db_session)) is None


def test_name_off_signals_payload(db_session, monkeypatch):
    """Name-search signals: barcode_raw kept off (not a barcode result),
    name_query recorded, off_from_cache False, medium tier."""
    _patch_search(monkeypatch, return_value=_OFF_RESULT)
    signals = ImageSignals(
        image_data_url="data:image/jpeg;base64,abc",
        barcode=None,
        vision_result=_SINGLE_FOOD,
        portion_grams=100.0,
    )
    result = asyncio.run(NameOFFStrategy().resolve(signals, db_session))

    s = result.signals
    assert s["barcode_raw"] is None  # keeps the reply's "Штрих-код:" line off
    assert s["barcode_detected"] is False
    assert s["product_name"] == "Чипсы Pringles Original"
    assert s["off_from_cache"] is False
    assert s["confidence_tier"] == "medium"
    assert s["strategy_chosen"] == "name_off"
    assert s["name_query"] == "Pringles Original"
    assert "barcode_unresolved" not in s  # no barcode was present


def test_name_off_records_unresolved_barcode(db_session, monkeypatch):
    """When a barcode was detected but didn't resolve, name-search preserves it
    under barcode_unresolved (for analytics) without surfacing it in the reply."""
    _patch_search(monkeypatch, return_value=_OFF_RESULT)
    signals = ImageSignals(
        image_data_url="data:image/jpeg;base64,abc",
        barcode="4607195501226",
        vision_result=_SINGLE_FOOD,
        portion_grams=100.0,
    )
    result = asyncio.run(NameOFFStrategy().resolve(signals, db_session))

    s = result.signals
    assert s["barcode_raw"] is None  # not surfaced (would misattribute)
    assert s["barcode_detected"] is True
    assert s["barcode_unresolved"] == "4607195501226"


# ---------------------------------------------------------------------------
# VisionFallbackStrategy
# ---------------------------------------------------------------------------


def test_vision_fallback_returns_nutrition(db_session):
    signals = ImageSignals(
        image_data_url="data:image/jpeg;base64,abc",
        barcode=None,
        vision_result=_NUTRITION,
        portion_grams=300.0,
    )
    result = asyncio.run(VisionFallbackStrategy().resolve(signals, db_session))

    assert result is not None
    assert result.source == "vision"
    assert result.confidence_tier == "low"
    assert result.nutrition == _NUTRITION
    assert result.portion_grams == 300.0


def test_vision_fallback_no_vision_returns_none(db_session):
    """If vision analysis failed (vision_result=None) → None (pipeline exhausts)."""
    signals = ImageSignals(
        image_data_url="data:image/jpeg;base64,abc",
        barcode=None,
        vision_result=None,
        portion_grams=None,
    )
    result = asyncio.run(VisionFallbackStrategy().resolve(signals, db_session))
    assert result is None


def test_vision_fallback_signals_barcode_not_detected(db_session):
    """When no barcode found, signals still record barcode_detected=False."""
    signals = ImageSignals(
        image_data_url="data:image/jpeg;base64,abc",
        barcode=None,
        vision_result=_NUTRITION,
        portion_grams=None,
    )
    result = asyncio.run(VisionFallbackStrategy().resolve(signals, db_session))
    assert result.signals["barcode_detected"] is False
    assert result.signals["barcode_raw"] is None


def test_vision_fallback_signals_barcode_detected_but_not_chosen(db_session):
    """Barcode found but pipeline still chose vision → signals reflect that."""
    signals = ImageSignals(
        image_data_url="data:image/jpeg;base64,abc",
        barcode="123456789012",  # barcode present but OFF didn't find it
        vision_result=_NUTRITION,
        portion_grams=None,
    )
    result = asyncio.run(VisionFallbackStrategy().resolve(signals, db_session))
    assert result.signals["barcode_detected"] is True
    assert result.signals["strategy_chosen"] == "vision"


# ---------------------------------------------------------------------------
# resolve_meal_nutrition — integration (with mocked OpenAI + OFF)
# ---------------------------------------------------------------------------


def _make_fake_openai_service(barcode_return, vision_return):
    """Build a minimal fake OpenAIService with async stubs."""
    svc = MagicMock()
    svc.model = "gpt-4o-mini"
    svc.extract_barcode_from_image = AsyncMock(return_value=barcode_return)
    svc.analyze_food_image = AsyncMock(return_value=json.dumps(vision_return))
    return svc


@pytest.fixture()
def mock_telegram_service(monkeypatch):
    """Replace telegram_service.openai_service accessed via lazy import."""

    def _patch(barcode_return, vision_return):
        fake_svc = _make_fake_openai_service(barcode_return, vision_return)
        # product_lookup_service uses a lazy import to avoid circular deps.
        import app.services.product_lookup_service as pls

        with patch.object(
            pls,
            "_extract_signals",
            wraps=lambda *a, **kw: _patched_extract_signals(
                fake_svc, barcode_return, vision_return, *a, **kw
            ),
        ):
            pass

        return fake_svc

    return _patch


async def _build_signals(barcode_return, vision_return, image_data_url):
    """Build ImageSignals directly (bypasses the real OpenAI calls)."""
    from app.services.product_lookup_service import _parse_portion_grams

    vision_parsed = (
        parse_nutrition(json.dumps(vision_return)) if vision_return else None
    )
    return ImageSignals(
        image_data_url=image_data_url,
        barcode=barcode_return,
        vision_result=vision_parsed,
        portion_grams=_parse_portion_grams(vision_parsed),
    )


def test_pipeline_barcode_wins(monkeypatch):
    """When barcode found and OFF has the product, result is barcode_off."""
    import app.services.product_lookup_service as pls

    async def fake_extract(image_data_url, *, telegram_id, input_ref):
        return await _build_signals("4607195501226", _NUTRITION, image_data_url)

    monkeypatch.setattr(pls, "_extract_signals", fake_extract)
    monkeypatch.setattr(
        "app.services.product_lookup_service.OpenFoodFactsService.lookup",
        AsyncMock(return_value=_OFF_RESULT),
    )

    result = asyncio.run(
        resolve_meal_nutrition("data:image/jpeg;base64,abc", telegram_id=42)
    )

    assert result.source == "barcode_off"
    assert result.confidence_tier == "high"
    assert result.signals["strategy_chosen"] == "barcode_off"
    assert result.signals["strategy_tried"] == ["barcode_off"]


def test_pipeline_falls_back_to_vision_when_off_not_found(monkeypatch):
    """Barcode found but OFF has no record → vision fallback."""
    import app.services.product_lookup_service as pls

    async def fake_extract(image_data_url, *, telegram_id, input_ref):
        return await _build_signals("9999999999999", _NUTRITION, image_data_url)

    monkeypatch.setattr(pls, "_extract_signals", fake_extract)
    monkeypatch.setattr(
        "app.services.product_lookup_service.OpenFoodFactsService.lookup",
        AsyncMock(return_value=None),
    )
    # A8 name search also misses → the pipeline continues to the vision fallback.
    monkeypatch.setattr(
        "app.services.product_lookup_service.OpenFoodFactsService.search_by_name",
        AsyncMock(return_value=None),
    )

    result = asyncio.run(
        resolve_meal_nutrition("data:image/jpeg;base64,abc", telegram_id=42)
    )

    assert result.source == "vision"
    assert result.confidence_tier == "low"
    # All three strategies were tried in order before vision won.
    assert result.signals["strategy_tried"] == ["barcode_off", "name_off", "vision"]


def test_pipeline_falls_back_when_no_barcode(monkeypatch):
    """No barcode in image → skip BarcodeOFFStrategy → vision fallback."""
    import app.services.product_lookup_service as pls

    async def fake_extract(image_data_url, *, telegram_id, input_ref):
        return await _build_signals(None, _NUTRITION, image_data_url)

    monkeypatch.setattr(pls, "_extract_signals", fake_extract)

    result = asyncio.run(
        resolve_meal_nutrition("data:image/jpeg;base64,abc", telegram_id=42)
    )

    assert result.source == "vision"
    assert "barcode_off" in result.signals["strategy_tried"]
    assert "vision" in result.signals["strategy_tried"]


def test_pipeline_raises_runtime_error_when_vision_also_fails(monkeypatch):
    """Both barcode (None) and vision (None) → RuntimeError."""
    import app.services.product_lookup_service as pls

    async def fake_extract(image_data_url, *, telegram_id, input_ref):
        return await _build_signals(None, None, image_data_url)

    monkeypatch.setattr(pls, "_extract_signals", fake_extract)

    with pytest.raises(RuntimeError, match="Pipeline exhausted"):
        asyncio.run(
            resolve_meal_nutrition("data:image/jpeg;base64,abc", telegram_id=42)
        )


def test_pipeline_propagates_model_unavailable_error(monkeypatch):
    """ModelUnavailableError from vision → re-raised for self-heal handling."""
    import app.services.product_lookup_service as pls
    from app.services.openai_service import ModelUnavailableError

    err = ModelUnavailableError("gpt-old", Exception("deprecated"))

    async def fake_extract(image_data_url, *, telegram_id, input_ref):
        raise err

    monkeypatch.setattr(pls, "_extract_signals", fake_extract)

    with pytest.raises(ModelUnavailableError):
        asyncio.run(
            resolve_meal_nutrition("data:image/jpeg;base64,abc", telegram_id=42)
        )


def test_build_pipeline_order():
    """Pipeline order locks A8 between the barcode (high) and vision (low)
    strategies so confidence tiers resolve most-trusted first."""
    order = [s.source_id for s in _build_pipeline()]
    assert order == ["barcode_off", "name_off", "vision"]


def test_pipeline_name_off_wins_when_no_barcode(monkeypatch):
    """No barcode, single vision food, OFF name-search hits → name_off wins."""
    import app.services.product_lookup_service as pls

    async def fake_extract(image_data_url, *, telegram_id, input_ref):
        return await _build_signals(None, _SINGLE_FOOD, image_data_url)

    monkeypatch.setattr(pls, "_extract_signals", fake_extract)
    monkeypatch.setattr(
        "app.services.product_lookup_service.OpenFoodFactsService.search_by_name",
        AsyncMock(return_value=_OFF_RESULT),
    )

    result = asyncio.run(
        resolve_meal_nutrition("data:image/jpeg;base64,abc", telegram_id=42)
    )

    assert result.source == "name_off"
    assert result.confidence_tier == "medium"
    assert result.signals["strategy_tried"] == ["barcode_off", "name_off"]
    assert result.signals["strategy_chosen"] == "name_off"


def test_pipeline_name_off_skipped_multi_food_falls_to_vision(monkeypatch):
    """A multi-food plate: name search is skipped, vision fallback wins, and
    search_by_name is never called."""
    import app.services.product_lookup_service as pls

    plate = {**_NUTRITION, "foods": ["rice", "chicken", "broccoli"]}

    async def fake_extract(image_data_url, *, telegram_id, input_ref):
        return await _build_signals(None, plate, image_data_url)

    monkeypatch.setattr(pls, "_extract_signals", fake_extract)
    search = AsyncMock(return_value=_OFF_RESULT)
    monkeypatch.setattr(
        "app.services.product_lookup_service.OpenFoodFactsService.search_by_name",
        search,
    )

    result = asyncio.run(
        resolve_meal_nutrition("data:image/jpeg;base64,abc", telegram_id=42)
    )

    assert result.source == "vision"
    assert result.signals["strategy_tried"] == ["barcode_off", "name_off", "vision"]
    search.assert_not_called()


# ---------------------------------------------------------------------------
# Resolution signals shape — keys required by ADR §5
# ---------------------------------------------------------------------------


def test_barcode_off_result_signals_include_all_adr_keys(monkeypatch):
    """All keys from ADR §5 resolution_signals contract are present."""
    import app.services.product_lookup_service as pls

    async def fake_extract(image_data_url, *, telegram_id, input_ref):
        return await _build_signals("4607195501226", _NUTRITION, image_data_url)

    monkeypatch.setattr(pls, "_extract_signals", fake_extract)
    monkeypatch.setattr(
        "app.services.product_lookup_service.OpenFoodFactsService.lookup",
        AsyncMock(return_value=_OFF_RESULT),
    )

    result = asyncio.run(
        resolve_meal_nutrition("data:image/jpeg;base64,abc", telegram_id=42)
    )

    required_keys = {
        "barcode_raw",
        "barcode_detected",
        "product_name",
        "brand",
        "off_code",
        "off_from_cache",
        "off_latency_ms",
        "portion_grams",
        "confidence_tier",
        "strategy_tried",
        "strategy_chosen",
        "vision_foods",
        "vision_portion_raw",
    }
    assert required_keys.issubset(result.signals.keys())


def test_vision_fallback_signals_include_all_adr_keys(monkeypatch):
    import app.services.product_lookup_service as pls

    async def fake_extract(image_data_url, *, telegram_id, input_ref):
        return await _build_signals(None, _NUTRITION, image_data_url)

    monkeypatch.setattr(pls, "_extract_signals", fake_extract)

    result = asyncio.run(
        resolve_meal_nutrition("data:image/jpeg;base64,abc", telegram_id=42)
    )

    required_keys = {
        "barcode_raw",
        "barcode_detected",
        "confidence_tier",
        "strategy_tried",
        "strategy_chosen",
        "vision_foods",
        "vision_portion_raw",
    }
    assert required_keys.issubset(result.signals.keys())


# ---------------------------------------------------------------------------
# Integration with telegram.py: resolution data stored in draft + MealCreate
# ---------------------------------------------------------------------------


def test_resolution_source_stored_in_draft(monkeypatch):
    """_run_meal_analysis stores resolution_source + resolution_signals in draft."""
    import app.services.product_lookup_service as pls
    from app.services import telegram as tg

    resolution = ResolutionResult(
        source="barcode_off",
        confidence_tier="high",
        nutrition=_NUTRITION,
        description="Чипсы Pringles Original",
        portion_grams=150.0,
        signals={"barcode_raw": "4607195501226", "strategy_chosen": "barcode_off"},
    )
    monkeypatch.setattr(
        pls, "resolve_meal_nutrition", AsyncMock(return_value=resolution)
    )
    monkeypatch.setattr(
        tg, "resolve_meal_nutrition", AsyncMock(return_value=resolution)
    )

    # Minimal fake update / context
    from tests.test_meal_handler import _make_photo_context, _make_photo_update

    update, _ = _make_photo_update()
    context = _make_photo_context()

    state = asyncio.run(
        tg._run_meal_analysis(
            update,
            context,
            kind="image",
            input_ref="PHOTO_FILE_ID",
            payload="data:image/jpeg;base64,abc",
        )
    )

    assert state == tg.CONFIRMING_MEAL
    meal = context.user_data["current_meal"]
    assert meal["resolution_source"] == "barcode_off"
    assert meal["resolution_signals"]["strategy_chosen"] == "barcode_off"


def test_resolution_source_none_for_text_inputs(monkeypatch):
    """Text inputs go through old path; resolution_source absent from draft."""
    from app.services import telegram as tg

    monkeypatch.setattr(
        tg.telegram_service.openai_service,
        "analyze_food_entry",
        AsyncMock(return_value=json.dumps(_NUTRITION)),
    )

    from types import SimpleNamespace

    from tests.test_meal_handler import _make_text_update

    update, _ = _make_text_update("chicken breast 200g")
    context = SimpleNamespace(user_data={})

    state = asyncio.run(
        tg._run_meal_analysis(
            update,
            context,
            kind="text",
            input_ref="chicken breast 200g",
            payload="chicken breast 200g",
        )
    )

    assert state == tg.CONFIRMING_MEAL
    meal = context.user_data["current_meal"]
    # Text path has no resolution_source field in draft
    assert meal.get("resolution_source") is None
