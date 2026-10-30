"""🧺 Надёжная еда and ✋ Не предлагать (stage 5, slice G, G5-R12, G5-R13).

Screens over a real in-memory database; the typed step through the real
ConversationHandler, and both lists through ``next_step``: a list the step does not
read would look just as finished on its own screen.
"""

import asyncio
import inspect
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from telegram import Update

from app.core.config import settings
from app.db.base import Base
from app.models.favorite_meal import FavoriteMeal
from app.models.meal import Meal
from app.models.user import User
from app.services import food_lists as fl
from app.services import next_step as ns
from app.services import settings_screen, stats_screen
from app.services import telegram as tg

NOW = datetime(2026, 10, 28, 8, 0, tzinfo=UTC)
DAY = date(2026, 10, 28)
TG = 42


@pytest.fixture
def Session(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(tg, "SessionLocal", factory)
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", str(TG))
    monkeypatch.setattr(settings, "DEFAULT_TIMEZONE", "Asia/Tbilisi")
    with factory() as db:
        db.add(User(telegram_id=TG, username="u"))
        db.commit()
    yield factory
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def _labels(screen) -> list[str]:
    return [b.text for row in screen.markup.inline_keyboard for b in row]


def _data(screen, label: str) -> str:
    for row in screen.markup.inline_keyboard:
        for button in row:
            if label in button.text:
                return button.callback_data
    raise AssertionError(f"no button {label!r} in {_labels(screen)}")


def _user(db) -> User:
    return db.query(User).filter(User.telegram_id == TG).one()


def _tap(Session, data: str):
    with Session() as db:
        return fl.handle_callback(db, TG, data, NOW)


def _setting(Session, key: str):
    with Session() as db:
        return (_user(db).settings or {}).get(key)


def _put(Session, **values) -> None:
    with Session() as db:
        user = _user(db)
        user.settings = {**(user.settings or {}), **values}
        db.commit()


def _submit(Session, kind: str, text: str):
    with Session() as db:
        return fl.submit_food(db, TG, kind, text)


def _favorite(db, name: str, removed: bool = False) -> None:
    db.add(
        FavoriteMeal(
            user_id=_user(db).id,
            name=name,
            removed_at=NOW if removed else None,
        )
    )
    db.commit()


def _meals(db, name: str, times: int, days_ago: int = 1) -> None:
    moment = NOW - timedelta(days=days_ago)
    db.add_all(
        Meal(user_id=_user(db).id, description=name, meal_time=moment)
        for _ in range(times)
    )
    db.commit()


# --- ✋ Не предлагать --------------------------------------------------------


def test_the_never_screen_starts_empty_and_waits_for_a_typed_food(Session):
    with Session() as db:
        screen, kind, wait = fl.ask_food(db, TG, fl.NEVER_OPEN, NOW)
    assert screen.text.startswith("<b>✋ Не предлагать</b>")
    assert "Пока пусто" in screen.text
    assert "напиши одним сообщением" in screen.text
    assert kind == ns.NO_SUGGEST and wait is True
    assert _labels(screen) == ["⬅️ Настройки"]
    assert _data(screen, "Настройки") == "set:open"


def test_a_typed_food_goes_on_the_list_and_the_screen_keeps_waiting(Session):
    screen, wait = _submit(Session, ns.NO_SUGGEST, "  Грибы ")
    assert _setting(Session, ns.NO_SUGGEST) == ["Грибы"]
    assert wait is True
    assert screen.text.startswith("<i>✓ Добавлено: Грибы</i>")
    assert "✖️ Грибы" in _labels(screen)
    _, wait = _submit(Session, ns.NO_SUGGEST, "молоко")
    assert _setting(Session, ns.NO_SUGGEST) == ["Грибы", "молоко"]


@pytest.mark.parametrize(
    "typed", ["грибы, молоко", "грибы и молоко", "грибы\nмолоко", "грибы; молоко"]
)
def test_several_foods_in_one_message_are_several_entries(Session, typed):
    # As one entry «молоко» would be honoured only next to «грибы»: every word of an
    # entry has to match, so the refused food would still be suggested.
    _submit(Session, ns.NO_SUGGEST, typed)
    assert _setting(Session, ns.NO_SUGGEST) == ["грибы", "молоко"]


def test_a_food_typed_twice_is_one_entry_whatever_its_case(Session):
    _submit(Session, ns.NO_SUGGEST, "Лён")
    screen, wait = _submit(Session, ns.NO_SUGGEST, "лен")
    assert _setting(Session, ns.NO_SUGGEST) == ["Лён"]
    assert "Уже в списке" in screen.text
    assert wait is True


@pytest.mark.parametrize("typed", ["", "   ", "!!!", "72,5", "ы" * (fl.NAME_MAX + 1)])
def test_what_is_not_a_food_asks_again_and_saves_nothing(Session, typed):
    screen, wait = _submit(Session, ns.NO_SUGGEST, typed)
    assert wait is True
    assert not _setting(Session, ns.NO_SUGGEST)
    assert not screen.text.startswith("<b>")


def test_html_in_a_typed_food_is_printed_not_read(Session):
    screen, _ = _submit(Session, ns.NO_SUGGEST, "<b>сыр</b>")
    assert "<b>сыр</b>" not in screen.text
    assert "&lt;b&gt;сыр&lt;/b&gt;" in screen.text


def test_a_refused_food_leaves_the_step_through_the_saved_settings(Session):
    _put(Session, **{ns.FOCUS: {"id": ns.EXTRAS}})
    with Session() as db:
        first = ns.view(ns.current(_user(db).settings, DAY, lambda: {})).step
    assert first is not None and first.foods
    _submit(Session, ns.NO_SUGGEST, first.foods[0])
    with Session() as db:
        after = ns.view(ns.current(_user(db).settings, DAY, lambda: {})).step
    assert after is not None and after.id != first.id
    assert first.foods[0] not in after.foods


def test_removing_from_never_applies_at_once_and_comes_back_by_one_tap(Session):
    screen, _ = _submit(Session, ns.NO_SUGGEST, "грибы, молоко")
    with Session() as db:
        removed, kind, wait = fl.ask_food(db, TG, _data(screen, "✖️ грибы"), NOW)
    assert kind == ns.NO_SUGGEST and wait is True
    assert _setting(Session, ns.NO_SUGGEST) == ["молоко"]
    assert removed.text.startswith("<i>Убрано: грибы</i>")
    with Session() as db:
        back, _, _ = fl.ask_food(db, TG, _data(removed, "↩️ Вернуть"), NOW)
    assert sorted(_setting(Session, ns.NO_SUGGEST)) == ["грибы", "молоко"]
    assert back.text.startswith("<i>✓ Вернулось: грибы</i>")


def test_a_double_tap_on_remove_keeps_the_undo_and_an_old_undo_adds_nothing(Session):
    screen, _ = _submit(Session, ns.NO_SUGGEST, "грибы")
    data = _data(screen, "✖️ грибы")
    with Session() as db:
        first, _, _ = fl.ask_food(db, TG, data, NOW)
        second, _, _ = fl.ask_food(db, TG, data, NOW)
    assert "↩️ Вернуть «грибы»" in _labels(second)
    undo = _data(first, "↩️ Вернуть")
    with Session() as db:
        fl.ask_food(db, TG, undo, NOW)
        fl.ask_food(db, TG, undo, NOW)
    assert _setting(Session, ns.NO_SUGGEST) == ["грибы"]


def test_no_food_name_travels_in_callback_data(Session):
    screen, _ = _submit(Session, ns.NO_SUGGEST, "очень длинное название еды тут")
    for row in screen.markup.inline_keyboard:
        for button in row:
            assert "еды" not in button.callback_data
            assert len(button.callback_data.encode()) <= 64


def test_a_full_never_list_says_so_and_saves_nothing_more(Session):
    full = [f"еда{i}" for i in range(fl.MAX_FOODS)]
    _put(Session, **{ns.NO_SUGGEST: full})
    screen, _ = _submit(Session, ns.NO_SUGGEST, "грибы")
    assert _setting(Session, ns.NO_SUGGEST) == full
    assert f"уже {fl.MAX_FOODS}" in screen.text


# --- 🧺 Надёжная еда --------------------------------------------------------


def test_the_list_seeds_from_favourites_and_repeated_meals(Session):
    with Session() as db:
        _favorite(db, "Хумус с хлебом")
        _favorite(db, "Убранное", removed=True)
        _meals(db, "Овсянка с бананом", fl.REPEATED_AT_LEAST)
        _meals(db, "Редкое блюдо", fl.REPEATED_AT_LEAST - 1)
        _meals(db, "Давнее блюдо", fl.REPEATED_AT_LEAST, days_ago=fl.SEED_DAYS + 1)
    screen = _tap(Session, fl.RELIABLE_OPEN)
    assert screen.text.startswith("<b>🧺 Надёжная еда</b>")
    assert _setting(Session, ns.RELIABLE) == ["Хумус с хлебом", "Овсянка с бананом"]
    assert "Хумус с хлебом" in screen.text and "Овсянка с бананом" in screen.text
    assert "Редкое" not in screen.text and "Убранное" not in screen.text
    assert "Давнее" not in screen.text
    assert "⭐ Избранного" in screen.text
    assert _labels(screen) == ["➕ Добавить", "➖ Убрать", "⬅️ Настройки"]


def test_spelling_does_not_split_a_repeated_meal(Session):
    with Session() as db:
        _meals(db, "Свёкла", 1)
        _meals(db, "свекла ", 1)
        _meals(db, "СВЕКЛА", 1)
    _tap(Session, fl.RELIABLE_OPEN)
    assert len(_setting(Session, ns.RELIABLE)) == 1


def test_ready_food_and_a_long_analysis_line(Session):
    with Session() as db:
        _meals(db, "Пельмени из заморозки", fl.REPEATED_AT_LEAST)
        _meals(db, "ч" * (fl.NAME_MAX + 1), fl.REPEATED_AT_LEAST)
    _tap(Session, fl.RELIABLE_OPEN)
    assert _setting(Session, ns.RELIABLE) == ["Пельмени из заморозки"]


def test_a_removed_food_is_not_seeded_again_until_it_is_brought_back(Session):
    with Session() as db:
        _favorite(db, "Хумус")
    _tap(Session, fl.RELIABLE_OPEN)
    picker = _tap(Session, fl.REMOVE_OPEN)
    assert picker.text.startswith("<b>🧺 Что убрать?</b>")
    removed = _tap(Session, _data(picker, "Хумус"))
    assert _setting(Session, ns.RELIABLE) == []
    assert removed.text.startswith("<i>Убрано: Хумус</i>")
    assert _setting(Session, ns.RELIABLE) == []
    again = _tap(Session, fl.RELIABLE_OPEN)
    assert _setting(Session, ns.RELIABLE) == []
    assert "Хумус" not in again.text
    _tap(Session, _data(removed, "↩️ Вернуть"))
    assert _setting(Session, ns.RELIABLE) == ["Хумус"]


def test_a_removed_food_typed_again_is_back_for_good(Session):
    with Session() as db:
        _favorite(db, "Хумус")
    _tap(Session, fl.RELIABLE_OPEN)
    _tap(Session, _data(_tap(Session, fl.REMOVE_OPEN), "Хумус"))
    _submit(Session, ns.RELIABLE, "хумус")
    assert _setting(Session, ns.RELIABLE) == ["хумус"]
    _tap(Session, fl.RELIABLE_OPEN)
    assert _setting(Session, ns.RELIABLE) == ["хумус"]


def test_a_typed_dish_stays_one_entry_and_returns_to_the_list(Session):
    screen, wait = _submit(Session, ns.RELIABLE, "Лаваш с сыром и зеленью")
    assert _setting(Session, ns.RELIABLE) == ["Лаваш с сыром и зеленью"]
    assert wait is False
    assert screen.text.startswith("<i>✓ Добавлено: Лаваш с сыром и зеленью</i>")
    assert "<b>🧺 Надёжная еда</b>" in screen.text


def test_seeding_stops_at_the_cap_and_the_list_keeps_what_the_user_typed(Session):
    _put(Session, **{ns.RELIABLE: ["своё"]})
    with Session() as db:
        for index in range(fl.MAX_FOODS + 3):
            _favorite(db, f"блюдо {index}")
    _tap(Session, fl.RELIABLE_OPEN)
    found = _setting(Session, ns.RELIABLE)
    assert len(found) == fl.MAX_FOODS and found[0] == "своё"


def test_the_add_button_goes_when_the_list_is_full_and_an_old_one_says_why(Session):
    _put(Session, **{ns.RELIABLE: [f"еда{i}" for i in range(fl.MAX_FOODS)]})
    screen = _tap(Session, fl.RELIABLE_OPEN)
    assert "➕ Добавить" not in _labels(screen)
    with Session() as db:
        old, _, wait = fl.ask_food(db, TG, fl.ADD, NOW)
    assert wait is False and old.toast


def test_an_empty_reliable_list_says_what_will_fill_it(Session):
    screen = _tap(Session, fl.RELIABLE_OPEN)
    assert "Пока пусто" in screen.text
    assert _labels(screen) == ["➕ Добавить", "⬅️ Настройки"]


def test_a_stale_remove_shows_the_list_with_a_note(Session):
    screen = _tap(Session, f"{fl.CALLBACK_PREFIX}rx:deadbeef")
    assert screen.toast and "<b>🧺 Надёжная еда</b>" in screen.text


@pytest.mark.parametrize("data", ["fl:", "fl:nope", "fl:rx", "fl:ru:", "fl:x:y:z"])
def test_unknown_data_is_a_screen_not_a_crash(Session, data):
    assert _tap(Session, data).text


def test_damaged_stored_lists_do_not_cost_the_user_the_screen(Session):
    _put(Session, **{ns.RELIABLE: "не список", ns.NO_SUGGEST: [1, None, "грибы"]})
    assert "<b>🧺 Надёжная еда</b>" in _tap(Session, fl.RELIABLE_OPEN).text
    with Session() as db:
        screen, _, _ = fl.ask_food(db, TG, fl.NEVER_OPEN, NOW)
    assert "грибы" in screen.text


# --- the step reads the list ------------------------------------------------


@pytest.mark.parametrize(
    "dish, step",
    [
        ("Овсянка с бананом", "fruit"),
        ("Йогурт с орехами", "walnut"),
        ("Хлеб с тахини", "bread"),
    ],
)
def test_a_dish_on_the_list_counts_for_a_step_naming_one_of_its_foods(dish, step):
    assert ns._relies_on(ns._BY_ID[step], dish)


@pytest.mark.parametrize(
    "dish, step",
    [
        ("Пельмени из заморозки", "fruit"),
        ("Суп с лапшой", "seeds"),  # «с» must not reach «семечки»
    ],
)
def test_a_dish_without_the_steps_food_does_not_count(dish, step):
    assert not ns._relies_on(ns._BY_ID[step], dish)


@pytest.mark.parametrize("mode", [ns.LOW, ns.SIMPLE])
def test_low_modes_take_a_step_from_a_whole_dish_on_the_list(mode):
    settings = {
        ns.ENERGY: mode,
        ns.FOCUS: {"id": ns.PLANTS},
        ns.RELIABLE: ["Овсянка с бананом"],
    }
    assert ns.view(ns.current(settings, DAY, lambda: {})).step.id == "fruit"


def test_the_list_does_not_steer_the_normal_mode():
    plain = {ns.FOCUS: {"id": ns.EXTRAS}}
    listed = {**plain, ns.RELIABLE: ["Йогурт с семечками"]}
    days = {"nuts": 5, "grain": 0, "omega": 5}
    assert (
        ns.view(ns.current(listed, DAY, lambda: days)).step.id
        == ns.view(ns.current(plain, DAY, lambda: days)).step.id
    )


def test_never_wins_over_reliable():
    settings = {
        ns.ENERGY: ns.LOW,
        ns.FOCUS: {"id": ns.PLANTS},
        ns.RELIABLE: ["Овсянка с бананом"],
        ns.NO_SUGGEST: ["банан"],
    }
    assert ns.view(ns.current(settings, DAY, lambda: {})).step.id != "fruit"


def test_the_low_mode_seeds_before_it_picks_with_nothing_opened_first(Session):
    # Nobody has to visit the list screen for the favourites to be read.
    with Session() as db:
        _favorite(db, "Овсянка с бананом")
        _meals(db, "Завтрак", 1, days_ago=0)
    _put(Session, **{ns.FOCUS: {"id": ns.PLANTS}, ns.ENERGY: ns.LOW})
    with Session() as db:
        stats_screen.open_view(db, TG, now=NOW)
        assert _user(db).settings[ns.RELIABLE] == ["Овсянка с бананом"]
        assert _user(db).settings[ns.STATE]["step"] == "fruit"


def test_the_normal_mode_does_not_seed(Session):
    with Session() as db:
        _favorite(db, "Овсянка с бананом")
        _meals(db, "Завтрак", 1, days_ago=0)
    _put(Session, **{ns.FOCUS: {"id": ns.PLANTS}})
    with Session() as db:
        stats_screen.open_view(db, TG, now=NOW)
    assert _setting(Session, ns.RELIABLE) is None
    assert _setting(Session, ns.STATE)  # the step was looked at, and nothing seeded


# --- ⚙️ Настройки -----------------------------------------------------------


def test_the_hub_counts_both_lists_and_opens_them(Session):
    with Session() as db:
        _favorite(db, "Хумус")
    _put(Session, **{ns.NO_SUGGEST: ["грибы", "молоко"]})
    with Session() as db:
        screen = settings_screen.open_view(db, TG, now=NOW)
    assert "🧺 Надёжная еда: 1 · ✋ Не предлагать: 2" in screen.text
    assert _data(screen, "🧺 Надёжная еда") == fl.RELIABLE_OPEN
    assert _data(screen, "✋ Не предлагать") == fl.NEVER_OPEN
    assert len(screen.markup.inline_keyboard) <= 5


def test_the_hub_says_when_the_lists_are_empty(Session):
    with Session() as db:
        screen = settings_screen.open_view(db, TG, now=NOW)
    assert "🧺 Надёжная еда: пока нет · ✋ Не предлагать: пока нет" in screen.text


# --- the typed step, through the real ConversationHandler --------------------


@pytest.fixture
def conv(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "1:fake", raising=False)
    application = tg.create_bot_application()
    return next(
        h for h in application.handlers[0] if isinstance(h, tg.ConversationHandler)
    )


def _text_update(text):
    return Update.de_json(
        {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "date": 0,
                "chat": {"id": TG, "type": "private"},
                "from": {"id": TG, "is_bot": False, "first_name": "u"},
                "text": text,
            },
        },
        None,
    )


