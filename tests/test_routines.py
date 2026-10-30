"""💊 Рутины (stage 5, slice C, G5-R08): the user's own things, ticked on the day card.

Screens and ticks over a real in-memory database; the typed-name step through the
real ConversationHandler, because that routing is what a screen test cannot see.
"""

import asyncio
import html
import inspect
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from telegram import Update

from app.core.config import settings
from app.db.base import Base
from app.models.routine_tick import RoutineTick
from app.models.user import User
from app.services import routines, settings_screen, stats_screen
from app.services import telegram as tg

WEDNESDAY = datetime(2026, 10, 28, 8, 0, tzinfo=UTC)
TODAY = date(2026, 10, 28)
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


def _tap(Session, data: str):
    with Session() as db:
        return routines.handle_callback(db, TG, data)


def _name(Session, text: str, rename=None):
    with Session() as db:
        return routines.submit_name(db, TG, text, rename)


def _add(Session, name: str, how="Каждый день", days=(), icon="💊") -> dict:
    """The whole path through the screens, as the user taps it."""
    screen, done = _name(Session, name)
    assert done
    screen = _tap(Session, _data(screen, how))
    for day in days:
        screen = _tap(Session, _data(screen, day))
    if how == "По дням":
        screen = _tap(Session, _data(screen, "✅ Сохранить"))
    screen = _tap(Session, _data(screen, icon))
    assert f"✓ Добавлено: {html.escape(name)}" in screen.text
    return _active(Session)[-1]


def _active(Session) -> list[dict]:
    with Session() as db:
        return routines.active(db.query(User).first())


def _card(Session, now=WEDNESDAY, data=None):
    with Session() as db:
        if data:
            return stats_screen.handle_callback(db, TG, data, now)
        return stats_screen.open_view(db, TG, stats_screen.TODAY, now)


def _ticks(Session) -> list[tuple]:
    with Session() as db:
        rows = db.scalars(select(RoutineTick)).all()
        return [(row.routine_id, row.day) for row in rows]


# --- the list and adding -------------------------------------------------------


def test_the_list_starts_empty_with_no_presets(Session):
    screen = _tap(Session, "rt:open")
    assert screen.text.startswith("<b>💊 Рутины</b>")
    assert _labels(screen) == ["➕ Добавить рутину", "⬅️ Настройки"]
    assert _active(Session) == []


def test_the_name_prompt_suggests_no_supplement():
    text = routines.name_screen().text
    assert "Прогулка" in text
    assert "итамин" not in text and "агни" not in text


def test_adding_by_days_shows_the_days_on_the_list(Session):
    routine = _add(Session, "Растяжка", "По дням", ("Пн", "Ср", "Пт"), "🧘")
    assert routine["days"] == [0, 2, 4] and routine["icon"] == "🧘"
    screen = _tap(Session, "rt:open")
    assert "🧘 Растяжка — пн, ср, пт" in screen.text
    assert "Неотмеченное нигде не считается" in screen.text
    assert _labels(screen) == ["🧘 Растяжка", "➕ Добавить рутину", "⬅️ Настройки"]


def test_a_new_routine_is_invisible_until_its_last_step(Session):
    _name(Session, "Прогулка")
    assert _active(Session) == []
    assert "Прогулка" not in _tap(Session, "rt:open").text
    assert stats_screen.TICK not in _callback_verbs(_card(Session))


def _callback_verbs(screen) -> list[str]:
    return [
        b.callback_data.split(":")[1]
        for row in screen.markup.inline_keyboard
        for b in row
    ]


def test_a_new_routine_left_halfway_is_replaced_not_piled_up(Session):
    _name(Session, "Прогулка")
    _name(Session, "Йога")
    with Session() as db:
        names = [r["name"] for r in routines.stored(db.query(User).first())]
    assert names == ["Йога"]


def test_days_cannot_be_saved_empty(Session):
    screen, _done = _name(Session, "Прогулка")
    screen = _tap(Session, _data(screen, "По дням"))
    after = _tap(Session, _data(screen, "✅ Сохранить"))
    assert after.toast == "Отметь хотя бы один день"
    assert "Отметь дни" in after.text


