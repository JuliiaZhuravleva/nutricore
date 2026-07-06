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

from app.core.config import settings
from app.crud.crud_app_setting import crud_app_setting
from app.db.base import Base
from app.models.ai_call_log import AiCallLog
from app.models.meal import Meal
from app.services import ai_call_log_service as ai_log
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
    def __init__(self, *, photo=None, text=None):
        self.photo = photo
        self.text = text
        self.from_user = SimpleNamespace(id=42, username="owner")
        self.replies = []

    async def reply_text(self, text, *args, **kwargs):
        self.replies.append(text)


def _make_update(*, photo=None, text=None):
    message = _FakeMessage(photo=photo, text=text)
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
    """Point telegram.SessionLocal at a fresh in-memory SQLite DB for every test.

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
    monkeypatch.setattr(ai_log, "SessionLocal", Session)  # recording hook
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


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
    assert len(rows) == 1
    row = rows[0]
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
    assert len(rows) == 1
    assert rows[0].status == "error"
    assert "boom" in rows[0].error
    assert rows[0].parsed_result is None


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
