"""The day card (stage 5, slice D): what was, in plain words, and nothing missing."""

import asyncio
import inspect
import re
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from telegram import Update

from app.core.config import settings
from app.db.base import Base
from app.models.meal import Meal
from app.models.user import User
from app.services import day_card, explain_screen
from app.services import stats_screen as st
from app.services import telegram as tg
from app.services.settings_screen import SHOW_NUMBERS

ZONE = ZoneInfo("Asia/Tbilisi")
# Fri 30 Oct 2026, 20:00 in Tbilisi (UTC+4).
NOW = datetime(2026, 10, 30, 16, 0, tzinfo=UTC)


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(settings, "DEFAULT_TIMEZONE", "Asia/Tbilisi")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine)() as session:
        session.add(User(id=1, telegram_id=42, settings={st.DAY_CARD_SEEN: True}))
        session.commit()
        yield session
    engine.dispose()


def _at(hour, minute=0, day=30):
    return datetime(2026, 10, day, hour, minute, tzinfo=ZONE).astimezone(UTC)


def _meal(db, when, name="еда", items=None, groups=None, calories=400):
    tags = {}
    if groups is not None:
        tags = {"food_items": items or [], "food_groups": groups}
    meal = Meal(
        user_id=1,
        description=name,
        meal_time=when,
        calories=calories,
        proteins=10,
        fats=5,
        carbohydrates=50,
        nutrients={},
        ai_analysis=tags,
    )
    db.add(meal)
    db.commit()
    return meal


def _card(db, tab=st.TODAY, opened=False):
    return st.open_view(db, 42, tab, NOW, opened)


def _rows(screen):
    return [[b.text for b in row] for row in screen.markup.inline_keyboard]


def _item(name, *groups):
    return {"name": name, "groups": list(groups)}


def test_the_card_says_when_and_which_groups_in_the_mocks_words(db):
    _meal(db, _at(8, 40), items=[_item("овсянка", "grain")], groups=["grain"])
    _meal(db, _at(13, 15), items=[_item("тофу", "anchor")], groups=["anchor"])
    _meal(db, _at(18, 30), items=[_item("чечевица", "anchor")], groups=["anchor"])

    assert _card(db).text.split("\n")[:5] == [
        "<b>📊 Сегодня, пт 30 октября</b>",
        "",
        "🕐 8:40 · 13:15 · 18:30",
        "🌾 Цельные злаки: овсянка утром",
        "⚓ Белковая опора: тофу в обед · чечевица вечером",
    ]


def test_a_group_with_nothing_behind_it_gets_no_line_at_all(db):
    _meal(db, _at(9), items=[_item("яблоко", "fruit")], groups=["fruit"])
    text = _card(db).text
    assert "Цельные злаки" not in text and "Белковая опора" not in text
    assert "🍽 Яблоко" in text  # named as food, not as a group (G5-R07, G5-R28)


def test_the_user_decides_not_the_analysis(db):
    # Untagged by hand: the item still says «anchor», the meal does not.
    _meal(db, _at(9), items=[_item("тофу", "anchor")], groups=[])
    assert "Белковая опора" not in _card(db).text


def test_a_group_ticked_by_hand_falls_back_to_the_meals_name(db):
    _meal(db, _at(13), name="суп <дня>", items=[_item("суп", "veg")], groups=["anchor"])
    assert "⚓ Белковая опора: суп &lt;дня&gt; в обед" in _card(db).text


def test_an_item_name_is_escaped(db):
    _meal(db, _at(9), items=[_item("<b>тофу</b>", "anchor")], groups=["anchor"])
    assert "⚓ Белковая опора: &lt;b&gt;тофу&lt;/b&gt; утром" in _card(db).text


def test_the_same_food_twice_in_one_part_of_the_day_is_named_once(db):
    for minute in (0, 30):
        _meal(db, _at(9, minute), items=[_item("Тофу", "anchor")], groups=["anchor"])
    _meal(db, _at(19), items=[_item("тофу", "anchor")], groups=["anchor"])
    assert "⚓ Белковая опора: Тофу утром · тофу вечером" in _card(db).text


