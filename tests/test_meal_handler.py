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
from unittest.mock import AsyncMock, patch

import pytest
from openai.types import CompletionUsage
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.services.product_lookup_service as pls_module
from app.core.config import settings
from app.crud.crud_app_setting import crud_app_setting
from app.db.base import Base
from app.models.ai_call_log import AiCallLog
from app.models.inbound_message import InboundMessage
from app.models.meal import Meal
from app.models.user import User
from app.services import ai_call_log_service as ai_log
from app.services import inbound_message_service as im_service
from app.services import loader as loader_mod
from app.services import meal_draft
from app.services import model_selection as ms
from app.services import telegram as tg
from app.services.model_selection import OPENAI_MODEL_SETTING_KEY
from app.services.openai_service import ModelUnavailableError
from app.services.usage_meter import record as record_usage
from tests.test_ai_errors import CREDITS_BODY, QUOTA_BODY, RATE_BODY, sdk_error

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
        self.reply_markups = []

    async def reply_text(self, text, *args, **kwargs):
        self.replies.append(text)
        self.reply_markups.append(kwargs.get("reply_markup"))


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
    monkeypatch.setattr(ms, "SessionLocal", Session)  # model override persist/load
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


@pytest.fixture(autouse=True)
def name_search_mock(monkeypatch):
    """Return None (no name match) for the A8 name-search strategy in all photo
    tests.

    The autouse ``barcode_mock`` above makes the barcode path miss, so the
    pipeline now reaches ``NameOFFStrategy``; without this its ``search_by_name``
    hits the real OFF API (nondeterministic network). The name-search path is
    tested directly in test_product_lookup_service.py / test_open_food_facts_service.py.
    """
    mock = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "app.services.product_lookup_service.OpenFoodFactsService.search_by_name",
        mock,
    )
    return mock


@pytest.fixture(autouse=True)
def label_ocr_mock(monkeypatch):
    """Return a null-basis response for the A10 label-OCR strategy in all photo
    tests so it falls through to vision without making real OpenAI calls.

    The label-OCR path is tested directly in test_product_lookup_service.py.
    The mock goes through analyze_and_log and adds one ``label_ocr`` row to
    ai_call_logs (expected by tests that count logged rows).
    """
    import json

    import app.services.openai_service as ois

    mock = AsyncMock(return_value=json.dumps({"basis": None}))
    monkeypatch.setattr(ois.OpenAIService, "extract_nutrition_label", mock)
    return mock


@pytest.fixture(autouse=True)
def web_search_mock(monkeypatch):
    """Return None (no match) for the A9 NameWebSearchStrategy in all photo tests.

    Mocked at the strategy level so no Responses-API call is made and no extra
    ai_call_logs row is written.  The A9 path is tested directly in
    test_product_lookup_service.py.
    """
    from app.services.product_lookup_service import NameWebSearchStrategy

    mock = AsyncMock(return_value=None)
    monkeypatch.setattr(NameWebSearchStrategy, "resolve", mock)
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

    # Reply shows parsed foods and portion (proves JSON parse + portion key);
    # calories wait behind «🔢 Цифры» by default (stage 5, G5-R01).
    assert "banana, oatmeal" in message.replies[0]
    assert "1 bowl (300g)" in message.replies[0]
    assert "350" not in message.replies[0]


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
    assert "Не получилось разобрать" in message.replies[-1]
    assert "current_meal" in context.user_data  # nothing half-written into nutrition
    assert "nutrition" not in context.user_data["current_meal"]


def test_photo_meal_invalid_json_falls_back(image_mock):
    image_mock.return_value = "not-json{{"
    update, message = _make_photo_update()
    context = _make_photo_context()

    state = asyncio.run(tg.process_meal_input(update, context))

    assert state == tg.ADDING_MEAL
    assert "Не получилось разобрать" in message.replies[-1]


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
    assert "Не получилось разобрать" in message.replies[-1]


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
    assert "✅ Записано" in message.replies[-1]
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


# --- confirm_meal: TD-015 (buttons + intent classification) ----------------


def test_confirm_intent_classifies():
    # Buttons and their tolerant spellings all resolve to affirm/reject; the
    # lowercase/punctuated variants are exactly what used to fall into reject.
    for t in ("Да", "да", "да.", " ДА ", "ага", "верно", "ок", "👍"):
        assert tg._confirm_intent(t) == "affirm", t
    for t in ("Нет", "нет", "нет!", "❌"):
        assert tg._confirm_intent(t) == "reject", t
    for t in ("Отмена", "отмена", "Отмена.", "cancel", "✖️ Отмена"):
        assert tg._confirm_intent(t) == "cancel", t
    for t in ("это была индейка", "порция побольше", "не курица", "", None):
        assert tg._confirm_intent(t) == "correction", t


def _button_rows(markup):
    return [[b.text for b in row] for row in markup.inline_keyboard]


def test_confirm_prompt_carries_inline_buttons(entry_mock):
    # The confirm prompt ships its actions under the message (Notion «поправить»
    # task): save, save to favorites, fix the weight, fix the product, cancel.
    update, message = _make_text_update("chicken breast 200g")
    context = SimpleNamespace(user_data={})

    state = asyncio.run(tg.process_meal_input(update, context))

    assert state == tg.CONFIRMING_MEAL
    markup = message.reply_markups[-1]
    assert _button_rows(markup) == [
        ["✏️ Поправить вес", "✏️ Поправить продукт"],
        ["✅ Сохранить", "⭐ И в избранное"],
        ["🔢 Цифры", "✖️ Отмена"],
    ]
    draft_id = context.user_data["current_meal"]["id"]
    datas = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert all(d.startswith(f"meal:{draft_id}:") for d in datas)
    # The one green button (Bot API ``style``) is the one that records the meal.
    save = markup.inline_keyboard[1][0]
    assert save.to_dict()["style"] == "success"
    assert "style" not in markup.inline_keyboard[1][1].to_dict()