def _tap_update(data):
    return Update.de_json(
        {
            "update_id": 1,
            "callback_query": {
                "id": "1",
                "chat_instance": "1",
                "data": data,
                "from": {"id": TG, "is_bot": False, "first_name": "u"},
                "message": {
                    "message_id": 1,
                    "date": 0,
                    "chat": {"id": TG, "type": "private"},
                },
            },
        },
        None,
    )


def _first_match(handlers, update):
    for handler in handlers:
        if handler.check_update(update):
            return handler
    return None


def _is(handler, function) -> bool:
    found = inspect.unwrap(handler.callback) if handler else None
    return found is inspect.unwrap(function)


@pytest.mark.parametrize("data", [fl.ADD, fl.NEVER_OPEN, "fl:nx:abc", "fl:nu:abc"])
def test_a_tap_that_waits_for_text_starts_from_every_kind_of_state(conv, data):
    tap = _tap_update(data)
    assert _is(_first_match(conv.entry_points, tap), tg.food_ask_name)
    for state in (
        tg.CHOOSING_ACTION,
        tg.ENTERING_ROUTINE_NAME,
        tg.ENTERING_WEIGHT,
        tg.ENTERING_FOOD_NAME,
    ):
        assert _is(_first_match(conv.states[state], tap), tg.food_ask_name), state


