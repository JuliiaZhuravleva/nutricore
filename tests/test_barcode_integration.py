"""Integration tests for the barcode → Open Food Facts pipeline (A7).

These tests exercise the FULL stack from ``process_meal_input`` / ``reprocess``
through the pluggable pipeline to the Telegram reply — all with mocked I/O:
no real OpenAI calls, no real HTTP calls to OFF, no real database.

Coverage matrix (A7 spec):
  ✓ barcode→OFF lookup (mocked EAN fixtures) — end-to-end from process_meal_input
  ✓ cache hit path (off_from_cache=True in resolution_signals)
  ✓ graceful fallback to vision when barcode not found in OFF
  ✓ chosen-path + signals recording (resolution_source + resolution_signals on draft)
  ✓ confirm_meal persists resolution_source + resolution_signals to DB
  ✓ auto-trigger: barcode found → CONFIRMING_MEAL immediately (no disambiguation)
  ✓ gram-basis display when portion estimate available (scaled "150г" in reply)
  ✓ per-100g warning when no vision portion estimate
  ✓ /reprocess image path through pipeline (barcode→OFF wins)
  ✓ /reprocess image vision fallback (no barcode → vision result used)
  ✓ opt-in gating: existing photo flow still works when barcode=None
"""

import asyncio
import base64
import json
from datetime import UTC, datetime
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
from app.services import telegram as tg
from app.services.open_food_facts_service import OFFLookupResult

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


def _make_photo_update():
    return _make_update(photo=[SimpleNamespace(file_id="PHOTO_FILE_ID")])


def _make_text_update(text):
    return _make_update(text=text)


def _make_photo_context():
    file = SimpleNamespace(
        download_as_bytearray=AsyncMock(return_value=bytearray(_IMAGE_BYTES)),
        file_path="https://api.telegram.org/file/bot<TOKEN>/photos/x.jpg",
    )
    bot = SimpleNamespace(get_file=AsyncMock(return_value=file))
    return SimpleNamespace(bot=bot, user_data={})


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


# ===========================================================================
# 1. End-to-end: barcode→OFF hit → badge + EAN in reply
# ===========================================================================


def test_barcode_photo_flow_shows_badge_and_ean(monkeypatch):
    """process_meal_input → barcode found + OFF hit → badge + EAN in reply."""
    _patch_barcode(monkeypatch, _EAN)
    _patch_vision(monkeypatch, _VISION_NUTRITION_WITH_PORTION)
    _patch_off(monkeypatch, _OFF_RESULT)

    update, message = _make_photo_update()
    context = _make_photo_context()

    state = asyncio.run(tg.process_meal_input(update, context))

    assert state == tg.CONFIRMING_MEAL, f"Expected CONFIRMING_MEAL, got {state}"
    reply = message.replies[0]
    # Badge tells the user the source of the data
    assert "штрих-коду" in reply, "Source badge missing from reply"
    # EAN is surfaced so the user can spot a wrong read
    assert _EAN in reply, "EAN not shown in reply"
    # Product name is shown
    assert "Pringles" in reply, "Product name missing from reply"


def test_barcode_photo_flow_draft_has_resolution_metadata(monkeypatch):
    """process_meal_input → barcode path stores resolution_source in draft."""
    _patch_barcode(monkeypatch, _EAN)
    _patch_vision(monkeypatch, _VISION_NUTRITION_WITH_PORTION)
    _patch_off(monkeypatch, _OFF_RESULT)

    update, _ = _make_photo_update()
    context = _make_photo_context()

    asyncio.run(tg.process_meal_input(update, context))

    meal = context.user_data["current_meal"]
    assert meal["resolution_source"] == "barcode_off"
    signals = meal["resolution_signals"]
    assert signals["barcode_raw"] == _EAN
    assert signals["strategy_chosen"] == "barcode_off"
    assert signals["confidence_tier"] == "high"


# ===========================================================================
# 2. Cache hit path
# ===========================================================================