def test_confirm_meal_lowercase_da_saves(patched_db):
    # A lowercase "да" used to fall into the reject branch and wipe the draft;
    # now it saves like the button.
    update, message = _make_text_update("да")
    context = SimpleNamespace(
        user_data={
            "current_meal": {
                "nutrition": _NUTRITION,
                "description": "banana, oatmeal",
                "photos": ["PHOTO_FILE_ID"],
            },
            "meal_time": datetime(2026, 7, 8, 12, 0, tzinfo=UTC),
        }
    )

    state = asyncio.run(tg.confirm_meal(update, context))

    assert state == tg.CHOOSING_ACTION
    assert "✅ Записано" in message.replies[-1]
    with sessionmaker(bind=patched_db)() as db:
        assert db.query(Meal).count() == 1


def test_confirm_meal_cancel_drops_the_meal_and_goes_to_the_menu(patched_db):
    # Julia, 2026-10-28: «Отмена» under a draft asked «Когда был приём пищи?»
    # again; now it ends like the draft's «✖️ Отмена» button.
    bot = SimpleNamespace(edit_message_reply_markup=AsyncMock())
    update, message = _make_text_update("Отмена")
    context = SimpleNamespace(
        bot=bot,
        user_data={
            "current_meal": {"nutrition": _NUTRITION, "msg_id": 5, "chat_id": 42},
            "meal_time": datetime(2026, 7, 8, 12, 0, tzinfo=UTC),
        },
    )

    state = asyncio.run(tg.confirm_meal(update, context))

    assert state == tg.CHOOSING_ACTION
    assert message.replies == ["Отменено."]
    assert message.reply_markups == [tg.main_keyboard]
    assert context.user_data == {}
    bot.edit_message_reply_markup.assert_awaited_once()  # old buttons gone
    with sessionmaker(bind=patched_db)() as db:
        assert db.query(Meal).count() == 0


def test_confirm_meal_no_still_asks_again():
    # «Нет» keeps its meaning: the analysis was wrong, start the entry over.
    update, message = _make_text_update("Нет")
    context = SimpleNamespace(user_data={"current_meal": {"nutrition": _NUTRITION}})

    state = asyncio.run(tg.confirm_meal(update, context))

    assert state == tg.ADDING_MEAL_TIME
    assert context.user_data["current_meal"] == {}
    assert "Когда был приём пищи?" in message.replies[-1]


def test_confirm_meal_free_text_reanalyzes_keeps_flow(entry_mock):
    # THE TD-015 bug: a typed correction in reply to the confirm prompt used to
    # silently discard the analyzed draft and restart. It must now re-analyze the
    # correction together with what came before and stay in the confirm step.
    entry_mock.return_value = json.dumps({**_NUTRITION, "foods": ["turkey"]})
    meal_time = datetime(2026, 7, 8, 19, 0, tzinfo=UTC)
    update, message = _make_text_update("это была индейка, не курица")
    context = SimpleNamespace(
        user_data={
            "current_meal": {"nutrition": _NUTRITION, "description": "chicken"},
            "meal_time": meal_time,
        }
    )

    state = asyncio.run(tg.confirm_meal(update, context))

    # Re-analyzed the old description plus the correction, stayed at confirm.
    assert state == tg.CONFIRMING_MEAL
    (sent_text,) = entry_mock.call_args.args
    assert sent_text == "chicken\nУточнение: это была индейка, не курица"

    meal = context.user_data["current_meal"]
    assert meal["nutrition"]["foods"] == ["turkey"]  # fresh analysis replaced it
    assert meal["description"] == sent_text
    # meal_time (draft context) is preserved.
    assert context.user_data["meal_time"] == meal_time
    assert _button_rows(message.reply_markups[-1])[-1] == ["🔢 Цифры", "✖️ Отмена"]


def test_correcting_a_photo_draft_reanalyzes_the_same_photo(image_mock):
    # Notion «поправить продукт»: an error is fixed without sending the photo
    # again — the same photo goes back with the correction in its caption.
    image_mock.return_value = json.dumps({**_NUTRITION, "foods": ["самса"]})
    update, message = _make_text_update("это самса с картошкой")
    context = _make_photo_context()
    context.user_data.update(
        current_meal={
            "nutrition": _NUTRITION,
            "description": "pie",
            "photos": ["OLD_PHOTO"],
            "caption": "обед",
            "resolution_source": "vision",
        },
        meal_time=datetime(2026, 7, 8, 19, 0, tzinfo=UTC),
    )

    state = asyncio.run(tg.confirm_meal(update, context))

    assert state == tg.CONFIRMING_MEAL
    context.bot.get_file.assert_awaited_once_with("OLD_PHOTO")
    image_mock.assert_awaited_once()
    meal = context.user_data["current_meal"]
    assert meal["photos"] == ["OLD_PHOTO"]
    assert meal["nutrition"]["foods"] == ["самса"]
    # The original caption stays first (a favorite is named by it).
    assert meal["caption"] == "обед\nэто самса с картошкой"
    assert meal["caption"] in image_mock.call_args.args + tuple(
        image_mock.call_args.kwargs.values()
    )


def test_photo_correction_falls_back_to_text_when_the_photo_is_gone(entry_mock):
    # No dead end: if the photo cannot be fetched again, the correction still
    # goes through as a text re-analysis.
    update, message = _make_text_update("это была индейка")
    context = SimpleNamespace(
        user_data={
            "current_meal": {
                "nutrition": _NUTRITION,
                "description": "chicken",
                "photos": ["OLD_PHOTO"],
            }
        }
    )

    state = asyncio.run(tg.confirm_meal(update, context))

    assert state == tg.CONFIRMING_MEAL
    (sent_text,) = entry_mock.call_args.args
    assert sent_text == "chicken\nУточнение: это была индейка"
    assert "photos" not in context.user_data["current_meal"]


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
    reply = tg._nutrition_reply(_NUTRITION, "Заголовок:", numbers=True)
    assert reply.startswith("Заголовок:\n")
    assert "Продукты: banana, oatmeal" in reply
    # A text analysis is an estimate: a range, whole grams, no ".0".
    assert "Калории: ≈ 350 ккал (280–420)" in reply
    assert "Белки: 12г" in reply
    assert "Порция: 1 bowl (300g)" in reply
    assert reply.endswith("Всё верно? Сохрани или поправь кнопками ниже.")


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