def test_other_list_taps_stay_outside_the_conversation(conv):
    tap = _tap_update(fl.RELIABLE_OPEN)
    assert _first_match(conv.entry_points, tap) is None
    assert _first_match(conv.states[tg.CHOOSING_ACTION], tap) is None


def test_in_the_food_step_text_is_the_food_and_menu_buttons_still_work(conv):
    handlers = conv.states[tg.ENTERING_FOOD_NAME]
    assert _is(_first_match(handlers, _text_update("грибы")), tg.process_food_name)
    assert _is(
        _first_match(handlers, _text_update("📊 Статистика")), tg.show_statistics
    )
    assert _is(
        _first_match(handlers, _tap_update(fl.RELIABLE_OPEN)), tg.food_leave_name
    )


def _update(text=None, data=None):
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    query = None
    if data is not None:
        query = SimpleNamespace(
            data=data,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
            message=SimpleNamespace(photo=None, caption=None, text="x"),
        )
    return SimpleNamespace(
        message=message,
        callback_query=query,
        effective_user=SimpleNamespace(id=TG),
    )


@pytest.fixture
def context():
    return SimpleNamespace(user_data={})


def _run(handler, update, context):
    return asyncio.run(handler(update, context))


def test_never_screen_then_text_is_saved_and_the_step_keeps_waiting(Session, context):
    context.user_data.update(food_kind=ns.NO_SUGGEST, food_asked_at=datetime.now(UTC))
    update = _update(text="грибы")
    state = _run(tg.process_food_name, update, context)
    assert state == tg.ENTERING_FOOD_NAME
    assert _setting(Session, ns.NO_SUGGEST) == ["грибы"]
    assert "food_asked_at" in context.user_data
    update.message.reply_text.assert_awaited_once()