@pytest.mark.parametrize("text", ["", "   ", "я" * (routines.NAME_MAX + 1)])
def test_a_name_that_does_not_fit_asks_again_and_saves_nothing(Session, text):
    screen, done = _name(Session, text)
    assert not done and str(routines.NAME_MAX) in screen.text
    with Session() as db:
        assert routines.stored(db.query(User).first()) == []


def test_a_name_is_taken_regardless_of_case_and_spaces(Session):
    _add(Session, "Прогулка")
    screen, done = _name(Session, "  прогулка ")
    assert not done and "уже есть" in screen.text


def test_a_name_with_markup_is_escaped_everywhere(Session):
    routine = _add(Session, "<b>чай</b> & сон")
    for screen in (
        _tap(Session, "rt:open"),
        _tap(Session, f"rt:r:{routine['id']}"),
        _tap(Session, f"rt:w:{routine['id']}"),
        _tap(Session, f"rt:ic:{routine['id']}"),
    ):
        assert "&lt;b&gt;чай&lt;/b&gt; &amp; сон" in screen.text
        assert "<b>чай" not in screen.text


def test_the_limit_hides_the_add_button_and_refuses_a_stale_one(Session):
    for index in range(routines.MAX_ROUTINES):
        _add(Session, f"Р{index}")
    assert "➕ Добавить рутину" not in _labels(_tap(Session, "rt:open"))
    with Session() as db:
        screen, _rid, wait = routines.ask_name(db, TG, "rt:add")
    assert not wait and "Рутин уже" in screen.toast


# --- one routine: rename, schedule, remove ---------------------------------------


def test_one_routine_screen_and_its_back_button(Session):
    routine = _add(Session, "Прогулка")
    screen = _tap(Session, f"rt:r:{routine['id']}")
    assert screen.text.startswith("<b>💊 Прогулка</b>")
    assert "Дни: каждый день" in screen.text
    assert _labels(screen) == [
        "🗓 Дни",
        "✏️ Название",
        "🔣 Значок",
        "🗑 Убрать",
        "⬅️ Рутины",
    ]
    assert _data(screen, "⬅️ Рутины") == "rt:open"


def test_rename_keeps_the_id_and_the_ticks(Session):
    routine = _add(Session, "Прогулка")
    screen, done = _name(Session, "Долгая прогулка", rename=routine["id"])
    assert done and "✓ Название: Долгая прогулка" in screen.text
    assert _active(Session)[0]["id"] == routine["id"]


def test_rename_to_the_same_name_in_new_case_is_allowed(Session):
    routine = _add(Session, "прогулка")
    _screen, done = _name(Session, "Прогулка", rename=routine["id"])
    assert done


def test_changing_days_returns_to_the_list_with_a_notice(Session):
    routine = _add(Session, "Прогулка")
    screen = _tap(Session, f"rt:sw:{routine['id']}")
    assert "<i>✓ Прогулка — раз в неделю</i>" in screen.text


def test_remove_applies_at_once_and_offers_undo_only_once(Session):
    routine = _add(Session, "Прогулка")
    screen = _tap(Session, f"rt:rm:{routine['id']}")
    assert "<i>Убрано: Прогулка</i>" in screen.text
    assert "↩️ Вернуть «Прогулка»" in _labels(screen)
    assert _active(Session) == []
    assert "↩️ Вернуть «Прогулка»" not in _labels(_tap(Session, "rt:open"))

    screen = _tap(Session, _data(screen, "↩️ Вернуть"))
    assert "<i>✓ Вернулось: Прогулка</i>" in screen.text
    assert [r["id"] for r in _active(Session)] == [routine["id"]]


def test_a_double_tap_on_remove_keeps_the_undo(Session):
    routine = _add(Session, "Прогулка")
    _tap(Session, f"rt:rm:{routine['id']}")
    screen = _tap(Session, f"rt:rm:{routine['id']}")
    assert "↩️ Вернуть «Прогулка»" in _labels(screen)