def test_source_badge_label_ocr():
    badge = tg._source_badge(
        _make_resolution_result(
            source="label_ocr", confidence_tier="medium", signals={}
        )
    )
    assert "этикетки" in badge
    assert "проверь" in badge


def test_source_badge_medium_confidence():
    badge = tg._source_badge(
        _make_resolution_result(source="name_off", confidence_tier="medium")
    )
    assert "нашли в базе" in badge
    assert "проверь" in badge


def test_source_badge_name_web_medium():
    """name_web + medium tier (OFF re-query succeeded) → cautious web badge."""
    badge = tg._source_badge(
        _make_resolution_result(source="name_web", confidence_tier="medium", signals={})
    )
    assert "нашли в сети" in badge
    assert "проверь" in badge
    assert "сверь — веб" not in badge  # not the lowest-trust badge


def test_source_badge_name_web_low():
    """name_web + low tier (prose-only path) → most cautious badge."""
    badge = tg._source_badge(
        _make_resolution_result(source="name_web", confidence_tier="low", signals={})
    )
    assert "нашли в сети" in badge
    assert "сверь — веб" in badge


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


def test_resolution_detail_lines_leave_the_portion_to_the_portion_line():
    # The per-100g warning moved to the reply's «Порция:» line, next to the
    # button that fixes it; the detail lines no longer repeat it.
    result = _make_resolution_result(
        source="barcode_off",
        portion_grams=None,
        signals={"barcode_raw": "1234567890", "product_name": "Test Product"},
    )
    lines = tg._resolution_detail_lines(result)
    assert not any("не определена" in l for l in lines)
    assert any("Test Product" in l for l in lines)


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
    reply = tg._nutrition_reply(_NUTRITION, "Заголовок:", result, numbers=True)
    assert reply.startswith("Заголовок:\n")
    assert "штрих-коду" in reply  # badge
    assert "4607195501226" in reply  # EAN
    assert "Pringles" in reply  # product name
    assert "Продукты: banana, oatmeal" in reply  # nutrition block intact
    assert reply.endswith("Всё верно? Сохрани или поправь кнопками ниже.")


def test_nutrition_reply_vision_shows_source_badge():
    result = _make_resolution_result(source="vision", confidence_tier="low", signals={})
    reply = tg._nutrition_reply(_NUTRITION, "Заголовок:", result, numbers=True)
    assert "фото" in reply
    assert "Продукты: banana, oatmeal" in reply
    assert reply.endswith("Всё верно? Сохрани или поправь кнопками ниже.")


def test_nutrition_reply_no_portion_shows_warning():
    result = _make_resolution_result(
        source="barcode_off",
        portion_grams=None,
        signals={"barcode_raw": "999", "product_name": "Item"},
    )
    reply = tg._nutrition_reply(_NUTRITION, "Заголовок:", result, numbers=True)
    assert "Порция: не определена — числа на 100г, поправь вес кнопкой" in reply


# --- A6: gram-basis display when OFF per-100g values were scaled (CQ2) -----


def test_nutrition_reply_shows_the_gram_basis_in_the_portion_line():
    """A6 CQ2: when per-100g values were scaled, the reply says to what weight
    and that the weight is a photo estimate (fixable with the buttons)."""
    result = _make_resolution_result(
        source="barcode_off",
        portion_grams=150.0,
        signals={"barcode_raw": "4607195501226", "product_name": "Pringles"},
    )
    reply = tg._nutrition_reply(_NUTRITION, "Заголовок:", result, numbers=True)
    assert "Порция: 1 bowl (300g), ≈ 150г (оценка по фото)" in reply


def test_nutrition_reply_zero_grams_is_per_100g_not_scaled():
    """A6: a zero gram value (degenerate case) means no valid scaling."""
    result = _make_resolution_result(
        source="barcode_off",
        portion_grams=0.0,
        signals={"barcode_raw": "1234", "product_name": "Test"},
    )
    reply = tg._nutrition_reply(_NUTRITION, "Заголовок:", result, numbers=True)
    assert "не определена — числа на 100г" in reply
    assert "оценка по фото" not in reply


def test_nutrition_reply_barcode_off_scaled_keeps_every_element():
    result = _make_resolution_result(
        source="barcode_off",
        portion_grams=150.0,
        signals={
            "barcode_raw": "4607195501226",
            "product_name": "Чипсы Pringles Original",
        },
    )
    reply = tg._nutrition_reply(_NUTRITION, "Заголовок:", result, numbers=True)
    assert "≈ 150г (оценка по фото)" in reply
    assert "штрих-коду" in reply  # badge
    assert "4607195501226" in reply  # EAN
    assert "Pringles" in reply  # product name
    assert "Продукты: banana, oatmeal" in reply  # nutrition block
    # A barcode hit is exact per 100 g: no range on its numbers.
    assert "Калории: 350 ккал" in reply


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
    update, _ = _make_text_update("🍽 Добавить приём пищи")
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
    # Pipeline makes 3 calls: barcode_extraction + image (Phase 1, concurrent)
    # + label_ocr (LabelOCRStrategy, A10 — mocked to null-basis, falls through).
    assert len(rows) == 3
    kinds = {r.kind for r in rows}
    assert kinds == {"barcode_extraction", "image", "label_ocr"}
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
    original_picks = dict(svc.task_models)
    yield
    svc.model = original
    svc.task_models = original_picks