def test_a_long_list_is_cut_and_counted_and_a_long_name_shortened(db):
    names = ["тофу", "нут", "чечевица", "темпе", "фасоль", "сейтан"]
    items = [_item(name, "anchor") for name in names]
    _meal(db, _at(9), items=items, groups=["anchor"])
    line = next(x for x in _card(db).text.split("\n") if x.startswith("⚓"))
    assert line == "⚓ Белковая опора: тофу, нут, чечевица, темпе утром · ещё 2"

    long_name = "очень длинное название одного блюда из кафе"
    assert len(day_card.short(long_name)) == day_card.NAME_MAX
    assert day_card.short(long_name).endswith("…")


@pytest.mark.parametrize(
    "hour,word",
    [(3, "ночью"), (5, "утром"), (11, "в обед"), (15, "днём"), (18, "вечером")],
)
def test_parts_of_the_day_start_where_the_design_says(hour, word):
    assert day_card._part_of_day(hour) == word


def test_a_long_gap_is_described_in_words_without_hours(db):
    _meal(db, _at(8, 40))
    _meal(db, _at(9, 40))
    _meal(db, _at(18, 30))
    line = next(x for x in _card(db).text.split("\n") if x.startswith("🕐"))
    assert line == "🕐 8:40 · 9:40 · 18:30, днём длинный перерыв"


def test_two_meals_get_no_gap_line_the_times_already_show_it(db):
    _meal(db, _at(14))
    _meal(db, _at(20))
    assert "перерыв" not in _card(db).text


def test_sleep_is_not_a_gap_but_a_gap_after_it_still_is(db):
    _meal(db, _at(0, 30))
    _meal(db, _at(3, 15))
    _meal(db, _at(16))
    assert "перерыв" not in _card(db).text
    _meal(db, _at(22))
    assert "🕐 0:30 · 3:15 · 16:00 · 22:00, вечером длинный перерыв" in _card(db).text


def test_under_six_hours_is_no_gap_and_six_is(db):
    _meal(db, _at(9))
    _meal(db, _at(14, 59))
    assert "перерыв" not in _card(db).text
    _meal(db, _at(20, 59))
    assert "перерыв" in _card(db).text


def test_one_meal_has_no_gap_and_a_meal_without_time_shows_no_time(db):
    # The column defaults to now: only a row written elsewhere can lack a time.
    timed = _meal(db, _at(9))
    timeless = _meal(db, _at(10), items=[_item("тофу", "anchor")], groups=["anchor"])
    timeless.meal_time = None
    assert day_card.facts(
        [timed, timeless], ZONE, lambda m: m.description
    ) == day_card.DayFacts(times=("9:00",), anchor="тофу", foods="еда")


def test_the_numbers_are_collapsed_and_there_is_no_goal(db):
    _meal(db, _at(9), name="овсянка", calories=380)
    closed = _card(db)
    assert "ккал" not in closed.text and "овсянка" not in closed.text
    opened = _card(db, opened=True)
    assert "<blockquote expandable>🔢 За день: ≈ 380 ккал" in opened.text
    assert "9:00 овсянка — ≈ 380 ккал</blockquote>" in opened.text
    for word in ("осталось", "цель", "норма", "кг"):
        assert word not in opened.text.casefold()


def test_the_numbers_setting_opens_them_without_a_button(db):
    user = db.get(User, 1)
    user.settings = {**user.settings, SHOW_NUMBERS: True}
    db.commit()
    _meal(db, _at(9))
    card = _card(db)
    assert "🔢 За день" in card.text
    assert _rows(card)[-3:] == [
        [st.STEP_FIRST_BUTTON],
        ["🍽 Приёмы"],
        ["✓ Сегодня", "Вчера", "7 дней"],
    ]


def test_tabs_are_the_last_row_under_ticks_and_numbers(db):
    _meal(db, _at(9))
    assert _rows(_card(db))[-1] == ["✓ Сегодня", "Вчера", "7 дней"]
    assert _rows(_card(db))[-2] == ["🔢 Цифры", "🍽 Приёмы"]
    assert _rows(_card(db, st.YESTERDAY)) == [["Сегодня", "✓ Вчера", "7 дней"]]