def test_an_old_undo_refuses_a_name_taken_since(Session):
    routine = _add(Session, "Прогулка")
    _tap(Session, f"rt:rm:{routine['id']}")
    _add(Session, "Прогулка")
    screen = _tap(Session, f"rt:un:{routine['id']}")
    assert screen.toast == "Такая рутина уже есть"
    assert len(_active(Session)) == 1


def test_an_old_undo_respects_the_limit(Session):
    routine = _add(Session, "Прогулка")
    _tap(Session, f"rt:rm:{routine['id']}")
    for index in range(routines.MAX_ROUTINES):
        _add(Session, f"Р{index}")
    assert "Рутин уже" in _tap(Session, f"rt:un:{routine['id']}").toast
    assert len(_active(Session)) == routines.MAX_ROUTINES


@pytest.mark.parametrize("verb", ["r", "w", "sd", "sw", "dd", "ds", "ic"])
def test_a_button_of_a_removed_routine_says_so_and_changes_nothing(Session, verb):
    routine = _add(Session, "Прогулка")
    _tap(Session, f"rt:rm:{routine['id']}")
    screen = _tap(Session, f"rt:{verb}:{routine['id']}")
    assert screen.toast == "Этой рутины уже нет"
    assert screen.text.startswith("<b>💊 Рутины</b>")
    assert _active(Session) == []


@pytest.mark.parametrize("data", ["rt:nope", "rt:r", "rt:", "rt:ip:zz:99", "rt:dt:x:9"])
def test_unknown_data_gives_the_list_never_a_crash(Session, data):
    assert _tap(Session, data).text.startswith("<b>💊 Рутины</b>")


@pytest.mark.parametrize("arg", ["99", "x", "-1"])
def test_a_bad_icon_or_day_argument_on_a_real_routine_is_ignored(Session, arg):
    routine = _add(Session, "Прогулка")
    for verb in ("ip", "dt"):
        screen = _tap(Session, f"rt:{verb}:{routine['id']}:{arg}")
        assert screen.text.startswith("<b>💊 Рутины</b>")
    assert _active(Session)[0]["icon"] == "💊"


@pytest.mark.parametrize("bad", [None, "x", 5, {"a": 1}, [1, "x", {"no": "id"}]])
def test_a_damaged_settings_value_reads_as_empty(Session, bad):
    with Session() as db:
        db.query(User).first().settings = {routines.ROUTINES: bad}
        db.commit()
    assert _tap(Session, "rt:open").text.startswith("<b>💊 Рутины</b>")
    assert _active(Session) == []


def test_every_screen_keeps_the_keyboard_within_the_rules(Session):
    routine = _add(Session, "Прогулка")
    rid = routine["id"]
    for data in ("open", f"r:{rid}", f"w:{rid}", f"dd:{rid}", f"ic:{rid}"):
        screen = _tap(Session, f"rt:{data}")
        assert all(len(row) <= 3 for row in screen.markup.inline_keyboard)
        assert _labels(screen)[-1].startswith("⬅️")
        assert all(
            len(b.callback_data.encode()) <= 64
            for row in screen.markup.inline_keyboard
            for b in row
        )


def test_a_long_name_gets_a_wider_button_and_a_tick_does_not_move_it(Session):
    for name in ("Стакан воды", "Прогулка"):
        _add(Session, name)
    card = _card(Session)
    assert [len(row) for row in card.markup.inline_keyboard][0] == 2
    card = _card(Session, data=_data(card, "Прогулка"))
    labels = [b.text for b in card.markup.inline_keyboard[0]]
    assert labels == ["💊 Стакан воды", "✓ 💊 Прогулка"]


def test_a_very_long_name_gets_a_row_of_its_own(Session):
    _add(Session, "Стакан тёплой воды утром")
    _add(Session, "Йога")
    assert [len(row) for row in _card(Session).markup.inline_keyboard[:2]] == [1, 1]


# --- the hub ------------------------------------------------------------------


def test_the_hub_counts_routines_and_opens_them(Session):
    _add(Session, "Прогулка")
    other = _add(Session, "Йога")
    _tap(Session, f"rt:rm:{other['id']}")
    with Session() as db:
        screen = settings_screen.open_view(db, TG)
    assert "💊 Рутины: 1 — отмечаются на карточке дня." in screen.text
    assert _data(screen, "💊 Рутины") == "rt:open"