def test_model_deprecation_offers_picker(monkeypatch):
    err = tg.ModelUnavailableError("gpt-old", Exception("deprecated"))
    monkeypatch.setattr(
        tg.telegram_service.openai_service,
        "analyze_food_entry",
        AsyncMock(side_effect=err),
    )
    monkeypatch.setattr(
        tg.telegram_service.openai_service,
        "list_candidates",
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
        svc, "list_candidates", AsyncMock(return_value=["gpt-4o", "gpt-4o-mini"])
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


def test_non_admin_gets_no_picker(monkeypatch):
    """Only the owner switches models (ADR-0004); everyone else is told the owner
    already knows."""
    # A subscriber who is not the owner.
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "7")
    monkeypatch.setattr(tg, "check_subscription", AsyncMock(return_value=True))
    svc = tg.telegram_service.openai_service
    monkeypatch.setattr(
        svc,
        "analyze_food_entry",
        AsyncMock(side_effect=tg.ModelUnavailableError("gpt-old", Exception("x"))),
    )
    candidates = AsyncMock(return_value=["openai:gpt-6-luna"])
    monkeypatch.setattr(svc, "list_candidates", candidates)
    update, message = _make_text_update("apple")
    context = SimpleNamespace(user_data={})

    state = asyncio.run(tg.process_meal_input(update, context))

    assert state == tg.CHOOSING_ACTION
    assert "владельцу" in message.replies[-1]
    assert "model_choices" not in context.user_data
    candidates.assert_not_called()


def test_non_admin_cannot_apply_a_stale_pick(monkeypatch):
    # A subscriber who is not the owner.
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "7")
    monkeypatch.setattr(tg, "check_subscription", AsyncMock(return_value=True))
    svc = tg.telegram_service.openai_service
    before = svc.model
    context = SimpleNamespace(user_data={"model_choices": ["openai:gpt-6-luna"]})
    update, _ = _make_text_update("openai:gpt-6-luna")

    assert asyncio.run(tg.process_model_choice(update, context)) == tg.CHOOSING_ACTION
    assert svc.model == before


def _fail_then_succeed(monkeypatch, err):
    svc = tg.telegram_service.openai_service
    monkeypatch.setattr(
        svc,
        "analyze_food_entry",
        AsyncMock(side_effect=[err, json.dumps(_NUTRITION)]),
    )
    monkeypatch.setattr(
        svc, "list_candidates", AsyncMock(return_value=["openai:gpt-6-luna"])
    )
    context = SimpleNamespace(user_data={})
    u1, _ = _make_text_update("apple")
    assert asyncio.run(tg.process_meal_input(u1, context)) == tg.CHOOSING_MODEL
    svc.list_candidates.assert_awaited_once_with("text", exclude=err.model)
    u2, _ = _make_text_update("openai:gpt-6-luna")
    assert asyncio.run(tg.process_model_choice(u2, context)) == tg.CONFIRMING_MEAL
    return svc


def test_pick_for_a_task_on_the_default_replaces_the_default(patched_db, monkeypatch):
    """gpt-4o-mini dies for every task at once: one choice, not one per task."""
    err = tg.ModelUnavailableError("gpt-old", Exception("x"), task="text")
    svc = _fail_then_succeed(monkeypatch, err)

    assert svc.model == "openai:gpt-6-luna"
    assert "text" not in svc.task_models
    with sessionmaker(bind=patched_db)() as db:
        assert crud_app_setting.get(db, OPENAI_MODEL_SETTING_KEY) == "openai:gpt-6-luna"
        assert crud_app_setting.get(db, "model:text") is None


def test_pick_for_a_task_with_its_own_model_replaces_only_that(patched_db, monkeypatch):
    svc = tg.telegram_service.openai_service
    default = svc.model
    svc.set_model("openrouter:qwen/qwen3.8-flash", task="text")
    err = tg.ModelUnavailableError(
        "openrouter:qwen/qwen3.8-flash", Exception("x"), task="text"
    )
    _fail_then_succeed(monkeypatch, err)

    assert svc.model == default
    assert svc.model_for("text") == "openai:gpt-6-luna"
    with sessionmaker(bind=patched_db)() as db:
        assert crud_app_setting.get(db, "model:text") == "openai:gpt-6-luna"
        assert crud_app_setting.get(db, OPENAI_MODEL_SETTING_KEY) is None


def test_persisted_task_pick_applies_on_startup(patched_db):
    svc = tg.telegram_service.openai_service
    with tg.SessionLocal() as db:
        crud_app_setting.set(db, "model:vision", "openai:gpt-6-luna")

    tg.apply_persisted_model(svc)

    assert svc.model_for("vision") == "openai:gpt-6-luna"


def test_load_persisted_model_applies_on_startup(patched_db):
    svc = tg.telegram_service.openai_service
    with tg.SessionLocal() as db:
        crud_app_setting.set(db, OPENAI_MODEL_SETTING_KEY, "gpt-persisted")

    tg.apply_persisted_model(svc)

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


# --- the caption must survive every path that re-runs the analysis ----------
#
# It is persisted to inbound_messages on receipt, so "the caption is saved" was
# true while the model never saw it. These assert the CALL, end to end through
# the real pipeline: the only mock is the vision method itself.

_REAL_CAPTION = (
    "Я доела остатки (1/3 порции примерно) этого батата с Classic Fasting Ranch Sauce"
)


def test_photo_caption_reaches_the_vision_call(patched_db, image_mock):
    update, _ = _make_update(
        photo=[SimpleNamespace(file_id="PHOTO_FILE_ID")], caption=_REAL_CAPTION
    )

    asyncio.run(tg.process_meal_input(update, _make_photo_context()))

    assert image_mock.call_args.kwargs.get("caption") == _REAL_CAPTION, (
        "the caption was persisted to inbound_messages but never sent to the "
        f"model (kwargs seen: {sorted(image_mock.call_args.kwargs)})"
    )


def test_photo_without_caption_passes_none(patched_db, image_mock):
    """Expected answer 'no finding': no caption → nothing invented."""
    update, _ = _make_photo_update()

    asyncio.run(tg.process_meal_input(update, _make_photo_context()))

    assert image_mock.call_args.kwargs.get("caption") is None