def test_an_empty_today_invites_and_an_empty_yesterday_does_not(db):
    today, yesterday = _card(db), _card(db, st.YESTERDAY)
    assert today.text.split("\n") == [
        "<b>📊 Сегодня, пт 30 октября</b>",
        "",
        "🍽 Похоже, не всё записано? Пришли фото или напиши, что было, — "
        "карточка соберётся сама.",
    ]
    assert today.markup.inline_keyboard[0][0].callback_data == st.ADD_DATA
    assert yesterday.text.endswith("За этот день записей нет.")
    assert "🍽 Добавить приём пищи" not in sum(_rows(yesterday), [])
    for screen in (today, yesterday):
        assert "🔢 Цифры" not in sum(_rows(screen), [])
        assert "0 ккал" not in screen.text


def test_the_intro_is_shown_once_on_a_card_with_food(db):
    user = db.get(User, 1)
    user.settings = {}
    db.commit()
    intro = "<i>Это карточка дня: что было, без оценок. Шаг внизу — по желанию.</i>"
    assert intro not in _card(db).text  # an empty card does not use it up
    _meal(db, _at(9))
    assert _card(db).text.split("\n")[2] == intro
    assert intro not in _card(db).text
    assert db.get(User, 1).settings[st.DAY_CARD_SEEN] is True


def test_the_intro_keeps_the_users_other_settings(db):
    user = db.get(User, 1)
    user.settings = {"diet": "veg"}
    db.commit()
    _meal(db, _at(9))
    _card(db)
    saved = db.get(User, 1).settings
    assert saved["diet"] == "veg" and saved[st.DAY_CARD_SEEN] is True


def test_add_outside_the_conversation_explains_and_shows_the_card(db):
    screen = st.handle_callback(db, 42, st.ADD_DATA, NOW)
    assert screen.toast == "Сначала заверши текущий шаг или нажми /cancel"
    assert screen.text.startswith("<b>📊 Сегодня")


def test_yesterday_uses_yesterdays_meals_only(db):
    _meal(db, _at(9, day=29), items=[_item("тофу", "anchor")], groups=["anchor"])
    today, yesterday = _card(db), _card(db, st.YESTERDAY)
    assert "тофу" not in today.text
    assert yesterday.text.startswith("<b>📊 Вчера, чт 29 октября</b>")
    assert "⚓ Белковая опора: тофу утром" in yesterday.text


# --- the hard day: the card records it, plainly and without a verdict ----------


def test_a_day_of_chips_is_the_times_and_the_chips(db):
    chips = [_item("чипсы")]
    _meal(db, _at(14), name="чипсы", items=chips, groups=[], calories=1400)
    _meal(db, _at(20), name="чипсы", items=chips, groups=[], calories=1400)
    assert _card(db).text.split("\n")[2:4] == ["🕐 14:00 · 20:00", "🍽 Чипсы"]
    assert _card(db).picture["foods"] == "Чипсы"
    assert "ккал" not in _card(db).text and "2800" not in _card(db).text


def test_foods_already_in_a_group_line_are_not_named_twice(db):
    items = [_item("тофу", "anchor"), _item("чипсы")]
    _meal(db, _at(13), name="обед", items=items, groups=["anchor"])
    text = _card(db).text
    assert "⚓ Белковая опора: тофу в обед\n🍽 Ещё: чипсы" in text
    assert _card(db).picture["foods"] == "Ещё: чипсы"


def test_a_day_with_everything_in_groups_has_no_food_line(db):
    _meal(db, _at(9), items=[_item("овсянка", "grain")], groups=["grain"])
    assert "🍽" not in _card(db).text


def test_a_meal_without_analysis_is_named_by_its_own_name(db):
    _meal(db, _at(9), name="<лапша> быстрая")
    assert "🍽 &lt;лапша&gt; быстрая" in _card(db).text


def test_a_hand_ticked_meal_is_not_named_again_by_its_items(db):
    _meal(db, _at(13), name="суп дня", items=[_item("суп", "veg")], groups=["anchor"])
    assert "🍽" not in _card(db).text