# --- the day card ---------------------------------------------------------------


def test_the_card_offers_a_tick_and_names_nothing_unticked(Session):
    _add(Session, "Прогулка")
    card = _card(Session)
    assert "💊 Прогулка" in _labels(card)
    assert "Прогулка" not in card.text


def test_a_tick_shows_on_the_card_and_a_second_tap_takes_it_back(Session):
    routine = _add(Session, "Прогулка")
    card = _card(Session, data=_data(_card(Session), "Прогулка"))
    assert card.toast == "✓ Прогулка"
    assert "💊 Прогулка ✓" in card.text
    assert "✓ 💊 Прогулка" in _labels(card)
    assert _ticks(Session) == [(routine["id"], TODAY)]

    card = _card(Session, data=_data(card, "Прогулка"))
    assert card.toast == "Отметка снята"
    assert "Прогулка" not in card.text
    assert _ticks(Session) == []


def test_an_old_unticked_button_cannot_untick(Session):
    routine = _add(Session, "Прогулка")
    old = _data(_card(Session), "Прогулка")
    assert _typed(Session, "Прогулка") == "Прогулка"
    card = _card(Session, data=old)
    assert card.toast == "✓ Прогулка"
    assert [tick[0] for tick in _ticks(Session)] == [routine["id"]]


def test_a_double_tap_stays_ticked(Session):
    routine = _add(Session, "Прогулка")
    button = _data(_card(Session), "Прогулка")
    _card(Session, data=button)
    _card(Session, data=button)
    assert [tick[0] for tick in _ticks(Session)] == [routine["id"]]


def test_a_double_tap_on_the_tick_off_stays_off(Session):
    _add(Session, "Прогулка")
    ticked = _card(Session, data=_data(_card(Session), "Прогулка"))
    button = _data(ticked, "Прогулка")
    _card(Session, data=button)
    assert _card(Session, data=button).toast == "Отметка снята"
    assert _ticks(Session) == []


def test_a_tick_stays_takeable_after_the_schedule_changes(Session):
    routine = _add(Session, "Прогулка")
    tuesday = datetime(2026, 10, 27, 8, 0, tzinfo=UTC)
    _card(Session, tuesday, _data(_card(Session, tuesday), "Прогулка"))
    _card(Session, data=_data(_card(Session), "Прогулка"))
    _tap(Session, f"rt:sw:{routine['id']}")  # now weekly, ticked on two days
    assert "✓ 💊 Прогулка" in _labels(_card(Session))


def test_a_button_of_a_routine_not_due_today_tells_the_truth(Session):
    routine = _add(Session, "Прогулка")
    button = _data(_card(Session), "Прогулка")
    _tap(Session, _data(_tap(Session, f"rt:dd:{routine['id']}"), "Пн"))
    _tap(Session, f"rt:ds:{routine['id']}")
    assert _card(Session, data=button).toast == "Этой рутины сегодня нет на карточке"
    assert _ticks(Session) == []


def test_a_routine_shows_only_on_its_days(Session):
    _add(Session, "Прогулка", "По дням", ("Пн",))
    assert "💊 Прогулка" not in _labels(_card(Session))  # a Wednesday
    monday = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)
    assert "💊 Прогулка" in _labels(_card(Session, monday))


def test_a_weekly_routine_waits_all_week_then_leaves_after_its_tick(Session):
    _add(Session, "Баня", "Раз в неделю")
    monday = datetime(2026, 10, 26, 8, 0, tzinfo=UTC)
    tuesday = datetime(2026, 10, 27, 8, 0, tzinfo=UTC)
    assert "💊 Баня" in _labels(_card(Session, monday))
    card = _card(Session, tuesday)
    card = _card(Session, tuesday, _data(card, "Баня"))
    assert "✓ 💊 Баня" in _labels(card)  # the day of the tick: can be taken back
    assert "Баня" not in " ".join(_labels(_card(Session, WEDNESDAY)))
    next_monday = datetime(2026, 11, 2, 8, 0, tzinfo=UTC)
    assert "💊 Баня" in _labels(_card(Session, next_monday))