def test_reliable_text_closes_the_step(Session, context):
    context.user_data.update(food_kind=ns.RELIABLE, food_asked_at=datetime.now(UTC))
    state = _run(tg.process_food_name, _update(text="Лаваш"), context)
    assert state == tg.CHOOSING_ACTION
    assert _setting(Session, ns.RELIABLE) == ["Лаваш"]
    assert "food_asked_at" not in context.user_data
    assert "food_kind" not in context.user_data


def test_text_after_the_wait_is_never_silently_a_food(Session, context):
    context.user_data.update(
        food_kind=ns.NO_SUGGEST,
        food_asked_at=datetime.now(UTC) - tg.FOOD_NAME_WAIT - timedelta(seconds=1),
    )
    _run(tg.process_food_name, _update(text="грибы"), context)
    assert not _setting(Session, ns.NO_SUGGEST)


def test_a_bare_number_after_the_wait_is_still_a_weigh_in(
    Session, context, monkeypatch
):
    weighed = AsyncMock(return_value=tg.CHOOSING_ACTION)
    monkeypatch.setattr(tg, "process_weight", weighed)
    _run(tg.process_food_name, _update(text="72,5"), context)
    weighed.assert_awaited_once()


def test_any_other_tap_closes_the_wait(context):
    context.user_data.update(food_asked_at=datetime.now(UTC))
    asyncio.run(tg.food_left_by_tap(_update(data="st:x"), context))
    assert "food_asked_at" not in context.user_data


def test_the_tap_outside_handlers_are_registered(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "1:fake", raising=False)
    application = tg.create_bot_application()
    every = [h for group in application.handlers.values() for h in group]
    tap = _tap_update(fl.RELIABLE_OPEN)
    assert any(
        _is(h, tg.food_lists_callback) and h.check_update(tap)
        for h in every
        if hasattr(h, "pattern")
    )
    other = _tap_update("set:open")
    assert any(
        _is(h, tg.food_left_by_tap) and h.check_update(other)
        for h in every
        if hasattr(h, "pattern")
    )
    assert not any(
        _is(h, tg.food_left_by_tap) and h.check_update(tap)
        for h in every
        if hasattr(h, "pattern")
    )
