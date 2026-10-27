"""Favorites: save from a confirmed meal, list, log with one tap, undo, remove.

The service (``app.services.favorites``) runs on an in-memory SQLite DB; the bot
side (``confirm_meal`` with "⭐ Да, и в избранное", the inline-button callback and
the handler registration) runs through the real handlers with fake Telegram
objects.
"""

import asyncio
from datetime import UTC, datetime, timedelta
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
from app.models.user import User
from app.services import favorites as fv
from app.services import telegram as tg

_NUTRITION = {
    "foods": ["savory pastry"],
    "calories": 420.4,
    "protein": 12,
    "fats": 22.25,
    "carbs": 42,
}


@pytest.fixture
def Session(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(tg, "SessionLocal", factory)
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "42")
    yield factory
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def _user(db, telegram_id=42) -> User:
    user = User(telegram_id=telegram_id, username=f"u{telegram_id}")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _meal(db, user, **kw) -> Meal:
    meal = Meal(
        user_id=user.id,
        description=kw.get("description", "savory pastry"),
        meal_time=datetime.now(UTC),
        calories=kw.get("calories", 420.4),
        proteins=12,
        fats=22.25,
        carbohydrates=42,
        nutrients=_NUTRITION,
        photos=["PHOTO"],
    )
    db.add(meal)
    db.commit()
    db.refresh(meal)
    return meal


def _fav(db, user, name="Самса веган", **kw) -> FavoriteMeal:
    fav, _ = fv.save_from_meal(db, _meal(db, user, **kw), name)
    return fav