def test_yesterday_names_only_the_ticked_and_has_no_tick_buttons(Session):
    _add(Session, "Прогулка")
    _add(Session, "Йога")
    _card(Session, data=_data(_card(Session), "Прогулка"))
    thursday = datetime(2026, 10, 29, 8, 0, tzinfo=UTC)
    with Session() as db:
        yesterday = stats_screen.open_view(db, TG, stats_screen.YESTERDAY, thursday)
    assert "💊 Прогулка ✓" in yesterday.text
    assert "Йога" not in yesterday.text
    assert not [
        data
        for data in (
            b.callback_data for r in yesterday.markup.inline_keyboard for b in r
        )
        if data.startswith("st:k:")
    ]


def test_a_card_left_open_overnight_ticks_nothing_and_shows_today(Session):
    _add(Session, "Прогулка")
    stale = _data(_card(Session), "Прогулка")
    thursday = datetime(2026, 10, 29, 8, 0, tzinfo=UTC)
    card = _card(Session, thursday, stale)
    assert card.toast == "Это карточка прошлого дня, вот сегодняшняя"
    assert _ticks(Session) == []
    assert card.text.startswith("<b>📊 Сегодня")


def test_the_day_turns_by_the_users_zone_not_utc(Session):
    routine = _add(Session, "Прогулка")
    # 21:30 UTC on the 27th is 01:30 on the 28th in Tbilisi.
    late = datetime(2026, 10, 27, 21, 30, tzinfo=UTC)
    _card(Session, late, _data(_card(Session, late), "Прогулка"))
    assert _ticks(Session) == [(routine["id"], TODAY)]


def test_a_removed_routine_keeps_its_tick_on_the_card_and_loses_its_button(Session):
    routine = _add(Session, "Прогулка")
    button = _data(_card(Session), "Прогулка")
    _card(Session, data=button)
    _tap(Session, f"rt:rm:{routine['id']}")
    card = _card(Session)
    assert "💊 Прогулка ✓" in card.text
    assert "Прогулка" not in " ".join(_labels(card))


def test_a_stale_tick_on_a_removed_routine_says_so(Session):
    routine = _add(Session, "Прогулка")
    button = _data(_card(Session), "Прогулка")
    _tap(Session, f"rt:rm:{routine['id']}")
    card = _card(Session, data=button)
    assert card.toast == "Этой рутины уже нет"
    assert _ticks(Session) == []


def test_only_three_ticks_fit_then_more_opens_the_rest(Session):
    names = ["Йога", "Сон", "Вода", "Чай", "Душ"]
    for name in names:
        _add(Session, name)
    card = _card(Session)
    first = [b.text for b in card.markup.inline_keyboard[0]]
    assert first == ["💊 Йога", "💊 Сон", "💊 Вода"]
    assert "💊 Ещё 2" in _labels(card)

    more = _card(Session, data=_data(card, "Ещё 2"))
    assert more.text.startswith("<b>💊 Рутины на сегодня</b>")
    assert _labels(more)[:5] == [f"💊 {name}" for name in names]
    assert _data(more, "⬅️ Сегодня") == "st:t"

    more = _card(Session, data=_data(more, "Душ"))
    assert more.toast == "✓ Душ"
    assert "✓ 💊 Душ" in _labels(more)  # stays on «Ещё», the next tick is one tap
    assert "💊 Душ ✓" in _card(Session).text


def test_the_open_numbers_stay_open_after_a_tick(Session):
    _add(Session, "Прогулка")
    opened = _card(Session, data="st:tn")
    after = _card(Session, data=_data(opened, "Прогулка"))
    assert _labels(after) == _labels(_card(Session, data="st:tn"))


@pytest.mark.parametrize("data", ["st:k:", "st:k:x", "st:k::", "st:m:", "st:K:a:b:c"])
def test_malformed_tick_data_is_today_never_a_crash(Session, data):
    _add(Session, "Прогулка")
    assert _card(Session, data=data).text.startswith("<b>📊 Сегодня")
    assert _ticks(Session) == []