def test_a_long_day_of_foods_is_cut_and_counted(db):
    names = ["лапша", "чипсы", "печенье", "пицца", "кола", "чипсы", "конфеты"]
    for hour, name in enumerate(names, 8):
        _meal(db, _at(hour), name=name, items=[_item(name)], groups=[])
    assert "🍽 Лапша, чипсы, печенье, пицца, ещё 2" in _card(db).text


def test_the_food_line_has_no_verdict_words(db):
    _meal(db, _at(4), name="чипсы", items=[_item("чипсы")], groups=[], calories=3000)
    text = _card(db).text.casefold()
    assert not re.search(r"поздно|ночной|вредн|полезн|переработ|жир", text)


def test_no_card_text_names_what_is_missing(db):
    _meal(db, _at(8))
    _meal(db, _at(20))
    text = _card(db).text.casefold()
    assert not re.search(r"пропущ|пропуск|не хватает|мало|недо|\d+\s*ч\b", text)


# --- the button, through the real handlers ------------------------------------


def _tap_update(data):
    return Update.de_json(
        {
            "update_id": 1,
            "callback_query": {
                "id": "1",
                "from": {"id": 42, "is_bot": False, "first_name": "J"},
                "chat_instance": "1",
                "data": data,
                "message": {
                    "message_id": 1,
                    "date": 0,
                    "chat": {"id": 42, "type": "private"},
                },
            },
        },
        None,
    )


@pytest.fixture
def conv(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "1:fake", raising=False)
    application = tg.create_bot_application()
    return next(
        h for h in application.handlers[0] if isinstance(h, tg.ConversationHandler)
    )


def _first(handlers, update):
    return next((h for h in handlers if h.check_update(update)), None)


def _target(handler):
    return inspect.unwrap(handler.callback) if handler else None


def test_the_add_button_enters_the_flow_from_idle_and_from_the_menu_states(conv):
    tap = _tap_update(st.ADD_DATA)
    assert _target(_first(conv.entry_points, tap)) is tg.add_meal_from_card
    for state in ("CHOOSING_ACTION", "ENTERING_WEIGHT", "ADDING_MEAL"):
        handlers = conv.states[getattr(tg, state)]
        assert _target(_first(handlers, tap)) is tg.add_meal_from_card


@pytest.mark.parametrize("state", ["ADDING_MEAL_TIME", "CONFIRMING_MEAL"])
def test_the_add_button_does_not_break_a_step_in_progress(conv, state):
    assert _first(conv.states[getattr(tg, state)], _tap_update(st.ADD_DATA)) is None


def test_a_tab_is_not_taken_for_the_add_button(conv):
    assert _first(conv.entry_points, _tap_update("st:t")) is None


def _context():
    return SimpleNamespace(
        user_data={"current_meal": {"calories": 1}, "meal_time": 1, "meal_type": "x"}
    )


def test_tapping_add_asks_the_same_question_with_a_clean_draft(monkeypatch):
    monkeypatch.setattr(tg, "check_subscription", AsyncMock(return_value=True))
    sent = AsyncMock()
    query = SimpleNamespace(answer=AsyncMock())
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(send_message=sent),
    )
    context = _context()

    state = asyncio.run(tg.add_meal_from_card(update, context))

    assert state == tg.ADDING_MEAL_TIME
    query.answer.assert_awaited_once_with()
    assert sent.await_args.args == ("Когда был приём пищи?",)
    assert sent.await_args.kwargs["reply_markup"] is tg.time_keyboard
    assert context.user_data == {"current_meal": {}}


def test_without_a_subscription_add_is_refused_and_nothing_starts(monkeypatch):
    monkeypatch.setattr(tg, "check_subscription", AsyncMock(return_value=False))
    sent = AsyncMock()
    query = SimpleNamespace(answer=AsyncMock())
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(send_message=sent),
    )

    assert asyncio.run(tg.add_meal_from_card(update, _context())) is None
    assert query.answer.await_args.kwargs == {"show_alert": True}
    sent.assert_not_awaited()


def test_the_card_explains_itself_in_the_topics_list():
    topic = explain_screen.TOPICS["day"]
    assert topic.label == "📊 Карточка дня"
    assert "Sivyer" in topic.source
    assert "без счёта часов" in topic.how
