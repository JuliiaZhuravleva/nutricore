"""The confirm step's inline buttons (``meal_callback``) and typed weights.

Runs the real handlers with fake Telegram objects on an in-memory SQLite DB:
a size chip edits the draft in place, «Поправить вес» + a typed number
rescales it, «Сохранить» saves what is on screen, a button of an older draft
only says it is stale.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from telegram.error import BadRequest

from app.core.config import settings
from app.db.base import Base
from app.models.favorite_meal import FavoriteMeal
from app.models.meal import Meal
from app.services import meal_draft as md
from app.services import telegram as tg

_NUTRITION = {
    "foods": ["самса"],
    "calories": 250.0,
    "protein": 6.0,
    "fats": 12.5,
    "carbs": 30.0,
    "portion": "1 штука (~100г)",
    "portion_grams": 100.0,
}
_SIGNALS = {"portion_grams": 100.0, "portion_source": "vision", "foods": ["самса"]}


@pytest.fixture
def Session(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(tg, "SessionLocal", factory)
    yield factory
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(autouse=True)
def _owner_is_admin(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "42")


class _Msg:
    def __init__(self, text=None):
        self.text = text
        self.chat_id = 42
        self.message_id = 7
        self.replies = []
        self.markups = []

    async def reply_text(self, text, *args, **kwargs):
        self.replies.append(text)
        self.markups.append(kwargs.get("reply_markup"))
        return SimpleNamespace(chat_id=42, message_id=100 + len(self.replies))


def _draft():
    draft = {
        "nutrition": dict(_NUTRITION),
        "description": "самса",
        "photos": ["PHOTO"],
        "caption": "Самса веган",
        "resolution_source": "vision",
        "resolution_signals": dict(_SIGNALS),
    }
    md.start(
        draft,
        header="Фото:",
        badge="📷 оценка по фото",
        detail_lines=[],
        estimate=True,
        base_grams=100.0,
        chips=True,
        basis="оценка по фото",
    )
    draft.update(chat_id=42, msg_id=7)
    return draft


def _context(draft):
    bot = SimpleNamespace(edit_message_reply_markup=AsyncMock())
    return SimpleNamespace(
        bot=bot, user_data={"current_meal": draft, "meal_type": "Обед"}
    )


def _tap(context, data):
    message = _Msg()
    query = SimpleNamespace(
        data=data,
        message=message,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        edit_message_reply_markup=AsyncMock(),
    )
    update = SimpleNamespace(
        callback_query=query, effective_user=SimpleNamespace(id=42, username="owner")
    )
    state = asyncio.run(tg.meal_callback(update, context))
    return state, query


def _type(context, text):
    message = _Msg(text)
    update = SimpleNamespace(
        message=message, effective_user=SimpleNamespace(id=42, username="owner")
    )
    state = asyncio.run(tg.confirm_meal(update, context))
    return state, message


def _cb(draft, verb, arg=""):
    return md._cb(draft, verb, arg)


# --- size chips + weight ------------------------------------------------------


def test_a_size_chip_rescales_the_draft_in_place():
    draft = _draft()
    context = _context(draft)

    state, query = _tap(context, _cb(draft, md.SIZE, "l"))

    assert state is None  # stays at the confirm step
    assert context.user_data["current_meal"]["nutrition"]["calories"] == 350.0
    query.answer.assert_awaited_once_with("Большая порция")
    text = query.edit_message_text.call_args.args[0]
    assert "Калории: ≈ 350 ккал (280–420)" in text
    assert "Порция: ≈ 140г (большая)" in text
    markup = query.edit_message_text.call_args.kwargs["reply_markup"]
    assert markup.inline_keyboard[0][2].text == "✓ Большая 140г"


def test_a_repeated_tap_on_the_same_chip_is_not_an_error():
    draft = _draft()
    context = _context(draft)
    message = _Msg()
    query = SimpleNamespace(
        data=_cb(draft, md.SIZE, "m"),
        message=message,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(
            side_effect=BadRequest("Message is not modified: same content")
        ),
    )
    update = SimpleNamespace(
        callback_query=query, effective_user=SimpleNamespace(id=42, username="u")
    )

    assert asyncio.run(tg.meal_callback(update, context)) is None


def test_fix_weight_asks_then_a_bare_number_rescales_and_moves_the_buttons():
    draft = _draft()
    context = _context(draft)

    _state, query = _tap(context, _cb(draft, md.ASK_GRAMS))
    assert draft["awaiting"] == "grams"
    assert query.edit_message_text.call_args.args[0].endswith(
        "Напиши вес в граммах, например 150."
    )

    state, message = _type(context, "140")

    assert state == tg.CONFIRMING_MEAL
    assert draft["nutrition"]["calories"] == 350.0
    assert draft["awaiting"] is None
    assert "Порция: 140г (твой вес)" in message.replies[-1]
    assert message.markups[-1].inline_keyboard  # the buttons moved down...
    context.bot.edit_message_reply_markup.assert_awaited_once_with(
        chat_id=42, message_id=7, reply_markup=None
    )  # ...from the old message
    assert draft["msg_id"] == 101


def test_a_weight_that_was_asked_for_but_unreadable_asks_again():
    draft = _draft()
    draft["awaiting"] = "grams"
    context = _context(draft)

    state, message = _type(context, "много")

    assert state == tg.CONFIRMING_MEAL
    assert "Не понял вес" in message.replies[-1]
    assert draft["nutrition"] == _NUTRITION


def test_a_weight_with_its_unit_rescales_without_asking():
    draft = _draft()
    context = _context(draft)

    _type(context, "200 г")

    assert draft["nutrition"]["calories"] == 500.0


def test_a_bare_number_nobody_asked_for_is_a_correction_not_grams(monkeypatch):
    # "2" may mean two pieces: it goes to the re-analysis, not to the weight.
    draft = _draft()
    context = _context(draft)
    reanalyze = AsyncMock(return_value=tg.CONFIRMING_MEAL)
    monkeypatch.setattr(tg, "_reanalyze_with_correction", reanalyze)

    _type(context, "2")

    reanalyze.assert_awaited_once()
    assert reanalyze.call_args.args[3] == "2"
    assert draft["nutrition"] == _NUTRITION


def test_fix_product_waits_for_the_text():
    draft = _draft()
    context = _context(draft)

    _state, query = _tap(context, _cb(draft, md.ASK_FOOD))

    assert draft["awaiting"] == "food"
    assert "фото останется" in query.edit_message_text.call_args.args[0]


# --- save / cancel ---------------------------------------------------------------


def test_save_button_saves_what_is_on_screen(Session, celery_delay):
    draft = _draft()
    context = _context(draft)
    _tap(context, _cb(draft, md.SIZE, "l"))

    state, query = _tap(context, _cb(draft, md.SAVE))

    assert state == tg.CHOOSING_ACTION
    assert context.user_data == {}
    with Session() as db:
        (meal,) = db.query(Meal).all()
        assert meal.calories == 350.0
        assert meal.meal_type == "Обед"
        assert meal.photos == ["PHOTO"]
        # What the analysis said, before the user's portion change.
        assert meal.ai_analysis["calories"] == 250.0
        assert meal.resolution_signals["portion_grams"] == 140.0
    # The settled draft keeps its text, loses its buttons.
    assert query.edit_message_text.call_args.args[0].endswith("✅ Сохранено")
    assert query.edit_message_text.call_args.kwargs["reply_markup"] is None
    assert "сохранен" in query.message.replies[-1]
    assert query.message.markups[-1] is tg.main_keyboard
    # TD-025: the call site still enqueues the personal-food save, and with a
    # gram basis that matches the saved numbers (350 ккал at 140 г = 250/100 г).
    celery_delay.assert_called_once()
    sent = celery_delay.call_args.kwargs
    assert sent["per_100g_calories"] == pytest.approx(250.0)
    assert sent["per_100g_fats"] == pytest.approx(12.5)


def test_save_to_favorites_button_names_the_dish_by_the_caption(Session):
    draft = _draft()
    context = _context(draft)

    _state, query = _tap(context, _cb(draft, md.SAVE_FAVORITE))

    with Session() as db:
        (fav,) = db.query(FavoriteMeal).all()
        assert fav.name == "Самса веган"
    assert "⭐ В избранном: «Самса веган»" in query.message.replies[-1]


def test_cancel_button_discards_the_draft():
    draft = _draft()
    context = _context(draft)

    state, query = _tap(context, _cb(draft, md.CANCEL))

    assert state == tg.CHOOSING_ACTION
    assert context.user_data == {}
    assert query.edit_message_text.call_args.args[0].endswith("✖️ Отменено")
    assert query.message.markups[-1] is tg.main_keyboard


# --- stale buttons + access ----------------------------------------------------


def test_a_button_of_an_older_draft_only_says_it_is_stale():
    old = _draft()
    current = _draft()
    context = _context(current)

    state, query = _tap(context, _cb(old, md.SIZE, "l"))

    assert state is None
    assert current["nutrition"] == _NUTRITION
    assert "неактуален" in query.answer.call_args.args[0]
    query.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)
    query.edit_message_text.assert_not_awaited()


def test_after_a_save_the_old_buttons_are_stale_not_a_second_save(Session):
    draft = _draft()
    context = _context(draft)
    _tap(context, _cb(draft, md.SAVE))

    _state, query = _tap(context, _cb(draft, md.SAVE))

    assert "неактуален" in query.answer.call_args.args[0]
    with Session() as db:
        assert db.query(Meal).count() == 1


def test_without_a_subscription_a_tap_changes_nothing(monkeypatch):
    monkeypatch.setattr(tg, "check_subscription", AsyncMock(return_value=False))
    draft = _draft()
    context = _context(draft)

    _state, query = _tap(context, _cb(draft, md.SIZE, "l"))

    assert draft["nutrition"] == _NUTRITION
    query.edit_message_text.assert_not_awaited()


def test_the_buttons_are_wired_to_the_confirm_step_and_globally(monkeypatch):
    from telegram.ext import CallbackQueryHandler, ConversationHandler

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:fake")
    application = tg.create_bot_application()
    handlers = [h for group in application.handlers.values() for h in group]

    (conv,) = [h for h in handlers if isinstance(h, ConversationHandler)]
    in_confirm = [
        h
        for h in conv.states[tg.CONFIRMING_MEAL]
        if isinstance(h, CallbackQueryHandler)
    ]
    assert [h.callback for h in in_confirm] == [tg.meal_callback]
    assert in_confirm[0].pattern.match("meal:abc123:s")
    assert not in_confirm[0].pattern.match("fav:l:1:n")
    # Outside the confirm step (after a save, a restart) a tap still answers.
    (stale,) = [
        h
        for h in handlers
        if isinstance(h, CallbackQueryHandler) and h.callback is tg.meal_stale_callback
    ]
    assert stale.pattern.match("meal:abc123:s")