def test_a_tick_on_someone_elses_routine_id_does_nothing(Session):
    with Session() as db:
        db.add(User(telegram_id=7, username="other"))
        db.commit()
    routine = _add(Session, "Прогулка")
    data = _data(_card(Session), "Прогулка")
    with Session() as db:
        screen = stats_screen.handle_callback(db, 7, data, WEDNESDAY)
    assert screen.toast == "Этой рутины уже нет"
    assert routine["id"] not in [tick[0] for tick in _ticks(Session)]


# --- a typed tick ---------------------------------------------------------------


def _typed(Session, text, now=WEDNESDAY):
    with Session() as db:
        return stats_screen.tick_by_name(db, TG, text, now)


def test_a_typed_exact_name_ticks_it_and_repeating_keeps_one_tick(Session):
    routine = _add(Session, "Прогулка")
    assert _typed(Session, "  прогулка ") == "Прогулка"
    assert _typed(Session, "Прогулка") == "Прогулка"
    assert _ticks(Session) == [(routine["id"], TODAY)]


@pytest.mark.parametrize("text", ["прогулка утром", "гречка", "", None])
def test_any_other_text_is_not_a_tick(Session, text):
    _add(Session, "Прогулка")
    assert _typed(Session, text) is None
    assert _ticks(Session) == []


def test_a_typed_name_off_its_day_is_not_a_tick(Session):
    _add(Session, "Прогулка", "По дням", ("Пн",))
    assert _typed(Session, "Прогулка") is None


# --- the real conversation routing ------------------------------------------------


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


def _callback_of(handler):
    return inspect.unwrap(handler.callback) if handler else None


def _is(handler, function) -> bool:
    return _callback_of(handler) is inspect.unwrap(function)


def test_add_starts_the_name_step_from_every_kind_of_state(conv):
    tap = _tap_update("rt:add")
    assert _is(_first_match(conv.entry_points, tap), tg.routine_ask_name)
    assert _is(
        _first_match(conv.states[tg.CHOOSING_ACTION] + conv.fallbacks, tap),
        tg.routine_ask_name,
    )
    assert _is(
        _first_match(conv.states[tg.ENTERING_WEIGHT] + conv.fallbacks, tap),
        tg.routine_ask_name,
    )


@pytest.mark.parametrize(
    "state", ["ADDING_MEAL_TIME", "ADDING_MEAL", "CONFIRMING_MEAL"]
)
def test_add_cannot_pull_the_user_out_of_a_meal(conv, Session, state):
    handlers = conv.states[getattr(tg, state)] + conv.fallbacks
    assert _first_match(handlers, _tap_update("rt:add")) is None
    assert _first_match(handlers, _tap_update("rt:ren:abc")) is None
    screen = _tap(Session, "rt:add")
    assert screen.text.startswith("<b>💊 Рутины</b>")
    assert "/cancel" in screen.toast


def test_any_other_tap_closes_the_name_step_through_its_own_group():
    application = tg.create_bot_application()
    handler = application.handlers[1][0]
    assert inspect.unwrap(handler.callback) is tg.routine_left_by_tap
    assert handler.check_update(_tap_update("st:t"))
    assert handler.check_update(_tap_update("wt:u:1"))
    assert not handler.check_update(_tap_update("rt:open"))


def test_a_rename_tap_starts_the_name_step_too(conv):
    assert _is(
        _first_match(conv.entry_points, _tap_update("rt:ren:abc123")),
        tg.routine_ask_name,
    )


def test_other_routine_taps_stay_outside_the_conversation(conv):
    tap = _tap_update("rt:open")
    assert _first_match(conv.entry_points, tap) is None
    assert _first_match(conv.states[tg.CHOOSING_ACTION] + conv.fallbacks, tap) is None


def test_in_the_name_step_text_is_the_name_and_menu_buttons_still_work(conv):
    handlers = conv.states[tg.ENTERING_ROUTINE_NAME]
    assert _is(
        _first_match(handlers, _text_update("Прогулка")), tg.process_routine_name
    )
    assert _is(
        _first_match(handlers, _text_update("📊 Статистика")), tg.show_statistics
    )
    assert _is(_first_match(handlers, _tap_update("rt:open")), tg.routine_leave_name)