def test_model_switch_retry_keeps_the_caption(patched_db, image_mock):
    """The retry re-runs the analysis from a stash; a caption dropped there is
    lost on exactly the retry the owner runs after a model outage."""
    update, _ = _make_update(
        photo=[SimpleNamespace(file_id="PHOTO_FILE_ID")], caption=_REAL_CAPTION
    )
    context = _make_photo_context()

    # First pass: the configured model is gone → the picker is offered.
    image_mock.side_effect = ModelUnavailableError("gpt-4o-mini", RuntimeError("gone"))
    asyncio.run(tg.process_meal_input(update, context))
    assert context.user_data["pending_analysis"]["caption"] == _REAL_CAPTION

    # Second pass: the owner picks a model and the analysis is replayed.
    image_mock.side_effect = None
    image_mock.return_value = json.dumps(_NUTRITION)
    context.user_data["model_choices"] = ["gpt-4o"]
    update2, _ = _make_text_update("gpt-4o")
    with patch.object(tg.telegram_service.openai_service, "set_model"), patch(
        "app.services.telegram.persist_model"
    ):
        asyncio.run(tg.process_model_choice(update2, context))

    assert image_mock.call_args.kwargs.get("caption") == _REAL_CAPTION


def test_reprocess_replays_the_stored_caption(patched_db, image_mock):
    """/reprocess OVERWRITES ai_analysis — replaying without the caption would
    actively downgrade a record the live flow got right."""
    with sessionmaker(bind=patched_db)() as db:
        db.add(
            InboundMessage(
                telegram_id=42,
                kind="image",
                content=_REAL_CAPTION,
                photo_file_id="PHOTO_FILE_ID",
                status="failed",
                error="old model",
            )
        )
        db.commit()

    update, _ = _make_text_update("/reprocess")
    file = SimpleNamespace(
        download_as_bytearray=AsyncMock(return_value=bytearray(_IMAGE_BYTES))
    )
    context = SimpleNamespace(
        user_data={}, bot=SimpleNamespace(get_file=AsyncMock(return_value=file))
    )

    asyncio.run(tg.reprocess(update, context))

    assert image_mock.call_args.kwargs.get("caption") == _REAL_CAPTION


# --- a swallowed strategy failure must be visible to the user --------------


def test_reply_warns_when_a_strategy_errored():
    result = _make_resolution_result(
        source="vision",
        confidence_tier="low",
        portion_grams=100.0,
        signals={
            "strategy_attempts": [
                {"strategy": "name_web", "outcome": "error", "error": "AttributeError"},
                {"strategy": "vision", "outcome": "hit"},
            ]
        },
    )

    lines = tg._resolution_detail_lines(result)

    assert any("не сработала" in line for line in lines), (
        "a strategy that was 100% broken left the reply looking completely "
        "normal — the user had no way to know the numbers came from a "
        "shallower path"
    )


def test_reply_stays_quiet_when_every_strategy_merely_missed():
    """Expected answer 'no finding': misses are normal, they are not a warning."""
    result = _make_resolution_result(
        source="vision",
        confidence_tier="low",
        portion_grams=100.0,
        signals={
            "strategy_attempts": [
                {"strategy": "name_web", "outcome": "miss"},
                {"strategy": "vision", "outcome": "hit"},
            ]
        },
    )

    assert tg._resolution_detail_lines(result) == []


def test_reply_renders_an_unknown_macro_as_a_dash():
    """OFF routinely lacks a macro; printing 0 stated a fact it never had."""
    nutrition = dict(_NUTRITION, protein=None)

    reply = tg._nutrition_reply(nutrition, "Заголовок:", numbers=True)

    assert "Белки: —" in reply
    assert "Белки: —г" not in reply, "the unit was kept on the dash — reads as a number"
    assert "Белки: 0г" not in reply


def test_reply_names_the_note_in_the_gram_basis_line():
    result = _make_resolution_result(
        source="barcode_off",
        portion_grams=60.0,
        signals={
            "barcode_raw": "1234",
            "product_name": "Батат",
            "portion_source": "vision+note",
        },
    )

    reply = tg._nutrition_reply(_NUTRITION, "Заголовок:", result, numbers=True)

    assert "оценка по фото и твоему описанию" in reply, (
        "the portion came from the user's own words but the reply still called "
        "it a photo estimate"
    )


# --- the confidence downgrade has to reach the BADGE, not just the DB -------


def test_barcode_badge_drops_the_certainty_claim_when_macros_are_missing():
    """_source_badge returns on `source` before it looks at the tier.

    So downgrading a macro-less barcode hit to "medium" changed the stored tier
    while the user still read "📦 по штрих-коду (точно)" beside a "—" macro.
    """
    result = _make_resolution_result(
        source="barcode_off",
        confidence_tier="medium",
        signals={"barcode_raw": "1234", "off_missing_macros": ["белки"]},
    )

    badge = tg._source_badge(result)

    assert "точно" not in badge, f"the downgrade never reached the user: {badge!r}"
    assert "неполные" in badge


def test_barcode_badge_still_says_certain_for_a_complete_record():
    """Expected answer 'no finding': a complete OFF record keeps its badge."""
    badge = tg._source_badge(
        _make_resolution_result(source="barcode_off", confidence_tier="high")
    )
    assert "точно" in badge


def test_no_degradation_warning_when_a_high_confidence_path_still_won():
    """The warning must not contradict the badge printed next to it.

    saved_rag can error while barcode_off still returns a complete, exact match —
    "числа могут быть грубее обычного" would then be false.
    """
    result = _make_resolution_result(
        source="barcode_off",
        confidence_tier="high",
        signals={
            "barcode_raw": "1234",
            "product_name": "Item",
            "strategy_attempts": [
                {
                    "strategy": "saved_rag",
                    "outcome": "error",
                    "error": "OperationalError",
                },
                {"strategy": "barcode_off", "outcome": "hit"},
            ],
        },
    )

    lines = tg._resolution_detail_lines(result)

    assert not any("не сработала" in line for line in lines), (
        "warned about degraded numbers on the most exact result the pipeline "
        f"can produce: {lines}"
    )


def test_reply_warns_when_a_winning_strategy_recorded_a_failure():
    """A strategy can RETURN a result and still have a broken leg.

    NameWebSearchStrategy falls back to web prose when its OFF re-query fails, so
    the outcome is "hit" while the numbers came from the lowest-trust path. Keying
    the warning on outcome == "error" alone showed a clean reply for those.
    """
    result = _make_resolution_result(
        source="name_web",
        confidence_tier="low",
        portion_grams=150.0,
        signals={
            "product_name": "Ranch",
            "strategy_attempts": [
                {
                    "strategy": "name_web",
                    "outcome": "hit",
                    "error": "ConnectError: off lookup failed",
                }
            ],
        },
    )

    lines = tg._resolution_detail_lines(result)

    assert any(
        "не сработала" in line for line in lines
    ), "the OFF re-query leg was fully broken and the reply said nothing"


