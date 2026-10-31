"""Integration tests for the barcode → Open Food Facts pipeline (A7).

These tests run the real stack with mocked I/O, from a photo sent through the real
application, or from /reprocess, through the pluggable pipeline to the stored meal:
no real OpenAI calls, no real HTTP calls to OFF, no real database.

  ✓ barcode→OFF lookup (mocked EAN fixtures), end to end
  ✓ cache hit path (off_from_cache=True in resolution_signals)
  ✓ graceful fallback to vision when barcode not found in OFF
  ✓ chosen-path + signals recording (resolution_source + resolution_signals on the row)
  ✓ numbers scaled to the portion read; no portion read still makes a meal
  ✓ /reprocess image path through pipeline (barcode→OFF wins)
  ✓ /reprocess image vision fallback (no barcode → vision result used)
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.services.product_lookup_service as pls_module
from app.core.config import settings
from app.db.base import Base
from app.models.inbound_message import InboundMessage
from app.models.meal import Meal
from app.services import ai_call_log_service as ai_log
from app.services import inbound_message_service as im_service
from app.services import model_selection
from app.services import telegram as tg
from app.services.open_food_facts_service import OFFLookupResult
from tests.test_capture_router import photo, send

# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

_IMAGE_BYTES = bytes(b"\xff\xd8\xff-fake-jpeg-bytes")

# Vision analysis: recognises the product as chips, estimates 150 g portion.
_VISION_NUTRITION_WITH_PORTION = {
    "foods": ["chips"],
    "calories": 500,
    "protein": 5,
    "fats": 28,
    "carbs": 50,
    "portion": "1 упаковка (150g)",
}

# Vision analysis: no clear gram basis in the portion string.
_VISION_NUTRITION_NO_PORTION = {
    "foods": ["chips"],
    "calories": 500,
    "protein": 5,
    "fats": 28,
    "carbs": 50,
    "portion": "по вкусу",  # no grams extractable
}

_EAN = "4607195501226"

_OFF_RESULT = OFFLookupResult(
    barcode=_EAN,
    off_code=_EAN,
    product_name="Чипсы Pringles Original",
    brand="Pringles",
    calories_per_100g=520.0,
    proteins_per_100g=5.5,
    fats_per_100g=31.0,
    carbohydrates_per_100g=53.0,
    raw_data={"code": _EAN},
    from_cache=False,
)

_OFF_RESULT_CACHED = OFFLookupResult(
    barcode=_EAN,
    off_code=_EAN,
    product_name="Чипсы Pringles Original",
    brand="Pringles",
    calories_per_100g=520.0,
    proteins_per_100g=5.5,
    fats_per_100g=31.0,
    carbohydrates_per_100g=53.0,
    raw_data={"code": _EAN},
    from_cache=True,  # returned from DB cache
)


# ---------------------------------------------------------------------------
# Helpers (mirrors test_meal_handler.py helpers; kept local to avoid coupling)
# ---------------------------------------------------------------------------


class _FakeMessage:
    def __init__(self, *, photo=None, text=None, caption=None):
        self.photo = photo
        self.text = text
        self.caption = caption
        self.from_user = SimpleNamespace(id=42, username="owner")
        self.replies = []

    async def reply_text(self, text, *args, **kwargs):
        self.replies.append(text)


def _make_update(*, photo=None, text=None, caption=None):
    message = _FakeMessage(photo=photo, text=text, caption=caption)
    update = SimpleNamespace(
        message=message,
        effective_user=SimpleNamespace(id=42, username="owner"),
    )
    return update, message


def _make_text_update(text):
    return _make_update(text=text)


def _make_reprocess_context(image_bytes=None):
    """Context for /reprocess — needs a bot that can download photos."""
    ib = image_bytes or _IMAGE_BYTES
    file = SimpleNamespace(
        download_as_bytearray=AsyncMock(return_value=bytearray(ib)),
    )
    return SimpleNamespace(
        user_data={},
        bot=SimpleNamespace(get_file=AsyncMock(return_value=file)),
    )


# ---------------------------------------------------------------------------
# Module-level fixtures (all tests in this file need these)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _owner_is_admin(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "42")
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:fake")
    monkeypatch.setattr(tg, "_schedule_personal_food_save", lambda **_: None)


@pytest.fixture(autouse=True)
def patched_db(monkeypatch):
    """Wire all SessionLocals to an in-memory SQLite DB for every test."""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(tg, "SessionLocal", Session)
    monkeypatch.setattr(ai_log, "SessionLocal", Session)
    monkeypatch.setattr(im_service, "SessionLocal", Session)
    monkeypatch.setattr(pls_module, "SessionLocal", Session)
    monkeypatch.setattr(model_selection, "SessionLocal", Session)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(autouse=True)
def _restore_model():
    """Singleton guard: restore the OpenAI model after tests that switch it."""
    svc = tg.telegram_service.openai_service
    original = svc.model
    yield
    svc.model = original


# ---------------------------------------------------------------------------
# Helpers for patching the two OpenAI calls in _extract_signals
# ---------------------------------------------------------------------------


def _patch_barcode(monkeypatch, return_value):
    monkeypatch.setattr(
        tg.telegram_service.openai_service,
        "extract_barcode_from_image",
        AsyncMock(return_value=return_value),
    )


def _patch_vision(monkeypatch, nutrition_dict):
    monkeypatch.setattr(
        tg.telegram_service.openai_service,
        "analyze_food_image",
        AsyncMock(return_value=json.dumps(nutrition_dict)),
    )


def _patch_off(monkeypatch, return_value):
    monkeypatch.setattr(
        "app.services.product_lookup_service.OpenFoodFactsService.lookup",
        AsyncMock(return_value=return_value),
    )


def _patch_search(monkeypatch, return_value):
    monkeypatch.setattr(
        "app.services.product_lookup_service.OpenFoodFactsService.search_by_name",
        AsyncMock(return_value=return_value),
    )


@pytest.fixture(autouse=True)
def _default_no_name_search(monkeypatch):
    """A8 inserted a name-search strategy that runs whenever the barcode path
    misses.  Default it to a miss so the pre-A8 integration tests exercise the
    barcode→vision fallback they were written for (and make no real OFF search
    call).  Tests that specifically exercise name_off override via _patch_search.
    """
    _patch_search(monkeypatch, None)


@pytest.fixture(autouse=True)
def _default_no_label_ocr(monkeypatch):
    """A10 LabelOCRStrategy calls extract_nutrition_label (OpenAI vision) whenever
    the pipeline reaches it.  Default to a null-basis miss so all pre-A10 barcode
    integration tests make no real OpenAI calls and the pipeline falls through to
    vision as originally written.  The label-OCR path is tested directly in
    test_product_lookup_service.py.
    """
    import json

    import app.services.openai_service as ois

    monkeypatch.setattr(
        ois.OpenAIService,
        "extract_nutrition_label",
        AsyncMock(return_value=json.dumps({"basis": None})),
    )


@pytest.fixture(autouse=True)
def _default_no_saved_food(monkeypatch):
    """SavedFoodRAGStrategy asks the real embeddings client once the person has a
    row, and the router gives everyone who writes one.  It is tested directly in
    test_product_lookup_service.py.
    """
    from app.services.product_lookup_service import SavedFoodRAGStrategy

    monkeypatch.setattr(SavedFoodRAGStrategy, "resolve", AsyncMock(return_value=None))


@pytest.fixture(autouse=True)
def _default_no_web_search(monkeypatch):
    """A9 NameWebSearchStrategy makes a Responses-API call whenever the pipeline
    reaches it.  Stub the entire resolve() so no real call is made and no
    ai_call_logs row is added.  The name-web path is tested directly in
    test_product_lookup_service.py.
    """
    from app.services.product_lookup_service import NameWebSearchStrategy

    monkeypatch.setattr(NameWebSearchStrategy, "resolve", AsyncMock(return_value=None))


# ===========================================================================
# 1. End-to-end: a photo through the real application and the real pipeline
# ===========================================================================


def _photo_meal(patched_db) -> Meal:
    """One photo through the application; the meal it left, read back from the row."""
    wire = send(photo())
    assert wire.seen()[-1].startswith("Записано"), wire.seen()
    with sessionmaker(bind=patched_db)() as db:
        (meal,) = db.query(Meal).all()
        db.expunge(meal)
    return meal


def test_barcode_photo_keeps_the_product_and_how_it_was_found(patched_db, monkeypatch):
    _patch_barcode(monkeypatch, _EAN)
    _patch_vision(monkeypatch, _VISION_NUTRITION_WITH_PORTION)
    _patch_off(monkeypatch, _OFF_RESULT)

    meal = _photo_meal(patched_db)

    assert meal.resolution_source == "barcode_off"
    assert "Pringles" in meal.description
    signals = meal.resolution_signals
    assert signals["barcode_raw"] == _EAN
    assert signals["strategy_chosen"] == "barcode_off"
    assert signals["confidence_tier"] == "high"


# ===========================================================================
# 2. Cache hit path
# ===========================================================================


def test_barcode_photo_cache_hit_reflected_in_signals(patched_db, monkeypatch):
    _patch_barcode(monkeypatch, _EAN)
    _patch_vision(monkeypatch, _VISION_NUTRITION_WITH_PORTION)
    _patch_off(monkeypatch, _OFF_RESULT_CACHED)

    meal = _photo_meal(patched_db)

    assert meal.resolution_signals["off_from_cache"] is True


# ===========================================================================
# 3. Graceful fallback to vision when OFF doesn't have the barcode
# ===========================================================================


def test_barcode_found_but_off_miss_falls_back_to_vision(patched_db, monkeypatch):
    _patch_barcode(monkeypatch, "9999999999999")  # unknown barcode
    _patch_vision(monkeypatch, _VISION_NUTRITION_WITH_PORTION)
    _patch_off(monkeypatch, None)  # OFF returns not-found

    meal = _photo_meal(patched_db)

    assert meal.resolution_source == "vision"
    tried = meal.resolution_signals["strategy_tried"]
    assert "barcode_off" in tried
    assert "vision" in tried


def test_no_barcode_falls_back_to_vision(patched_db, monkeypatch):
    """The vision-only path coexists with the barcoded one."""
    _patch_barcode(monkeypatch, None)
    _patch_vision(monkeypatch, _VISION_NUTRITION_WITH_PORTION)

    meal = _photo_meal(patched_db)

    assert meal.resolution_source == "vision"
    assert meal.calories == _VISION_NUTRITION_WITH_PORTION["calories"]


# ===========================================================================
# 4. Portion scaling, and the case with no portion to scale to
# ===========================================================================


def test_barcode_numbers_are_scaled_to_the_portion_read(patched_db, monkeypatch):
    _patch_barcode(monkeypatch, _EAN)
    _patch_vision(monkeypatch, _VISION_NUTRITION_WITH_PORTION)  # 150g portion
    _patch_off(monkeypatch, _OFF_RESULT)  # 520 kcal per 100 g

    meal = _photo_meal(patched_db)

    assert meal.calories == 780.0
    assert meal.nutrients["portion"] == "150г"


def test_barcode_with_no_portion_read_still_makes_a_meal(patched_db, monkeypatch):
    _patch_barcode(monkeypatch, _EAN)
    _patch_vision(monkeypatch, _VISION_NUTRITION_NO_PORTION)  # no grams extractable
    _patch_off(monkeypatch, _OFF_RESULT)

    meal = _photo_meal(patched_db)

    assert meal.resolution_source == "barcode_off"
    assert meal.reading_status == "read"


# ===========================================================================
# 7. /reprocess image path through the pipeline
# ===========================================================================


def _seed_failed_image(patched_db, photo_file_id="OLD_PHOTO_ID"):
    """Add a failed image inbound message to the test DB."""
    with sessionmaker(bind=patched_db)() as db:
        db.add(
            InboundMessage(
                telegram_id=42,
                kind="image",
                photo_file_id=photo_file_id,
                status="failed",
                error="old model",
            )
        )
        db.commit()


def test_reprocess_image_barcode_path_marks_analyzed(patched_db, monkeypatch):
    """/reprocess image → pipeline finds barcode → OFF hit → marks row analyzed."""
    _seed_failed_image(patched_db)
    _patch_barcode(monkeypatch, _EAN)
    _patch_vision(monkeypatch, _VISION_NUTRITION_WITH_PORTION)
    _patch_off(monkeypatch, _OFF_RESULT)

    update, message = _make_text_update("/reprocess")
    context = _make_reprocess_context()

    asyncio.run(tg.reprocess(update, context))

    with sessionmaker(bind=patched_db)() as db:
        rows = db.query(InboundMessage).all()
    assert len(rows) == 1
    assert rows[0].status == "analyzed"
    assert rows[0].ai_analysis is not None
    # ai_analysis comes from resolution_result.nutrition (barcode_off, scaled)
    assert rows[0].ai_analysis.get("portion") == "150г"  # portion was scaled
    assert "успешно 1" in message.replies[-1]


def test_reprocess_image_vision_fallback_marks_analyzed(patched_db, monkeypatch):
    """/reprocess image → no barcode → vision fallback → marks row analyzed."""
    _seed_failed_image(patched_db)
    _patch_barcode(monkeypatch, None)  # no barcode in image
    _patch_vision(monkeypatch, _VISION_NUTRITION_WITH_PORTION)

    update, message = _make_text_update("/reprocess")
    context = _make_reprocess_context()

    asyncio.run(tg.reprocess(update, context))

    with sessionmaker(bind=patched_db)() as db:
        rows = db.query(InboundMessage).all()
    assert rows[0].status == "analyzed"
    assert "успешно 1" in message.replies[-1]


def test_reprocess_image_off_miss_still_analyzes_via_vision(patched_db, monkeypatch):
    """/reprocess image: barcode found but OFF misses → vision result saved."""
    _seed_failed_image(patched_db)
    _patch_barcode(monkeypatch, "9999999999999")
    _patch_vision(monkeypatch, _VISION_NUTRITION_WITH_PORTION)
    _patch_off(monkeypatch, None)  # OFF not found

    update, message = _make_text_update("/reprocess")
    context = _make_reprocess_context()

    asyncio.run(tg.reprocess(update, context))

    with sessionmaker(bind=patched_db)() as db:
        rows = db.query(InboundMessage).all()
    assert rows[0].status == "analyzed"


# ===========================================================================
# 8. Opt-in gating: existing photo flow still works when barcode=None
# ===========================================================================