def test_outside_a_step_plain_text_goes_to_the_typed_tick(conv):
    handlers = conv.states[tg.CHOOSING_ACTION]
    assert _is(_first_match(handlers, _text_update("Прогулка")), tg.typed_routine_tick)
    assert _is(
        _first_match(conv.entry_points, _text_update("Йога")), tg.typed_routine_tick
    )


def test_the_typed_tick_leaves_a_weight_and_the_menu_to_their_handlers(conv):
    handlers = conv.states[tg.CHOOSING_ACTION]
    assert _is(_first_match(handlers, _text_update("72,5")), tg.process_weight)
    assert _is(_first_match(handlers, _text_update("⚙️ Настройки")), tg.show_settings)


@pytest.mark.parametrize("text", ["❓ О боте", "💎 Получить подписку"])
def test_the_typed_tick_does_not_swallow_the_two_fallback_buttons(conv, text):
    update = _text_update(text)
    assert _first_match(conv.states[tg.CHOOSING_ACTION], update) is None
    assert _first_match(conv.fallbacks, update) is not None


def test_food_being_entered_never_reaches_the_typed_tick(conv):
    handler = _first_match(conv.states[tg.ADDING_MEAL], _text_update("Прогулка"))
    assert not _is(handler, tg.typed_routine_tick)


# --- the handlers end to end -----------------------------------------------------


def _msg_update(text):
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    return message, SimpleNamespace(
        message=message,
        effective_message=message,
        effective_user=SimpleNamespace(id=TG, username="u"),
        effective_chat=SimpleNamespace(id=TG),
    )


def _query_update(data):
    query = SimpleNamespace(
        data=data, answer=AsyncMock(), edit_message_text=AsyncMock()
    )
    return query, SimpleNamespace(
        callback_query=query, effective_user=SimpleNamespace(id=TG)
    )


def test_tap_add_type_a_name_then_the_state_returns(Session):
    context = SimpleNamespace(user_data={})
    query, update = _query_update("rt:add")
    state = asyncio.run(tg.routine_ask_name(update, context))
    assert state == tg.ENTERING_ROUTINE_NAME
    assert "Новая рутина" in query.edit_message_text.call_args.args[0]

    message, update = _msg_update("я" * (routines.NAME_MAX + 1))
    assert (
        asyncio.run(tg.process_routine_name(update, context))
        == tg.ENTERING_ROUTINE_NAME
    )
    message, update = _msg_update("Прогулка")
    assert asyncio.run(tg.process_routine_name(update, context)) == tg.CHOOSING_ACTION
    assert "В какие дни" in message.reply_text.call_args.args[0]


def test_tap_rename_carries_the_id_to_the_typed_name(Session):
    routine = _add(Session, "Прогулка")
    context = SimpleNamespace(user_data={})
    _query, update = _query_update(f"rt:ren:{routine['id']}")
    assert asyncio.run(tg.routine_ask_name(update, context)) == tg.ENTERING_ROUTINE_NAME
    _message, update = _msg_update("Йога")
    asyncio.run(tg.process_routine_name(update, context))
    assert [r["name"] for r in _active(Session)] == ["Йога"]
    assert "routine_rename" not in context.user_data


def test_back_from_the_name_step_closes_it(Session):
    context = SimpleNamespace(user_data={"routine_rename": "x"})
    query, update = _query_update("rt:open")
    assert asyncio.run(tg.routine_leave_name(update, context)) == tg.CHOOSING_ACTION
    assert context.user_data == {}
    assert "<b>💊 Рутины</b>" in query.edit_message_text.call_args.args[0]


def _after_moving_on(Session, text):
    """➕ or ✏️ tapped, then another screen's button: the step is closed."""
    routine = _add(Session, "Прогулка")
    context = SimpleNamespace(user_data={})
    _query, update = _query_update(f"rt:ren:{routine['id']}")
    asyncio.run(tg.routine_ask_name(update, context))
    asyncio.run(tg.routine_left_by_tap(_query_update("st:t")[1], context))
    message, update = _msg_update(text)
    return asyncio.run(tg.process_routine_name(update, context)), message, routine