def test_draft_keeps_the_number_caveats_with_the_hidden_numbers():
    """The ⚠️ lines speak about the numbers, so they hide and open with them;
    the barcode and the product name stay, being about the food (stage 5)."""
    result = _make_resolution_result(
        source="name_web",
        confidence_tier="low",
        signals={
            "barcode_raw": "1234",
            "product_name": "Ranch",
            "off_missing_macros": ["белки"],
            "strategy_attempts": [{"strategy": "name_web", "error": "Boom"}],
        },
    )
    draft = {"nutrition": dict(_NUTRITION)}
    tg._start_draft(draft, "Разбор фото:", result, numbers=False)
    hidden = meal_draft.text(draft)
    assert "Штрих-код: 1234" in hidden and "Продукт: Ranch" in hidden
    assert "⚠️" not in hidden

    meal_draft.toggle_numbers(draft)
    opened = meal_draft.text(draft)
    assert "⚠️ в базе нет: белки" in opened
    assert "не сработала" in opened


# --- the meal-of-day label: asked since day one, stored since 2026-09-13 ----


def test_meal_type_label_reaches_the_saved_meal(patched_db):
    """The bot asks "Когда был приём пищи?" and offers Завтрак/Обед/Ужин.

    The answer used to live only in context.user_data and was dropped when the
    conversation ended — no column, no schema field, no read site anywhere.
    """
    context = SimpleNamespace(user_data={})
    update, _ = _make_text_update("Завтрак")
    asyncio.run(tg.process_meal_time(update, context))

    context.user_data["current_meal"] = {
        "nutrition": _NUTRITION,
        "description": "овсянка",
    }
    confirm, _ = _make_text_update("Да")
    asyncio.run(tg.confirm_meal(confirm, context))

    with sessionmaker(bind=patched_db)() as db:
        meals = db.query(Meal).all()
    assert len(meals) == 1
    assert (
        meals[0].meal_type == "Завтрак"
    ), "the user answered the bot's own question and the answer was discarded"


def test_now_stores_no_label_rather_than_inventing_one(patched_db):
    """Expected answer 'no finding': "Сейчас" is not a meal of the day.

    Seeded with a stale label on purpose: starting from an empty user_data, this
    assertion held whether or not the "Сейчас" branch CLEARS a previous answer,
    so it could not see the leak the review found on the reject path.
    """
    context = SimpleNamespace(user_data={"meal_type": "Ужин"})
    update, _ = _make_text_update("Сейчас")
    asyncio.run(tg.process_meal_time(update, context))

    context.user_data["current_meal"] = {
        "nutrition": _NUTRITION,
        "description": "перекус",
    }
    confirm, _ = _make_text_update("Да")
    asyncio.run(tg.confirm_meal(confirm, context))

    with sessionmaker(bind=patched_db)() as db:
        meals = db.query(Meal).all()
    assert meals[0].meal_type is None


def test_a_new_meal_does_not_inherit_the_previous_label(patched_db):
    """State leak: add_meal popped meal_time but not meal_type.

    Abandon a breakfast entry, start a new one, pick "Сейчас" — the stale
    "Завтрак" would have been attached to the second meal.
    """
    context = SimpleNamespace(user_data={})
    first, _ = _make_text_update("Завтрак")
    asyncio.run(tg.process_meal_time(first, context))  # …then abandoned

    restart, _ = _make_text_update("Добавить приём пищи")
    asyncio.run(tg.add_meal(restart, context))
    second, _ = _make_text_update("Сейчас")
    asyncio.run(tg.process_meal_time(second, context))

    context.user_data["current_meal"] = {
        "nutrition": _NUTRITION,
        "description": "ужин",
    }
    confirm, _ = _make_text_update("Да")
    asyncio.run(tg.confirm_meal(confirm, context))

    with sessionmaker(bind=patched_db)() as db:
        meals = db.query(Meal).all()
    assert (
        meals[0].meal_type is None
    ), "an abandoned entry's label leaked into the next meal"


def test_declining_a_draft_drops_the_label_on_the_retry(patched_db):
    """The bot's OWN retry path, which does not go through add_meal.

    "Нет" resets the draft and re-asks "Когда был приём пищи?" directly, so the
    first fix (clearing the label in add_meal) never ran here: answering "Сейчас"
    on the retry still saved the declined "Завтрак".
    """
    context = SimpleNamespace(user_data={})
    first, _ = _make_text_update("Завтрак")
    asyncio.run(tg.process_meal_time(first, context))
    context.user_data["current_meal"] = {
        "nutrition": _NUTRITION,
        "description": "овсянка",
    }

    reject, _ = _make_text_update("Нет")
    state = asyncio.run(tg.confirm_meal(reject, context))
    assert state == tg.ADDING_MEAL_TIME

    retry, _ = _make_text_update("Сейчас")
    asyncio.run(tg.process_meal_time(retry, context))
    context.user_data["current_meal"] = {
        "nutrition": _NUTRITION,
        "description": "ужин",
    }
    confirm, _ = _make_text_update("Да")
    asyncio.run(tg.confirm_meal(confirm, context))

    with sessionmaker(bind=patched_db)() as db:
        meals = db.query(Meal).all()
    assert len(meals) == 1
    assert (
        meals[0].meal_type is None
    ), "the label the user declined was written to the meal they kept"


def test_free_text_at_the_time_step_is_not_stored_as_a_label(patched_db):
    """meals.meal_type is exported; it must hold a label, not whatever was typed.

    The buttons are the contract, but a Telegram user can type anything instead
    of pressing one.
    """
    context = SimpleNamespace(user_data={})
    typed, _ = _make_text_update("да я не помню, где-то днём")
    asyncio.run(tg.process_meal_time(typed, context))

    context.user_data["current_meal"] = {
        "nutrition": _NUTRITION,
        "description": "обед",
    }
    confirm, _ = _make_text_update("Да")
    asyncio.run(tg.confirm_meal(confirm, context))

    with sessionmaker(bind=patched_db)() as db:
        meals = db.query(Meal).all()
    assert meals[0].meal_type is None