def _callbacks(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def _labels(markup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]


# --- naming ------------------------------------------------------------------


def test_name_prefers_caption_then_description_then_foods():
    assert (
        fv.favorite_name(caption="Самса веган\nс нефаршем", description="x", foods=[])
        == "Самса веган"
    )
    assert fv.favorite_name(caption="  ", description="сырники  2 шт", foods=None) == (
        "сырники 2 шт"
    )
    assert fv.favorite_name(caption=None, description="", foods=["a", "b"]) == "a, b"
    assert fv.favorite_name(caption=None, description=None, foods=None) == "Блюдо"


def test_name_is_capped():
    name = fv.favorite_name(caption="я" * 200, description=None, foods=None)
    assert len(name) == fv.NAME_MAX and name.endswith("…")


# --- save ----------------------------------------------------------------------


def test_save_copies_numbers_and_dedups_by_name_case_insensitively(Session):
    with Session() as db:
        user = _user(db)
        fav, created = fv.save_from_meal(db, _meal(db, user), "Самса веган")
        assert created
        assert (fav.calories, fav.proteins, fav.fats, fav.carbohydrates) == (
            420.4,
            12,
            22.25,
            42,
        )
        assert fav.nutrients == _NUTRITION and fav.use_count == 0
        # Cyrillic case differs: SQLite lower() would not fold it; the dedup must.
        again, created = fv.save_from_meal(db, _meal(db, user), "САМСА ВЕГАН")
        assert not created and again.id == fav.id
        assert db.query(FavoriteMeal).count() == 1


def test_save_after_removal_creates_a_fresh_favorite(Session):
    with Session() as db:
        user = _user(db)
        fav = _fav(db, user)
        fv.handle_callback(db, 42, f"fav:r:{fav.id}:0")
        again, created = fv.save_from_meal(db, _meal(db, user), "Самса веган")
        assert created and again.id != fav.id


# --- list ----------------------------------------------------------------------


def test_empty_list_points_to_the_confirm_button(Session):
    with Session() as db:
        screen = fv.open_list(db, 42)  # user row doesn't even exist yet
    assert "⭐ Да, и в избранное" in screen.text
    assert screen.markup is None


def test_list_is_most_recent_first_and_pages_by_four(Session):
    with Session() as db:
        user = _user(db)
        favs = [_fav(db, user, name=f"Блюдо {i}") for i in range(6)]
        base = datetime(2026, 10, 1, tzinfo=UTC)
        for i, f in enumerate(favs):
            f.created_at = base + timedelta(hours=i)
        favs[0].last_used_at = base + timedelta(days=5)  # used last → first
        db.commit()

        first = fv.open_list(db, 42, 0)
        labels = _labels(first.markup)
        assert labels[:4] == [
            "Блюдо 0 · 420 ккал",
            "Блюдо 5 · 420 ккал",
            "Блюдо 4 · 420 ккал",
            "Блюдо 3 · 420 ккал",
        ]
        # Pager on page 1 of 2: no ◀️ at the start.
        assert labels[4:] == ["1/2", "▶️"]
        assert _callbacks(first.markup)[-1] == "fav:p:1"

        second = fv.open_list(db, 42, 1)
        assert _labels(second.markup) == [
            "Блюдо 2 · 420 ккал",
            "Блюдо 1 · 420 ккал",
            "◀️",
            "2/2",
        ]
        # A page past the end (list shrank under an old button) clamps, not errors.
        assert _labels(fv.open_list(db, 42, 9).markup) == _labels(second.markup)


def test_single_page_has_no_pager(Session):
    with Session() as db:
        _fav(db, _user(db))
        assert _labels(fv.open_list(db, 42).markup) == ["Самса веган · 420 ккал"]


def test_list_shows_only_my_active_favorites(Session):
    with Session() as db:
        me, other = _user(db, 42), _user(db, 7)
        mine = _fav(db, me, name="Моё")
        _fav(db, other, name="Чужое")
        gone = _fav(db, me, name="Убранное")
        fv.handle_callback(db, 42, f"fav:r:{gone.id}:0")
        assert _callbacks(fv.open_list(db, 42).markup) == [f"fav:o:{mine.id}:0"]


def test_names_are_html_escaped(Session):
    with Session() as db:
        fav = _fav(db, _user(db), name="<b>самса</b> & чай")
        screen = fv.handle_callback(db, 42, f"fav:o:{fav.id}:0")
    assert "&lt;b&gt;самса&lt;/b&gt; &amp; чай" in screen.text


# --- open → log → undo -----------------------------------------------------------


def test_open_shows_macros_and_four_plain_chips(Session):
    with Session() as db:
        fav = _fav(db, _user(db))
        screen = fv.handle_callback(db, 42, f"fav:o:{fav.id}:1")
    assert "420 ккал · Б 12 · Ж 22.2 · У 42" in screen.text  # round(22.25, 1)
    assert _labels(screen.markup) == [
        "Сейчас",
        "Завтрак",
        "Обед",
        "Ужин",
        "⬅️ Назад",
        "✖️ Убрать",
    ]
    # "Назад" returns to the page the dish was opened from.
    assert _callbacks(screen.markup)[4] == "fav:p:1"


def test_tap_logs_a_meal_without_analysis_and_undo_deletes_it(Session):
    with Session() as db:
        user = _user(db)
        fav = _fav(db, user)
        meals_before = db.query(Meal).count()

        screen = fv.handle_callback(db, 42, f"fav:l:{fav.id}:l")

        assert screen.toast == "Записано"
        assert "Самса веган · 420 ккал" in screen.text and "Обед" in screen.text
        meal = db.query(Meal).order_by(Meal.id.desc()).first()
        assert db.query(Meal).count() == meals_before + 1
        assert meal.user_id == user.id
        assert meal.description == "Самса веган"
        assert meal.meal_type == "Обед"
        assert meal.calories == 420.4 and meal.nutrients == _NUTRITION
        assert not meal.photos and meal.ai_analysis is None
        assert meal.resolution_source == fv.FAVORITE_SOURCE
        assert meal.resolution_signals == {"favorite_id": fav.id}
        db.refresh(fav)
        assert fav.use_count == 1 and fav.last_used_at is not None
        assert _callbacks(screen.markup) == [f"fav:u:{meal.id}"]

        undone = fv.handle_callback(db, 42, f"fav:u:{meal.id}")
        assert undone.toast == "Отменено"
        assert db.get(Meal, meal.id) is None
        # A second tap on the same old button: a notice, not an error.
        again = fv.handle_callback(db, 42, f"fav:u:{meal.id}")
        assert "уже отменена" in again.text
        assert db.query(Meal).count() == meals_before


def test_now_chip_stores_no_meal_type(Session):
    with Session() as db:
        fav = _fav(db, _user(db))
        fv.handle_callback(db, 42, f"fav:l:{fav.id}:n")
        meal = db.query(Meal).filter(Meal.resolution_source == "favorite").one()
    assert meal.meal_type is None


def test_undo_never_deletes_an_analysed_meal(Session):
    # The undo button only ever carries a favorite-logged meal's id, but the data
    # comes from the client: a crafted id of an ordinary meal must not delete it.
    with Session() as db:
        ordinary = _meal(db, _user(db))
        screen = fv.handle_callback(db, 42, f"fav:u:{ordinary.id}")
        assert "уже отменена" in screen.text
        assert db.get(Meal, ordinary.id) is not None


def test_another_users_ids_are_refused(Session):
    with Session() as db:
        theirs = _fav(db, _user(db, 7), name="Чужое")
        _user(db, 42)
        their_meal = db.query(Meal).first()
        fv.handle_callback(db, 7, f"fav:l:{theirs.id}:n")
        their_logged = db.query(Meal).filter(Meal.resolution_source == "favorite").one()
        count = db.query(Meal).count()

        for data in (
            f"fav:o:{theirs.id}:0",
            f"fav:l:{theirs.id}:n",
            f"fav:r:{theirs.id}:0",
            f"fav:b:{theirs.id}:0",
            f"fav:u:{their_logged.id}",
        ):
            fv.handle_callback(db, 42, data)

        assert db.query(Meal).count() == count
        assert db.get(Meal, their_meal.id) is not None
        db.refresh(theirs)
        assert theirs.removed_at is None and theirs.use_count == 1


# --- remove → restore ------------------------------------------------------------


def test_remove_then_restore(Session):
    with Session() as db:
        fav = _fav(db, _user(db))
        removed = fv.handle_callback(db, 42, f"fav:r:{fav.id}:0")
        assert "убрано" in removed.text
        assert _callbacks(removed.markup) == [f"fav:b:{fav.id}:0", "fav:p:0"]
        assert fv.open_list(db, 42).markup is None  # gone from the list

        # A stale "open" or "log" button on the removed dish → the list + notice.
        stale = fv.handle_callback(db, 42, f"fav:l:{fav.id}:b")
        assert "уже нет в избранном" in stale.text
        assert db.query(Meal).filter(Meal.resolution_source == "favorite").count() == 0

        restored = fv.handle_callback(db, 42, f"fav:b:{fav.id}:0")
        assert "Снова в избранном" in restored.text
        assert _callbacks(fv.open_list(db, 42).markup) == [f"fav:o:{fav.id}:0"]


@pytest.mark.parametrize(
    "data", ["fav:", "fav:zz", "fav:o:abc:0", "fav:l:1:q", "fav:p"]
)
def test_malformed_data_gets_the_list(Session, data):
    with Session() as db:
        _fav(db, _user(db))
        screen = fv.handle_callback(db, 42, data)
    assert screen.text.startswith("<b>⭐ Избранное</b>")


# --- the bot side --------------------------------------------------------------


class _Msg:
    def __init__(self, text=None):
        self.text = text
        self.replies, self.kwargs = [], []

    async def reply_text(self, text, *args, **kwargs):
        self.replies.append(text)
        self.kwargs.append(kwargs)


def _confirm(text, current_meal):
    message = _Msg(text)
    update = SimpleNamespace(
        message=message, effective_user=SimpleNamespace(id=42, username="owner")
    )
    context = SimpleNamespace(
        user_data={"current_meal": current_meal, "meal_type": "Обед"}
    )
    state = asyncio.run(tg.confirm_meal(update, context))
    return state, message


def test_typed_confirm_intents():
    # The former reply-keyboard texts still classify when typed or tapped from
    # an old keyboard left on screen.
    assert tg._confirm_intent("✅ Да") == "affirm"
    assert tg._confirm_intent("⭐ Да, и в избранное") == "affirm_favorite"
    assert tg._confirm_intent("Нет") == "reject"


def test_main_keyboard_has_favorites_next_to_add_meal():
    assert [b.text for b in tg.main_keyboard.keyboard[0]] == [
        "🍽 Добавить прием пищи",
        "⭐ Избранное",
    ]


def test_confirm_with_favorite_saves_meal_and_favorite_named_by_caption(Session):
    meal = {
        "nutrition": _NUTRITION,
        "description": "savory pastry",
        "caption": "Самса веган",
        "photos": ["PHOTO"],
    }
    state, message = _confirm("⭐ Да, и в избранное", meal)

    assert state == tg.CHOOSING_ACTION
    assert "сохранен" in message.replies[-1]
    assert "⭐ В избранном: «Самса веган»" in message.replies[-1]
    with Session() as db:
        saved = db.query(Meal).one()
        fav = db.query(FavoriteMeal).one()
        assert fav.name == "Самса веган" and fav.source_meal_id == saved.id
        assert fav.calories == saved.calories == 420.4

    # The same dish again: one favorite, and the reply says so.
    _, message = _confirm("⭐ Да, и в избранное", dict(meal))
    assert "уже в избранном" in message.replies[-1]
    with Session() as db:
        assert db.query(Meal).count() == 2
        assert db.query(FavoriteMeal).count() == 1


def test_plain_yes_adds_no_favorite(Session):
    _confirm("✅ Да", {"nutrition": _NUTRITION, "description": "x"})
    with Session() as db:
        assert db.query(Meal).count() == 1
        assert db.query(FavoriteMeal).count() == 0


def test_favorite_failure_keeps_the_saved_meal(Session, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(fv, "save_from_meal", boom)
    state, message = _confirm(
        "⭐ Да, и в избранное", {"nutrition": _NUTRITION, "description": "x"}
    )
    assert state == tg.CHOOSING_ACTION
    assert "сохранен" in message.replies[-1]
    assert "не получилось" in message.replies[-1]
    assert "ошибка при сохранении" not in message.replies[-1]
    with Session() as db:
        assert db.query(Meal).count() == 1


def test_menu_button_sends_the_list(Session):
    with Session() as db:
        _fav(db, _user(db))
    message = _Msg("⭐ Избранное")
    update = SimpleNamespace(
        message=message, effective_user=SimpleNamespace(id=42, username="owner")
    )
    state = asyncio.run(tg.show_favorites(update, SimpleNamespace(user_data={})))
    assert state == tg.CHOOSING_ACTION
    assert message.replies[-1].startswith("<b>⭐ Избранное</b>")
    assert message.kwargs[-1]["parse_mode"] == "HTML"
    assert _labels(message.kwargs[-1]["reply_markup"]) == ["Самса веган · 420 ккал"]


def _query(data, telegram_id=42, edit=None):
    query = SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        edit_message_text=edit or AsyncMock(),
    )
    update = SimpleNamespace(
        callback_query=query, effective_user=SimpleNamespace(id=telegram_id)
    )
    return update, query


def test_callback_logs_edits_in_place_and_toasts(Session):
    with Session() as db:
        fav = _fav(db, _user(db))
    update, query = _query(f"fav:l:{fav.id}:d")
    asyncio.run(tg.favorites_callback(update, SimpleNamespace()))

    query.answer.assert_awaited_once_with("Записано")
    (text,), kwargs = query.edit_message_text.await_args
    assert "Записано" in text and "Ужин" in text
    assert kwargs["parse_mode"] == "HTML"
    with Session() as db:
        assert db.query(Meal).filter(Meal.meal_type == "Ужин").count() == 1


def test_callback_without_subscription_changes_nothing(Session, monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "1")
    monkeypatch.setattr(tg, "check_subscription", AsyncMock(return_value=False))
    with Session() as db:
        fav = _fav(db, _user(db))
    update, query = _query(f"fav:l:{fav.id}:d")
    asyncio.run(tg.favorites_callback(update, SimpleNamespace()))

    assert "подпиской" in query.answer.await_args.args[0]
    query.edit_message_text.assert_not_awaited()
    with Session() as db:
        assert db.query(Meal).filter(Meal.resolution_source == "favorite").count() == 0


def test_pager_counter_tap_only_answers(Session):
    update, query = _query("fav:x")
    asyncio.run(tg.favorites_callback(update, SimpleNamespace()))
    query.answer.assert_awaited_once()
    query.edit_message_text.assert_not_awaited()


def test_callback_ignores_message_not_modified(Session):
    edit = AsyncMock(side_effect=BadRequest("Message is not modified: same content"))
    update, query = _query("fav:p:0", edit=edit)
    asyncio.run(tg.favorites_callback(update, SimpleNamespace()))  # no raise
    edit.assert_awaited_once()


def test_callback_reraises_other_bad_requests(Session):
    edit = AsyncMock(side_effect=BadRequest("Can't parse entities"))
    update, query = _query("fav:p:0", edit=edit)
    with pytest.raises(BadRequest, match="parse entities"):
        asyncio.run(tg.favorites_callback(update, SimpleNamespace()))


def test_handlers_are_registered(monkeypatch):
    from telegram.ext import CallbackQueryHandler, ConversationHandler

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:fake")
    application = tg.create_bot_application()
    handlers = [h for group in application.handlers.values() for h in group]

    (fav_handler,) = [
        h
        for h in handlers
        if isinstance(h, CallbackQueryHandler) and h.callback is tg.favorites_callback
    ]
    assert fav_handler.pattern.match("fav:l:1:n")
    assert not fav_handler.pattern.match("model:x")

    (conv,) = [h for h in handlers if isinstance(h, ConversationHandler)]
    # Entry points: the menu works after a restart (state lost) without /start.
    entry = {getattr(h, "callback", None) for h in conv.entry_points}
    assert {tg.add_meal, tg.show_favorites} <= entry
    # The menu button texts match the entry-point filters exactly.
    by_callback = {h.callback: h for h in conv.entry_points}
    add_text, fav_text = (b.text for b in tg.main_keyboard.keyboard[0])
    assert by_callback[tg.show_favorites].filters.pattern.match(fav_text)
    assert by_callback[tg.add_meal].filters.pattern.match(add_text)