def test_barcode_photo_flow_cache_hit_reflected_in_signals(monkeypatch):
    """When OFF returns from_cache=True, resolution_signals records the cache hit."""
    _patch_barcode(monkeypatch, _EAN)
    _patch_vision(monkeypatch, _VISION_NUTRITION_WITH_PORTION)
    _patch_off(monkeypatch, _OFF_RESULT_CACHED)

    update, _ = _make_photo_update()
    context = _make_photo_context()

    asyncio.run(tg.process_meal_input(update, context))

    signals = context.user_data["current_meal"]["resolution_signals"]
    assert signals["off_from_cache"] is True, "Cache hit not reflected in signals"


# ===========================================================================
# 3. Graceful fallback to vision when OFF doesn't have the barcode
# ===========================================================================


def test_barcode_found_but_off_miss_falls_back_to_vision(monkeypatch):
    """Barcode read, but OFF has no record → vision fallback + low badge."""
    _patch_barcode(monkeypatch, "9999999999999")  # unknown barcode
    _patch_vision(monkeypatch, _VISION_NUTRITION_WITH_PORTION)
    _patch_off(monkeypatch, None)  # OFF returns not-found

    update, message = _make_photo_update()
    context = _make_photo_context()

    state = asyncio.run(tg.process_meal_input(update, context))

    assert state == tg.CONFIRMING_MEAL
    meal = context.user_data["current_meal"]
    # Falls back to vision when OFF misses
    assert meal["resolution_source"] == "vision"
    # Low-confidence badge ("оценка по фото") shown
    reply = message.replies[0]
    assert "фото" in reply, "Vision fallback badge missing"
    # Both strategies appear in the tried list
    signals = meal["resolution_signals"]
    assert "barcode_off" in signals["strategy_tried"]
    assert "vision" in signals["strategy_tried"]


def test_no_barcode_falls_back_to_vision(monkeypatch):
    """When no barcode in the image, pipeline skips BarcodeOFFStrategy → vision."""
    _patch_barcode(monkeypatch, None)  # no barcode
    _patch_vision(monkeypatch, _VISION_NUTRITION_WITH_PORTION)

    update, message = _make_photo_update()
    context = _make_photo_context()

    state = asyncio.run(tg.process_meal_input(update, context))

    assert state == tg.CONFIRMING_MEAL
    assert context.user_data["current_meal"]["resolution_source"] == "vision"
    assert "фото" in message.replies[0]


# ===========================================================================
# 4. Gram-basis display and portion scaling
# ===========================================================================


def test_barcode_reply_shows_scaled_gram_basis(monkeypatch):
    """When vision gives a portion estimate, the reply shows the scaled gram basis."""
    _patch_barcode(monkeypatch, _EAN)
    _patch_vision(monkeypatch, _VISION_NUTRITION_WITH_PORTION)  # 150g portion
    _patch_off(monkeypatch, _OFF_RESULT)

    update, message = _make_photo_update()
    context = _make_photo_context()

    asyncio.run(tg.process_meal_input(update, context))

    reply = message.replies[0]
    # The portion field should show the scaled value (150г), not per-100g
    assert "150г" in reply, "Scaled portion (150г) missing from reply"
    # No per-100g warning when grams are known
    assert "не определена" not in reply, "Unexpected per-100g warning when grams known"


def test_barcode_reply_warns_per_100g_when_no_portion_estimate(monkeypatch):
    """When vision can't estimate grams, reply warns data is per-100g."""
    _patch_barcode(monkeypatch, _EAN)
    _patch_vision(monkeypatch, _VISION_NUTRITION_NO_PORTION)  # no grams extractable
    _patch_off(monkeypatch, _OFF_RESULT)

    update, message = _make_photo_update()
    context = _make_photo_context()

    asyncio.run(tg.process_meal_input(update, context))

    reply = message.replies[0]
    assert "не определена" in reply, "Per-100g warning missing from reply"
    assert "100г" in reply, "100г basis not mentioned in reply"


# ===========================================================================
# 5. Auto-trigger: no disambiguation needed when pipeline is confident
# ===========================================================================