def test_a_typed_label_is_normalised_rather_than_refused(patched_db):
    """…but the rule must not wrongly refuse: a typed "завтрак" IS the label."""
    context = SimpleNamespace(user_data={})
    typed, _ = _make_text_update("завтрак ")
    asyncio.run(tg.process_meal_time(typed, context))

    context.user_data["current_meal"] = {
        "nutrition": _NUTRITION,
        "description": "овсянка",
    }
    confirm, _ = _make_text_update("Да")
    asyncio.run(tg.confirm_meal(confirm, context))

    with sessionmaker(bind=patched_db)() as db:
        meals = db.query(Meal).all()
    assert meals[0].meal_type == "Завтрак"


# --- a photo sent from the menu, without «🍽 Добавить приём пищи» -------------


def test_quick_photo_is_a_meal_eaten_now(image_mock):
    update, message = _make_photo_update()
    context = _make_photo_context()
    # Leftovers of an abandoned entry: an old label and an old draft.
    context.user_data.update(
        meal_type="Завтрак",
        meal_time=datetime(2020, 1, 1, tzinfo=UTC),
        current_meal={"description": "old"},
    )
    before = datetime.now(UTC)

    state = asyncio.run(tg.quick_meal_photo(update, context))

    assert state == tg.CONFIRMING_MEAL
    image_mock.assert_awaited_once()
    assert "meal_type" not in context.user_data
    assert context.user_data["meal_time"] >= before
    assert context.user_data["current_meal"]["photos"] == ["PHOTO_FILE_ID"]
    assert "banana, oatmeal" in message.replies[0]


# --- the loader while the analysis runs --------------------------------------


