"""Handler tests for the meal-logging conversation (no network, no real DB).

Covers ``process_meal_input`` (photo + text branches, happy paths, foods
normalization, and the analysis-error fallbacks) and ``confirm_meal`` (the
save-to-DB path and the decline path). The owner bypasses ``subscription_required``
before any DB access (``check_subscription`` short-circuits on admin ids), so the
handlers run without a database except where we explicitly wire an in-memory one.
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
from app.crud.crud_app_setting import crud_app_setting
from app.db.base import Base
from app.models.ai_call_log import AiCallLog
from app.models.inbound_message import InboundMessage
from app.models.meal import Meal
from app.services import ai_call_log_service as ai_log
from app.services import inbound_message_service as im_service
from app.services import telegram as tg
from app.services.openai_service import OPENAI_MODEL_SETTING_KEY

_NUTRITION = {
    "foods": ["banana", "oatmeal"],
    "calories": 350,
    "protein": 12,
    "fats": 6,
    "carbs": 60,
    "portion": "1 bowl (300g)",
}
_IMAGE_BYTES = bytes(b"\xff\xd8\xff-fake-jpeg-bytes")


# --- fakes / helpers -------------------------------------------------------


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


# --- fixtures --------------------------------------------------------------


@pytest.fixture(autouse=True)
def _owner_is_admin(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "42")


@pytest.fixture
def image_mock(monkeypatch):
    mock = AsyncMock(return_value=json.dumps(_NUTRITION))
    monkeypatch.setattr(tg.telegram_service.openai_service, "analyze_food_image", mock)
    return mock


@pytest.fixture
def entry_mock(monkeypatch):
    mock = AsyncMock(return_value=json.dumps(_NUTRITION))
    monkeypatch.setattr(tg.telegram_service.openai_service, "analyze_food_entry", mock)
    return mock


@pytest.fixture(autouse=True)
def patched_db(monkeypatch):
    """Point all SessionLocals at a fresh in-memory SQLite DB for every test.

    Autouse so the best-effort ai_call_logs recording (and confirm_meal's save)
    write to a throwaway DB instead of attempting a real connection. Tests that
    inspect the DB request `patched_db` by name to get the engine.
    """
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(tg, "SessionLocal", Session)  # confirm_meal's save
    monkeypatch.setattr(ai_log, "SessionLocal", Session)  # ai_call_logs hook
    monkeypatch.setattr(im_service, "SessionLocal", Session)  # inbound_messages hook
    monkeypatch.setattr(pls_module, "SessionLocal", Session)  # pipeline session
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(autouse=True)
def barcode_mock(monkeypatch):
    """Return None (no barcode) for all photo tests.

    Without this, the pipeline's barcode extraction call hits the real OpenAI
    API (→ 401 with the test key) and leaves an unexpected ai_call_logs row.
    The barcode path is tested separately in test_product_lookup_service.py.
    """
    mock = AsyncMock(return_value=None)
    monkeypatch.setattr(
        tg.telegram_service.openai_service, "extract_barcode_from_image", mock
    )
    return mock


# --- process_meal_input: photo branch --------------------------------------


def test_photo_meal_flow(image_mock):
    update, message = _make_photo_update()
    context = _make_photo_context()

    state = asyncio.run(tg.process_meal_input(update, context))

    assert state == tg.CONFIRMING_MEAL

    # The image goes to OpenAI as a base64 data URL, NOT the token-bearing URL.
    (sent_url,) = image_mock.call_args.args
    assert "api.telegram.org" not in sent_url
    assert (
        sent_url == "data:image/jpeg;base64," + base64.b64encode(_IMAGE_BYTES).decode()
    )

    meal = context.user_data["current_meal"]
    assert meal["nutrition"] == _NUTRITION
    assert meal["description"] == "banana, oatmeal"
    assert meal["photos"] == ["PHOTO_FILE_ID"]

    # Reply shows parsed foods and calories (proves JSON parse + portion key).
    assert "banana, oatmeal" in message.replies[0]
    assert "350" in message.replies[0]


def test_photo_meal_empty_foods_gets_fallback_description(image_mock):
    image_mock.return_value = json.dumps({**_NUTRITION, "foods": []})
    update, _ = _make_photo_update()
    context = _make_photo_context()

    state = asyncio.run(tg.process_meal_input(update, context))

    assert state == tg.CONFIRMING_MEAL
    assert context.user_data["current_meal"]["description"] == "Фото приёма пищи"


def test_photo_meal_foods_as_string_is_normalized(image_mock):
    image_mock.return_value = json.dumps({**_NUTRITION, "foods": "pizza"})
    update, message = _make_photo_update()
    context = _make_photo_context()

    asyncio.run(tg.process_meal_input(update, context))

    assert context.user_data["current_meal"]["description"] == "pizza"
    assert "Продукты: pizza" in message.replies[0]


def test_photo_meal_analysis_error_falls_back(image_mock):
    image_mock.side_effect = RuntimeError("openai 500")
    update, message = _make_photo_update()
    context = _make_photo_context()

    state = asyncio.run(tg.process_meal_input(update, context))

    assert state == tg.ADDING_MEAL
    assert "не удалось проанализировать" in message.replies[-1]
    assert "current_meal" in context.user_data  # nothing half-written into nutrition
    assert "nutrition" not in context.user_data["current_meal"]


def test_photo_meal_invalid_json_falls_back(image_mock):
    image_mock.return_value = "not-json{{"
    update, message = _make_photo_update()
    context = _make_photo_context()

    state = asyncio.run(tg.process_meal_input(update, context))

    assert state == tg.ADDING_MEAL
    assert "не удалось проанализировать" in message.replies[-1]


# --- process_meal_input: text branch ---------------------------------------


def test_text_meal_flow(entry_mock):
    update, message = _make_text_update("chicken breast 200g")
    context = SimpleNamespace(user_data={})

    state = asyncio.run(tg.process_meal_input(update, context))

    assert state == tg.CONFIRMING_MEAL
    (sent_text,) = entry_mock.call_args.args
    assert sent_text == "chicken breast 200g"

    meal = context.user_data["current_meal"]
    assert meal["nutrition"] == _NUTRITION
    assert meal["description"] == "chicken breast 200g"  # text path keeps the raw text
    assert "banana, oatmeal" in message.replies[0]


def test_text_meal_foods_as_string_is_normalized(entry_mock):
    entry_mock.return_value = json.dumps({**_NUTRITION, "foods": "omelette"})
    update, message = _make_text_update("omelette")
    context = SimpleNamespace(user_data={})

    asyncio.run(tg.process_meal_input(update, context))

    assert "Продукты: omelette" in message.replies[0]


def test_text_meal_analysis_error_falls_back(entry_mock):
    entry_mock.side_effect = RuntimeError("openai down")
    update, message = _make_text_update("something")
    context = SimpleNamespace(user_data={})

    state = asyncio.run(tg.process_meal_input(update, context))

    assert state == tg.ADDING_MEAL
    assert "не удалось проанализировать" in message.replies[-1]


# --- confirm_meal ----------------------------------------------------------


def test_confirm_meal_saves_with_photo_and_ai_analysis(patched_db):
    update, message = _make_text_update("Да")
    context = SimpleNamespace(
        user_data={
            "current_meal": {
                "nutrition": _NUTRITION,
                "description": "banana, oatmeal",
                "photos": ["PHOTO_FILE_ID"],
            },
            "meal_time": datetime(2026, 7, 6, 12, 0, tzinfo=UTC),
        }
    )

    state = asyncio.run(tg.confirm_meal(update, context))

    assert state == tg.CHOOSING_ACTION
    assert "сохранен" in message.replies[-1]
    assert context.user_data == {}  # cleared after a successful save

    with sessionmaker(bind=patched_db)() as db:
        meals = db.query(Meal).all()
        assert len(meals) == 1
        assert meals[0].calories == 350
        assert meals[0].photos == ["PHOTO_FILE_ID"]
        assert meals[0].ai_analysis == _NUTRITION


def test_confirm_meal_declined_returns_to_time():
    update, message = _make_text_update("Нет")
    context = SimpleNamespace(user_data={"current_meal": {"nutrition": _NUTRITION}})

    state = asyncio.run(tg.confirm_meal(update, context))

    assert state == tg.ADDING_MEAL_TIME
    assert "ещё раз" in message.replies[-1]
    # Draft is discarded on reject — the retry starts fresh (no stale carryover).
    assert context.user_data["current_meal"] == {}


# --- shared helpers --------------------------------------------------------


def test_parse_nutrition_accepts_json_string_and_dict():
    from_string = tg._parse_nutrition(json.dumps(_NUTRITION))
    from_dict = tg._parse_nutrition(dict(_NUTRITION))
    assert from_string == _NUTRITION
    assert from_dict == _NUTRITION


def test_parse_nutrition_normalizes_foods():
    assert tg._parse_nutrition(json.dumps({**_NUTRITION, "foods": "apple"}))[
        "foods"
    ] == ["apple"]
    assert tg._parse_nutrition(json.dumps({**_NUTRITION, "foods": []}))["foods"] == []
    # Missing foods key → empty list, never a KeyError.
    no_foods = {k: v for k, v in _NUTRITION.items() if k != "foods"}
    assert tg._parse_nutrition(json.dumps(no_foods))["foods"] == []


def test_parse_nutrition_coerces_non_string_foods():
    # A swapped-in model may return foods as dicts/numbers — coerce so the later
    # ", ".join(...) can't raise TypeError.
    data = tg._parse_nutrition(
        json.dumps({**_NUTRITION, "foods": [{"name": "rice"}, 42]})
    )
    assert all(isinstance(x, str) for x in data["foods"])
    assert len(data["foods"]) == 2


def test_nutrition_reply_formats_all_fields():
    reply = tg._nutrition_reply(_NUTRITION, "Заголовок:")
    assert reply.startswith("Заголовок:\n")
    assert "Продукты: banana, oatmeal" in reply
    assert "Калории: 350 ккал" in reply
    assert "Порция: 1 bowl (300g)" in reply
    assert reply.endswith("Всё верно? (Да/Нет)")


# --- _source_badge + _resolution_detail_lines (A5 transparency helpers) ---


def _make_resolution_result(
    source="barcode_off",
    confidence_tier="high",
    portion_grams=150.0,
    signals=None,
):
    """Build a ResolutionResult for badge/detail tests without importing the dataclass."""
    from app.services.product_lookup_service import ResolutionResult

    return ResolutionResult(
        source=source,
        confidence_tier=confidence_tier,
        nutrition=_NUTRITION,
        description="test",
        portion_grams=portion_grams,
        signals=signals
        or {
            "barcode_raw": "4607195501226",
            "product_name": "Чипсы Pringles Original",
        },
    )


def test_source_badge_barcode_off():
    badge = tg._source_badge(_make_resolution_result(source="barcode_off"))
    assert "штрих-коду" in badge
    assert "точно" in badge


def test_source_badge_medium_confidence():
    badge = tg._source_badge(
        _make_resolution_result(source="name_off", confidence_tier="medium")
    )
    assert "нашли в базе" in badge
    assert "проверь" in badge


def test_source_badge_vision():
    badge = tg._source_badge(
        _make_resolution_result(source="vision", confidence_tier="low", signals={})
    )
    assert "фото" in badge


def test_source_badge_unknown_tier_falls_back_to_vision():
    # The taxonomy only emits high/medium/low; an unrecognised tier must fall
    # back to the honest low-confidence vision badge, never an "exact" one
    # (the dead "ambiguous" branch was removed during review cleanup).
    badge = tg._source_badge(
        _make_resolution_result(source="tbd", confidence_tier="ambiguous", signals={})
    )
    assert badge == "📷 оценка по фото"


def test_source_badge_none_returns_empty():
    assert tg._source_badge(None) == ""


def test_resolution_detail_lines_barcode_off_with_portion():
    result = _make_resolution_result(
        source="barcode_off",
        portion_grams=150.0,
        signals={"barcode_raw": "4607195501226", "product_name": "Pringles Original"},
    )
    lines = tg._resolution_detail_lines(result)
    assert any("4607195501226" in l for l in lines)
    assert any("Pringles" in l for l in lines)
    # Portion is known — no warning
    assert not any("не определена" in l for l in lines)


def test_resolution_detail_lines_barcode_off_no_portion_warns():
    result = _make_resolution_result(
        source="barcode_off",
        portion_grams=None,
        signals={"barcode_raw": "1234567890", "product_name": "Test Product"},
    )
    lines = tg._resolution_detail_lines(result)
    assert any("не определена" in l for l in lines)
    assert any("100г" in l for l in lines)


def test_resolution_detail_lines_vision_returns_empty():
    result = _make_resolution_result(source="vision", confidence_tier="low", signals={})
    assert tg._resolution_detail_lines(result) == []


def test_resolution_detail_lines_none_returns_empty():
    assert tg._resolution_detail_lines(None) == []


def test_nutrition_reply_barcode_off_shows_badge_and_ean():
    result = _make_resolution_result(
        source="barcode_off",
        portion_grams=150.0,
        signals={
            "barcode_raw": "4607195501226",
            "product_name": "Чипсы Pringles Original",
        },
    )
    reply = tg._nutrition_reply(_NUTRITION, "Заголовок:", result)
    assert reply.startswith("Заголовок:\n")
    assert "штрих-коду" in reply  # badge
    assert "4607195501226" in reply  # EAN
    assert "Pringles" in reply  # product name
    assert "Продукты: banana, oatmeal" in reply  # nutrition block intact
    assert reply.endswith("Всё верно? (Да/Нет)")


def test_nutrition_reply_vision_shows_source_badge():
    result = _make_resolution_result(source="vision", confidence_tier="low", signals={})
    reply = tg._nutrition_reply(_NUTRITION, "Заголовок:", result)
    assert "фото" in reply
    assert "Продукты: banana, oatmeal" in reply
    assert reply.endswith("Всё верно? (Да/Нет)")


def test_nutrition_reply_no_portion_shows_warning():
    result = _make_resolution_result(
        source="barcode_off",
        portion_grams=None,
        signals={"barcode_raw": "999", "product_name": "Item"},
    )
    reply = tg._nutrition_reply(_NUTRITION, "Заголовок:", result)
    assert "не определена" in reply
    assert "100г" in reply


# --- A6: gram-basis display when OFF per-100g values were scaled (CQ2) -----


def test_resolution_detail_lines_shows_gram_basis_line_when_scaled():
    """A6 CQ2: when scaling was applied, _resolution_detail_lines shows the gram basis."""
    result = _make_resolution_result(
        source="barcode_off",
        portion_grams=150.0,
        signals={"barcode_raw": "4607195501226", "product_name": "Pringles"},
    )
    lines = tg._resolution_detail_lines(result)
    # The gram-basis line must be present.
    gram_lines = [l for l in lines if "150" in l]
    assert gram_lines, "Gram-basis line missing when scaling was applied"
    # It must reference vision estimate origin so user knows this is an estimate.
    assert any(
        "фото" in l for l in gram_lines
    ), "Gram-basis line should indicate the value came from the vision estimate"


def test_resolution_detail_lines_gram_basis_includes_correction_hint():
    """A6: gram-basis line tells the user they can correct the value at confirm step."""
    result = _make_resolution_result(
        source="barcode_off",
        portion_grams=200.0,
        signals={"barcode_raw": "1234", "product_name": "Test"},
    )
    lines = tg._resolution_detail_lines(result)
    gram_lines = [l for l in lines if "200" in l]
    assert gram_lines, "Gram-basis line must include the gram value (200г)"
    assert any(
        "подтверждени" in l for l in gram_lines
    ), "Gram-basis line should hint the user can correct at confirm step"


def test_resolution_detail_lines_gram_basis_not_shown_for_zero():
    """A6: a zero gram value (degenerate case) triggers the per-100g warning instead."""
    result = _make_resolution_result(
        source="barcode_off",
        portion_grams=0.0,
        signals={"barcode_raw": "1234", "product_name": "Test"},
    )
    lines = tg._resolution_detail_lines(result)
    # Zero grams means no valid scaling → treated like None → per-100g warning.
    assert any(
        "не определена" in l for l in lines
    ), "Zero-gram case should fall back to per-100g warning"
    assert not any(
        "Пересчитано" in l for l in lines
    ), "Zero-gram case must not show the scaled gram-basis line"


def test_nutrition_reply_barcode_off_scaled_shows_gram_basis_line():
    """A6: full reply contains the explicit gram-basis scaling line."""
    result = _make_resolution_result(
        source="barcode_off",
        portion_grams=150.0,
        signals={
            "barcode_raw": "4607195501226",
            "product_name": "Чипсы Pringles Original",
        },
    )
    reply = tg._nutrition_reply(_NUTRITION, "Заголовок:", result)
    # Gram-basis line from A6
    assert "150" in reply
    assert "фото" in reply  # vision estimate origin
    assert "подтверждени" in reply  # correction hint
    # Other elements unaffected
    assert "штрих-коду" in reply  # badge
    assert "4607195501226" in reply  # EAN
    assert "Pringles" in reply  # product name
    assert "Продукты: banana, oatmeal" in reply  # nutrition block
    assert reply.endswith("Всё верно? (Да/Нет)")


def test_resolution_detail_lines_no_gram_basis_for_vision_even_with_portion():
    """A6: vision-only results never show the gram-basis line (no OFF scaling used)."""
    result = _make_resolution_result(
        source="vision",
        confidence_tier="low",
        portion_grams=300.0,  # known, but irrelevant — vision path, not scaled
        signals={},
    )
    lines = tg._resolution_detail_lines(result)
    assert lines == [], "Vision-only results must have no intermediate detail lines"


# --- stale-draft reset + atomic write --------------------------------------


def test_add_meal_resets_stale_draft():
    update, _ = _make_text_update("🍽 Добавить прием пищи")
    context = SimpleNamespace(
        user_data={"current_meal": {"nutrition": {"old": 1}}, "meal_time": "stale"}
    )

    state = asyncio.run(tg.add_meal(update, context))

    assert state == tg.ADDING_MEAL_TIME
    assert context.user_data["current_meal"] == {}
    assert "meal_time" not in context.user_data


def test_photo_meal_reply_failure_leaves_no_partial_draft(image_mock):
    update, message = _make_photo_update()
    # The success reply fails to send; the fallback reply then succeeds.
    message.reply_text = AsyncMock(side_effect=[RuntimeError("send failed"), None])
    context = _make_photo_context()

    state = asyncio.run(tg.process_meal_input(update, context))

    assert state == tg.ADDING_MEAL
    # Draft is committed only after a successful reply — nothing half-written.
    assert "nutrition" not in context.user_data["current_meal"]


# --- ai_call_logs recording ------------------------------------------------


def test_photo_meal_records_ai_call(patched_db, image_mock):
    update, _ = _make_photo_update()
    context = _make_photo_context()

    asyncio.run(tg.process_meal_input(update, context))

    with sessionmaker(bind=patched_db)() as db:
        rows = db.query(AiCallLog).all()
    # Pipeline makes 2 concurrent calls: barcode_extraction + image.
    assert len(rows) == 2
    image_rows = [r for r in rows if r.kind == "image"]
    assert len(image_rows) == 1
    row = image_rows[0]
    assert row.kind == "image"
    assert row.status == "ok"
    assert row.input_ref == "PHOTO_FILE_ID"
    assert row.parsed_result == _NUTRITION
    assert row.model == tg.telegram_service.openai_service.model
    assert row.telegram_id == 42
    assert row.latency_ms is not None


def test_text_meal_records_ai_call(patched_db, entry_mock):
    update, _ = _make_text_update("banana")
    context = SimpleNamespace(user_data={})

    asyncio.run(tg.process_meal_input(update, context))

    with sessionmaker(bind=patched_db)() as db:
        rows = db.query(AiCallLog).all()
    assert len(rows) == 1
    assert rows[0].kind == "text"
    assert rows[0].status == "ok"
    assert rows[0].input_ref == "banana"


def test_records_error_status_on_analysis_failure(patched_db, image_mock):
    image_mock.side_effect = RuntimeError("boom")
    update, _ = _make_photo_update()
    context = _make_photo_context()

    asyncio.run(tg.process_meal_input(update, context))

    with sessionmaker(bind=patched_db)() as db:
        rows = db.query(AiCallLog).all()
    # Pipeline: barcode_extraction (ok/None) + image (error).
    assert len(rows) == 2
    error_rows = [r for r in rows if r.status == "error"]
    assert len(error_rows) == 1
    assert error_rows[0].kind == "image"
    assert "boom" in error_rows[0].error
    assert error_rows[0].parsed_result is None


# --- reject → re-log discards the stale draft -------------------------------


def test_reject_then_relog_as_text_drops_stale_photo(patched_db, entry_mock):
    # A photo meal built a draft carrying a photo file_id; the user rejects it
    # and re-logs the meal as text in the same conversation.
    ctx = SimpleNamespace(
        user_data={
            "current_meal": {
                "nutrition": _NUTRITION,
                "description": "banana, oatmeal",
                "photos": ["OLD_PHOTO"],
            }
        }
    )

    # "Нет" → draft discarded, back to time selection.
    reject_update, _ = _make_text_update("Нет")
    assert asyncio.run(tg.confirm_meal(reject_update, ctx)) == tg.ADDING_MEAL_TIME
    assert ctx.user_data["current_meal"] == {}

    # Re-log as text, then confirm-save.
    text_update, _ = _make_text_update("салат")
    asyncio.run(tg.process_meal_input(text_update, ctx))
    save_update, _ = _make_text_update("Да")
    asyncio.run(tg.confirm_meal(save_update, ctx))

    with sessionmaker(bind=patched_db)() as db:
        meals = db.query(Meal).all()
    assert len(meals) == 1
    assert meals[0].photos == []  # the rejected photo did not carry over


# --- _parse_nutrition validation -------------------------------------------


def test_parse_nutrition_rejects_malformed():
    with pytest.raises(ValueError):
        tg._parse_nutrition("null")  # JSON null → not an object
    with pytest.raises(ValueError):
        tg._parse_nutrition(json.dumps({"foods": ["x"]}))  # missing calories/macros


# --- model self-heal on deprecation (TD-005) -------------------------------


@pytest.fixture(autouse=True)
def _restore_model():
    """The OpenAIService is a process-wide singleton; a test that switches its
    model must not leak that into later tests."""
    svc = tg.telegram_service.openai_service
    original = svc.model
    yield
    svc.model = original


def test_model_deprecation_offers_picker(monkeypatch):
    err = tg.ModelUnavailableError("gpt-old", Exception("deprecated"))
    monkeypatch.setattr(
        tg.telegram_service.openai_service,
        "analyze_food_entry",
        AsyncMock(side_effect=err),
    )
    monkeypatch.setattr(
        tg.telegram_service.openai_service,
        "list_suitable_models",
        AsyncMock(return_value=["gpt-4o", "gpt-4o-mini"]),
    )
    update, message = _make_text_update("apple")
    context = SimpleNamespace(user_data={})

    state = asyncio.run(tg.process_meal_input(update, context))

    assert state == tg.CHOOSING_MODEL
    assert context.user_data["pending_analysis"]["payload"] == "apple"
    assert context.user_data["model_choices"] == ["gpt-4o", "gpt-4o-mini"]
    assert any("недоступна" in r for r in message.replies)


def test_model_choice_switches_and_retries(patched_db, monkeypatch):
    svc = tg.telegram_service.openai_service
    # First analysis raises (dead model); after switching, the retry succeeds.
    monkeypatch.setattr(
        svc,
        "analyze_food_entry",
        AsyncMock(
            side_effect=[
                tg.ModelUnavailableError("gpt-old", Exception("x")),
                json.dumps(_NUTRITION),
            ]
        ),
    )
    monkeypatch.setattr(
        svc, "list_suitable_models", AsyncMock(return_value=["gpt-4o", "gpt-4o-mini"])
    )
    context = SimpleNamespace(user_data={})

    # 1) First input trips the picker.
    u1, _ = _make_text_update("apple")
    assert asyncio.run(tg.process_meal_input(u1, context)) == tg.CHOOSING_MODEL

    # 2) Picking a model switches it, persists it, and retries → confirmation.
    u2, _ = _make_text_update("gpt-4o")
    state = asyncio.run(tg.process_model_choice(u2, context))

    assert state == tg.CONFIRMING_MEAL
    assert svc.model == "gpt-4o"
    assert context.user_data["current_meal"]["nutrition"] == _NUTRITION
    assert "pending_analysis" not in context.user_data
    # The choice was persisted to app_settings (survives a restart).
    with sessionmaker(bind=patched_db)() as db:
        assert crud_app_setting.get(db, OPENAI_MODEL_SETTING_KEY) == "gpt-4o"


def test_model_choice_invalid_stays_on_picker():
    context = SimpleNamespace(user_data={"model_choices": ["gpt-4o"]})
    update, message = _make_text_update("garbage")

    state = asyncio.run(tg.process_model_choice(update, context))

    assert state == tg.CHOOSING_MODEL
    assert "кнопкой" in message.replies[-1]


def test_model_choice_cancel_resets():
    context = SimpleNamespace(
        user_data={
            "model_choices": ["gpt-4o"],
            "pending_analysis": {"kind": "text", "input_ref": "a", "payload": "a"},
        }
    )
    update, _ = _make_text_update("Отмена")

    state = asyncio.run(tg.process_model_choice(update, context))

    assert state == tg.CHOOSING_ACTION
    assert context.user_data == {}


def test_load_persisted_model_applies_on_startup(patched_db):
    svc = tg.telegram_service.openai_service
    with tg.SessionLocal() as db:
        crud_app_setting.set(db, OPENAI_MODEL_SETTING_KEY, "gpt-persisted")

    tg._load_persisted_model()

    assert svc.model == "gpt-persisted"


# --- inbound message persistence + reprocess (TD-009) -----------------------


def test_text_meal_records_inbound_analyzed(patched_db, entry_mock):
    update, _ = _make_text_update("banana")
    context = SimpleNamespace(user_data={})

    asyncio.run(tg.process_meal_input(update, context))

    with sessionmaker(bind=patched_db)() as db:
        rows = db.query(InboundMessage).all()
    assert len(rows) == 1
    assert rows[0].kind == "text"
    assert rows[0].content == "banana"
    assert rows[0].telegram_id == 42
    assert rows[0].status == "analyzed"
    assert rows[0].ai_analysis == _NUTRITION


def test_photo_meal_records_inbound_with_file_id(patched_db, image_mock):
    update, _ = _make_photo_update()
    context = _make_photo_context()

    asyncio.run(tg.process_meal_input(update, context))

    with sessionmaker(bind=patched_db)() as db:
        rows = db.query(InboundMessage).all()
    assert len(rows) == 1
    assert rows[0].kind == "image"
    assert rows[0].photo_file_id == "PHOTO_FILE_ID"
    assert rows[0].status == "analyzed"


def test_analysis_failure_marks_inbound_failed(patched_db, entry_mock):
    entry_mock.side_effect = RuntimeError("boom")
    update, _ = _make_text_update("something")
    context = SimpleNamespace(user_data={})

    asyncio.run(tg.process_meal_input(update, context))

    with sessionmaker(bind=patched_db)() as db:
        rows = db.query(InboundMessage).all()
    assert len(rows) == 1
    assert rows[0].status == "failed"
    assert "boom" in rows[0].error


def test_photo_fetch_failure_still_records_inbound(patched_db, image_mock):
    # A photo that never downloads used to vanish entirely; now it leaves a
    # failed row that /reprocess can replay.
    update, _ = _make_photo_update()
    context = _make_photo_context()
    context.bot.get_file = AsyncMock(side_effect=RuntimeError("fetch fail"))

    state = asyncio.run(tg.process_meal_input(update, context))

    assert state == tg.ADDING_MEAL
    with sessionmaker(bind=patched_db)() as db:
        rows = db.query(InboundMessage).all()
    assert len(rows) == 1
    assert rows[0].status == "failed"
    assert "photo fetch failed" in rows[0].error
    assert rows[0].photo_file_id == "PHOTO_FILE_ID"


def test_reprocess_reanalyzes_failed_text(patched_db, entry_mock):
    with sessionmaker(bind=patched_db)() as db:
        db.add(
            InboundMessage(
                telegram_id=42,
                kind="text",
                content="oatmeal",
                status="failed",
                error="old model",
            )
        )
        db.commit()

    update, message = _make_text_update("/reprocess")
    context = SimpleNamespace(user_data={}, bot=SimpleNamespace())

    asyncio.run(tg.reprocess(update, context))

    with sessionmaker(bind=patched_db)() as db:
        rows = db.query(InboundMessage).all()
    assert len(rows) == 1
    assert rows[0].status == "analyzed"
    assert rows[0].ai_analysis == _NUTRITION
    assert "успешно 1" in message.replies[-1]


def test_reply_failure_still_marks_inbound_analyzed(patched_db, image_mock):
    # Analysis succeeds but the confirmation reply fails: the row must read
    # 'analyzed' (not mislabeled 'failed'), else /reprocess re-pays for it.
    update, message = _make_photo_update()
    message.reply_text = AsyncMock(side_effect=[RuntimeError("send failed"), None])
    context = _make_photo_context()

    state = asyncio.run(tg.process_meal_input(update, context))

    assert state == tg.ADDING_MEAL
    with sessionmaker(bind=patched_db)() as db:
        rows = db.query(InboundMessage).all()
    assert len(rows) == 1
    assert rows[0].status == "analyzed"
    assert rows[0].ai_analysis == _NUTRITION
    # Draft still uncommitted — the reply failed, so it stays atomic.
    assert "nutrition" not in context.user_data["current_meal"]


def test_reprocess_records_ai_call_log(patched_db, entry_mock):
    # The replay path goes through analyze_and_log, so a re-analysis is visible in
    # ai_call_logs just like the live flow (not a silent, untraced OpenAI call).
    with sessionmaker(bind=patched_db)() as db:
        db.add(
            InboundMessage(
                telegram_id=42, kind="text", content="oatmeal", status="failed"
            )
        )
        db.commit()

    update, _ = _make_text_update("/reprocess")
    context = SimpleNamespace(user_data={}, bot=SimpleNamespace())

    asyncio.run(tg.reprocess(update, context))

    with sessionmaker(bind=patched_db)() as db:
        logs = db.query(AiCallLog).all()
    assert len(logs) == 1
    assert logs[0].kind == "text"
    assert logs[0].status == "ok"
    assert logs[0].telegram_id == 42


def test_reprocess_empty_queue(patched_db):
    update, message = _make_text_update("/reprocess")
    context = SimpleNamespace(user_data={}, bot=SimpleNamespace())

    asyncio.run(tg.reprocess(update, context))

    assert "Нет сообщений" in message.replies[-1]


def test_reprocess_stops_when_model_still_unavailable(patched_db, monkeypatch):
    with sessionmaker(bind=patched_db)() as db:
        db.add(
            InboundMessage(
                telegram_id=42, kind="text", content="oatmeal", status="failed"
            )
        )
        db.commit()
    monkeypatch.setattr(
        tg.telegram_service.openai_service,
        "analyze_food_entry",
        AsyncMock(side_effect=tg.ModelUnavailableError("gpt-old", Exception("x"))),
    )

    update, message = _make_text_update("/reprocess")
    context = SimpleNamespace(user_data={}, bot=SimpleNamespace())

    asyncio.run(tg.reprocess(update, context))

    assert "всё ещё недоступна" in message.replies[-1]
    with sessionmaker(bind=patched_db)() as db:
        # Left queued (still failed) — not silently dropped.
        assert db.query(InboundMessage).first().status == "failed"