def test_auto_trigger_goes_directly_to_confirming_meal(monkeypatch):
    """Auto-trigger: barcode detected → pipeline resolves immediately.

    The system must NOT enter DISAMBIGUATING_PRODUCT when the barcode→OFF path
    returns a confident result.  The user receives the reply in the same turn —
    no button press needed.
    """
    _patch_barcode(monkeypatch, _EAN)
    _patch_vision(monkeypatch, _VISION_NUTRITION_WITH_PORTION)
    _patch_off(monkeypatch, _OFF_RESULT)

    update, _ = _make_photo_update()
    context = _make_photo_context()

    state = asyncio.run(tg.process_meal_input(update, context))

    # DISAMBIGUATING_PRODUCT = 7 (defined in telegram.py); must NOT be returned.
    assert state == tg.CONFIRMING_MEAL, (
        f"Expected CONFIRMING_MEAL ({tg.CONFIRMING_MEAL}), got {state} "
        f"(DISAMBIGUATING_PRODUCT={tg.DISAMBIGUATING_PRODUCT})"
    )


# ===========================================================================
# 6. confirm_meal persists resolution columns to the DB
# ===========================================================================


def test_confirm_meal_persists_resolution_source(patched_db):
    """confirm_meal writes resolution_source + resolution_signals to meals table."""
    resolution_signals = {
        "barcode_raw": _EAN,
        "strategy_chosen": "barcode_off",
        "confidence_tier": "high",
    }
    update, message = _make_text_update("Да")
    context = SimpleNamespace(
        user_data={
            "current_meal": {
                "nutrition": _VISION_NUTRITION_WITH_PORTION,
                "description": "Чипсы Pringles Original",
                "photos": ["PHOTO_FILE_ID"],
                "resolution_source": "barcode_off",
                "resolution_signals": resolution_signals,
            },
            "meal_time": datetime(2026, 7, 7, 12, 0, tzinfo=UTC),
        }
    )

    state = asyncio.run(tg.confirm_meal(update, context))

    assert state == tg.CHOOSING_ACTION
    assert "сохранен" in message.replies[-1]

    with sessionmaker(bind=patched_db)() as db:
        meals = db.query(Meal).all()
    assert len(meals) == 1
    saved = meals[0]
    assert saved.resolution_source == "barcode_off"
    assert saved.resolution_signals is not None
    assert saved.resolution_signals["barcode_raw"] == _EAN
    assert saved.resolution_signals["strategy_chosen"] == "barcode_off"


def test_confirm_meal_vision_path_resolution_source_null(patched_db):
    """Vision-path meal has resolution_source=None (no barcode used)."""
    update, _ = _make_text_update("Да")
    context = SimpleNamespace(
        user_data={
            "current_meal": {
                "nutrition": _VISION_NUTRITION_WITH_PORTION,
                "description": "chips",
                "photos": ["PHOTO_FILE_ID"],
                # No resolution_source key → legacy / vision-only
            },
            "meal_time": datetime(2026, 7, 7, 12, 0, tzinfo=UTC),
        }
    )

    asyncio.run(tg.confirm_meal(update, context))

    with sessionmaker(bind=patched_db)() as db:
        meals = db.query(Meal).all()
    assert meals[0].resolution_source is None
    assert meals[0].resolution_signals is None


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


def test_existing_photo_flow_unaffected_when_no_barcode(monkeypatch):
    """The vision-only path (no barcode) is unaffected by the new pipeline code.

    This is the regression guard for A7: barcoded and non-barcoded photo flows
    must coexist without breaking the existing vision-only tests.
    """
    _patch_barcode(monkeypatch, None)  # simulate the barcode_mock fixture behaviour
    _patch_vision(monkeypatch, _VISION_NUTRITION_WITH_PORTION)

    update, message = _make_photo_update()
    context = _make_photo_context()

    state = asyncio.run(tg.process_meal_input(update, context))

    assert state == tg.CONFIRMING_MEAL
    meal = context.user_data["current_meal"]
    assert meal["nutrition"] == _VISION_NUTRITION_WITH_PORTION
    # No OFF badge — vision path shows the photo badge
    assert "фото" in message.replies[0]
    # No resolution data from barcode pipeline
    assert meal["resolution_source"] == "vision"