class _EditableMessage(_FakeMessage):
    """Replies come back as messages that can be edited, like Telegram's."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.screen = []  # [text, markup] per message still on screen

    async def reply_text(self, text, *args, **kwargs):
        await super().reply_text(text, *args, **kwargs)
        shown = [text, kwargs.get("reply_markup")]
        self.screen.append(shown)
        message = self

        class _Sent:
            chat_id, message_id = 42, len(self.screen)

            async def edit_text(self, text, reply_markup=None):
                shown[:] = [text, reply_markup]
                return self

            async def delete(self):
                message.screen.remove(shown)

        return _Sent()


def _slow(mock, monkeypatch, takes=0.05, delay=0.01):
    monkeypatch.setattr(loader_mod, "DELAY", delay)
    answer, error = mock.return_value, mock.side_effect

    async def slow(*args, **kwargs):
        await asyncio.sleep(takes)
        if error:
            raise error
        return answer

    return slow


def test_a_slow_photo_shows_a_loader_that_becomes_the_draft(image_mock, monkeypatch):
    monkeypatch.setattr(
        tg.telegram_service.openai_service,
        "analyze_food_image",
        _slow(image_mock, monkeypatch),
    )
    message = _EditableMessage(photo=[SimpleNamespace(file_id="PHOTO_FILE_ID")])
    update = SimpleNamespace(message=message, effective_user=message.from_user)
    context = _make_photo_context()

    state = asyncio.run(tg.process_meal_input(update, context))

    assert state == tg.CONFIRMING_MEAL
    assert message.replies == ["⏳ Смотрю фото…"]  # one message, edited
    ((text, markup),) = message.screen
    assert "banana, oatmeal" in text
    assert markup is not None  # the draft's buttons came with the edit
    assert context.user_data["current_meal"]["msg_id"] == 1


def test_a_slow_failure_turns_the_loader_into_the_error(entry_mock, monkeypatch):
    entry_mock.side_effect = RuntimeError("openai 500")
    monkeypatch.setattr(
        tg.telegram_service.openai_service,
        "analyze_food_entry",
        _slow(entry_mock, monkeypatch),
    )
    message = _EditableMessage(text="гречка 200 г")
    update = SimpleNamespace(message=message, effective_user=message.from_user)
    context = SimpleNamespace(bot=None, user_data={"meal_time": datetime.now(UTC)})

    state = asyncio.run(tg.process_meal_input(update, context))

    assert state == tg.ADDING_MEAL
    assert message.replies == ["⏳ Считаю…"]
    ((text, _markup),) = message.screen
    assert "Не получилось разобрать" in text


def test_a_fast_answer_shows_no_loader(entry_mock, monkeypatch):
    monkeypatch.setattr(
        tg.telegram_service.openai_service,
        "analyze_food_entry",
        _slow(entry_mock, monkeypatch, takes=0.01, delay=0.2),
    )
    message = _EditableMessage(text="гречка 200 г")
    update = SimpleNamespace(message=message, effective_user=message.from_user)
    context = SimpleNamespace(bot=None, user_data={"meal_time": datetime.now(UTC)})

    asyncio.run(tg.process_meal_input(update, context))

    assert len(message.replies) == 1
    assert not message.replies[0].startswith("⏳")


# --- 💸 the cost line (⚙️ Настройки → Стоимость запросов) -----------------------


def _shows_costs(patched_db, on=True):
    with sessionmaker(bind=patched_db)() as db:
        db.add(User(telegram_id=42, username="owner", settings={"show_costs": on}))
        db.commit()


def _metered(mock, *, fails=False):
    """The analysis as openai_service makes it: one chat call, usage recorded."""
    answer = mock.return_value

    async def analyze(*args, **kwargs):
        record_usage(
            "gpt-6-luna",
            CompletionUsage(
                prompt_tokens=2000, completion_tokens=400, total_tokens=2400
            ),
        )
        if fails:
            raise RuntimeError("openai 500")
        return answer

    return analyze


def _text_meal(monkeypatch, entry_mock, **kw):
    monkeypatch.setattr(
        tg.telegram_service.openai_service,
        "analyze_food_entry",
        _metered(entry_mock, **kw),
    )
    update, message = _make_text_update("гречка 200 г")
    context = SimpleNamespace(bot=None, user_data={"meal_time": datetime.now(UTC)})
    state = asyncio.run(tg.process_meal_input(update, context))
    return state, message, context


# gpt-6-luna: 2000 in × $0.10 + 400 out × $0.50 per 1M tokens.
_COST = "💸 Запрос: $0.0004 · 1 вызов ИИ"


def test_the_draft_shows_what_the_analysis_cost(patched_db, entry_mock, monkeypatch):
    _shows_costs(patched_db)

    state, message, context = _text_meal(monkeypatch, entry_mock)

    assert state == tg.CONFIRMING_MEAL
    assert _COST in message.replies[-1]
    # Kept in the draft, so a re-render (a portion change) keeps the line.
    assert _COST in meal_draft.text(context.user_data["current_meal"])


@pytest.mark.parametrize("setting", ["off", "no user"])
def test_no_cost_line_unless_switched_on(patched_db, entry_mock, monkeypatch, setting):
    if setting == "off":
        _shows_costs(patched_db, on=False)

    state, message, context = _text_meal(monkeypatch, entry_mock)

    assert state == tg.CONFIRMING_MEAL
    assert "💸" not in message.replies[-1]
    assert "cost_line" not in context.user_data["current_meal"]


def test_a_failed_analysis_still_says_what_it_cost(patched_db, entry_mock, monkeypatch):
    _shows_costs(patched_db)

    state, message, _ = _text_meal(monkeypatch, entry_mock, fails=True)

    assert state == tg.ADDING_MEAL
    assert "Не получилось разобрать" in message.replies[-1]
    assert message.replies[-1].endswith(_COST)


# --- a used-up AI balance / a rate limit (ai_errors) ------------------------


@pytest.fixture
def alerts(monkeypatch):
    """The owner alerts that went out; the hourly dedupe starts empty."""
    sent = []

    async def notify(text):
        sent.append(text)
        return True

    monkeypatch.setattr(tg, "notify_admins", notify)
    monkeypatch.setattr(tg, "_credit_alerted", {})
    return sent


def _not_admin(monkeypatch):
    """User 42 is a subscribed guest, not the owner."""
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "7")
    monkeypatch.setattr(tg, "check_subscription", AsyncMock(return_value=True))


def test_text_meal_out_of_credit_tells_owner_where_to_pay(entry_mock, alerts):
    entry_mock.side_effect = sdk_error(429, QUOTA_BODY)
    update, message = _make_text_update("oatmeal")
    context = SimpleNamespace(user_data={})

    state = asyncio.run(tg.process_meal_input(update, context))

    reply = message.replies[-1]
    assert state == tg.CHOOSING_ACTION  # a retry cannot pass: back to the menu
    assert message.reply_markups[-1] is tg.main_keyboard
    assert "У OpenAI закончился баланс или лимит трат" in reply
    assert "platform.openai.com" in reply
    assert "/reprocess" in reply  # the meal was saved as failed, it waits
    assert "Не получилось разобрать" not in reply
    assert alerts == []  # the owner reads it right here


def test_photo_meal_out_of_credit_survives_the_pipeline(patched_db, image_mock, alerts):
    # The pipeline swallows the vision error; it must still be the cause.
    image_mock.side_effect = sdk_error(429, QUOTA_BODY)
    update, message = _make_photo_update()
    context = _make_photo_context()

    state = asyncio.run(tg.process_meal_input(update, context))

    assert state == tg.CHOOSING_ACTION
    assert "У OpenAI закончился баланс" in message.replies[-1]
    with sessionmaker(bind=patched_db)() as db:
        (row,) = db.query(InboundMessage).all()
    assert row.status == "failed"  # /reprocess picks it up after the top-up


def test_openrouter_out_of_credit_names_openrouter(entry_mock, alerts):
    entry_mock.side_effect = sdk_error(402, CREDITS_BODY, host="openrouter.ai/api")
    update, message = _make_text_update("oatmeal")

    asyncio.run(tg.process_meal_input(update, SimpleNamespace(user_data={})))

    assert "У OpenRouter закончился баланс" in message.replies[-1]
    assert "openrouter.ai → Settings → Credits" in message.replies[-1]


def test_out_of_credit_for_a_guest_alerts_the_owner_once(
    entry_mock, alerts, monkeypatch
):
    _not_admin(monkeypatch)
    entry_mock.side_effect = sdk_error(429, QUOTA_BODY)

    for _ in range(2):
        update, message = _make_text_update("oatmeal")
        asyncio.run(tg.process_meal_input(update, SimpleNamespace(user_data={})))
        reply = message.replies[-1]
        assert "временно недоступно — владельцу бота уже пришло уведомление" in reply
        assert "platform.openai.com" not in reply  # billing is the owner's business

    assert len(alerts) == 1  # two meals, one alert
    assert "у пользователя 42" in alerts[0] and "/reprocess" in alerts[0]


def test_out_of_credit_for_a_guest_claims_no_alert_that_failed(entry_mock, monkeypatch):
    _not_admin(monkeypatch)
    monkeypatch.setattr(tg, "notify_admins", AsyncMock(return_value=False))
    monkeypatch.setattr(tg, "_credit_alerted", {})
    entry_mock.side_effect = sdk_error(429, QUOTA_BODY)
    update, message = _make_text_update("oatmeal")

    asyncio.run(tg.process_meal_input(update, SimpleNamespace(user_data={})))

    assert message.replies[-1] == (
        "💳 Распознавание еды временно недоступно. Попробуй позже."
    )
    assert tg._credit_alerted == {}  # the next meal tries to tell the owner again


def test_rate_limit_asks_to_wait_a_minute(entry_mock, alerts):
    entry_mock.side_effect = sdk_error(429, RATE_BODY)
    update, message = _make_text_update("oatmeal")

    state = asyncio.run(tg.process_meal_input(update, SimpleNamespace(user_data={})))

    assert state == tg.ADDING_MEAL  # it passes in a minute: stay, send again
    assert "перегружен" in message.replies[-1]
    assert "💳" not in message.replies[-1]
    assert alerts == []