def test_text_after_moving_on_is_no_rename_and_stays_silent(Session):
    state, message, _routine = _after_moving_on(Session, "гречка")
    assert state == tg.CHOOSING_ACTION
    assert [r["name"] for r in _active(Session)] == ["Прогулка"]
    message.reply_text.assert_not_called()


def test_a_weight_after_moving_on_is_a_weight(Session, monkeypatch):
    recorded = AsyncMock(return_value=tg.ENTERING_WEIGHT)
    monkeypatch.setattr(tg, "process_weight", recorded)
    state, _message, _routine = _after_moving_on(Session, "72,5")
    assert state == tg.ENTERING_WEIGHT
    recorded.assert_awaited_once()
    assert [r["name"] for r in _active(Session)] == ["Прогулка"]


def test_a_routine_name_after_moving_on_ticks_it(Session):
    state, message, routine = _after_moving_on(Session, "Прогулка")
    assert state == tg.CHOOSING_ACTION
    assert [tick[0] for tick in _ticks(Session)] == [routine["id"]]
    assert message.reply_text.call_args.args[0] == "✓ Прогулка"


def test_the_name_step_expires_after_its_wait(Session):
    routine = _add(Session, "Прогулка")
    context = SimpleNamespace(user_data={})
    _query, update = _query_update(f"rt:ren:{routine['id']}")
    asyncio.run(tg.routine_ask_name(update, context))
    context.user_data["routine_asked_at"] -= tg.ROUTINE_NAME_WAIT + timedelta(seconds=1)
    _message, update = _msg_update("гречка")
    assert asyncio.run(tg.process_routine_name(update, context)) == tg.CHOOSING_ACTION
    assert [r["name"] for r in _active(Session)] == ["Прогулка"]


def test_a_name_that_does_not_fit_keeps_the_step_open(Session):
    context = SimpleNamespace(user_data={})
    asyncio.run(tg.routine_ask_name(_query_update("rt:add")[1], context))
    update = _msg_update("я" * (routines.NAME_MAX + 1))[1]
    asyncio.run(tg.process_routine_name(update, context))
    update = _msg_update("Прогулка")[1]
    assert asyncio.run(tg.process_routine_name(update, context)) == tg.CHOOSING_ACTION
    with Session() as db:
        names = [r["name"] for r in routines.stored(db.query(User).first())]
    assert names == ["Прогулка"]


@pytest.mark.parametrize("name", ["72,5", "100", "50 кг"])
def test_a_name_that_reads_as_a_weight_is_refused(Session, name):
    screen, done = _name(Session, name)
    assert not done and "похоже на вес" in screen.text


def test_a_refused_tap_changes_no_state(Session):
    context = SimpleNamespace(user_data={})
    update = _query_update("rt:ren:gone00")[1]
    assert asyncio.run(tg.routine_ask_name(update, context)) is None
    assert context.user_data == {}


def test_a_locked_user_gets_no_name_step(Session, monkeypatch):
    monkeypatch.setattr(tg, "check_subscription", AsyncMock(return_value=False))
    query, update = _query_update("rt:add")
    context = SimpleNamespace(user_data={})
    assert asyncio.run(tg.routine_ask_name(update, context)) is None
    query.answer.assert_awaited_once()
    query.edit_message_text.assert_not_called()


def test_a_typed_tick_answers_and_other_text_stays_silent(Session):
    routine = _add(Session, "Прогулка")
    context = SimpleNamespace(user_data={})
    message, update = _msg_update("гречка")
    assert asyncio.run(tg.typed_routine_tick(update, context)) is None
    message.reply_text.assert_not_called()

    message, update = _msg_update("Прогулка")
    assert asyncio.run(tg.typed_routine_tick(update, context)) == tg.CHOOSING_ACTION
    assert message.reply_text.call_args.args[0] == "✓ Прогулка"
    assert [tick[0] for tick in _ticks(Session)] == [routine["id"]]
