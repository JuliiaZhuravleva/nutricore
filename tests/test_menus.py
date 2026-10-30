"""The main menu's 📊 Статистика, ⚖️ Мой вес and ⚙️ Настройки.

The screens (``app.services.stats_screen``, ``app.services.weight_screen``) run
on an in-memory SQLite DB with a fixed "now"; the bot side runs through the
real handlers with fake Telegram objects, and the registration is read off the
built application.
"""

import asyncio
import json
import time
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.base import Base
from app.models.body_metric import BodyMetric
from app.models.meal import Meal
from app.models.user import User
from app.services import settings_screen
from app.services import stats_screen as st
from app.services import telegram as tg
from app.services import weight_screen as wt
from app.services.card_delivery import CardDelivery
from app.services.numbers import SHOW_NUMBERS
from app.services.picture_renderer import PictureRenderer

# Wed 28 Oct 2026, 12:00 in Tbilisi (UTC+4).
NOW = datetime(2026, 10, 28, 8, 0, tzinfo=UTC)


@pytest.fixture
def Session(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(tg, "SessionLocal", factory)
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "42")
    monkeypatch.setattr(settings, "DEFAULT_TIMEZONE", "Asia/Tbilisi")
    yield factory
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def _user(db, telegram_id=42, numbers=False) -> User:
    user = User(telegram_id=telegram_id, username=f"u{telegram_id}")
    if numbers:  # ⚙️ «🔢 Цифры» on: calories shown without a tap
        user.settings = {SHOW_NUMBERS: True}
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _meal(db, user, when, calories=400, description="гречка", **kw) -> Meal:
    meal = Meal(
        user_id=user.id,
        description=description,
        meal_time=when,
        calories=calories,
        proteins=kw.get("proteins", 10),
        fats=kw.get("fats", 5),
        carbohydrates=kw.get("carbohydrates", 50),
        meal_type=kw.get("meal_type"),
        nutrients={},
    )
    db.add(meal)
    db.commit()
    return meal


def _weigh(db, user, kg, when, source=wt.SOURCE) -> BodyMetric:
    metric = BodyMetric(
        user_id=user.id,
        timestamp=when,
        weight=kg,
        metrics={"source": source} if source else None,
    )
    db.add(metric)
    db.commit()
    db.refresh(metric)
    return metric


def _callbacks(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def _labels(markup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]


# --- statistics: days ---------------------------------------------------------


def test_day_is_the_local_calendar_day_not_utc(Session):
    with Session() as db:
        user = _user(db, numbers=True)
        # 01:30 on the 28th in Tbilisi, still the 27th in UTC: today.
        _meal(db, user, datetime(2026, 10, 27, 21, 30, tzinfo=UTC), 300, "сырники")
        # 23:30 on the 27th in Tbilisi: yesterday.
        _meal(db, user, datetime(2026, 10, 27, 19, 30, tzinfo=UTC), 700, "плов")
        today = st.open_view(db, 42, st.TODAY, NOW)
        yesterday = st.open_view(db, 42, st.YESTERDAY, NOW)

    assert today.text.startswith("<b>📊 Сегодня, ср 28 октября</b>")
    assert "1:30 сырники — ≈ 300 ккал" in today.text and "плов" not in today.text
    assert "🔢 За день: ≈ 300 ккал" in today.text
    assert yesterday.text.startswith("<b>📊 Вчера, вт 27 октября</b>")
    assert "23:30 плов — ≈ 700 ккал" in yesterday.text
    assert "сырники" not in yesterday.text


def test_today_lists_meals_in_order_with_totals(Session):
    with Session() as db:
        user = _user(db, numbers=True)
        _meal(db, user, NOW - timedelta(hours=1), 250.4, "<b>омлет</b>",
              proteins=20, fats=15.4, carbohydrates=2, meal_type="Завтрак")  # fmt: skip
        _meal(db, user, NOW - timedelta(hours=3), 1000, "кофе",
              proteins=1, fats=1, carbohydrates=3)  # fmt: skip
        screen = st.open_view(db, 42, st.TODAY, NOW)

    lines = screen.text.split("\n")
    assert "🕐 9:00 · 11:00" in lines
    assert (
        "<blockquote expandable>🔢 За день: ≈ 1 250 ккал · Б 21 г · Ж 16 г · У 5 г"
        in lines
    )
    first = lines.index("9:00 кофе — ≈ 1 000 ккал")
    assert lines[first + 1] == (
        "11:00 &lt;b&gt;омлет&lt;/b&gt; — ≈ 250 ккал</blockquote>"
    )
    assert _labels(screen.markup)[-3:] == ["✓ Сегодня", "Вчера", "7 дней"]
    assert _callbacks(screen.markup)[-3:] == ["st:t", "st:y", "st:w"]


def test_empty_day_says_where_records_come_from(Session):
    with Session() as db:
        _user(db)
        screen = st.open_view(db, 42, st.TODAY, NOW)
    assert "Похоже, не всё записано?" in screen.text and "ккал" not in screen.text
    assert _rows(screen.markup) == [
        ["🍽 Добавить приём пищи"],
        ["✓ Сегодня", "Вчера", "7 дней"],
    ]


def test_unknown_user_gets_an_empty_day_not_an_error(Session):
    with Session() as db:
        screen = st.open_view(db, 999, st.TODAY, NOW)
    assert "Похоже, не всё записано?" in screen.text


def test_only_my_meals_are_counted(Session):
    with Session() as db:
        me, other = _user(db, numbers=True), _user(db, telegram_id=7)
        _meal(db, me, NOW, 100, "мой")
        _meal(db, other, NOW, 900, "чужой")
        screen = st.open_view(db, 42, st.TODAY, NOW)
    assert "мой" in screen.text and "чужой" not in screen.text
    assert "🔢 За день: ≈ 100 ккал" in screen.text


# --- statistics: week ---------------------------------------------------------


def test_week_numbers_cover_the_period_and_average_over_logged_days(Session):
    with Session() as db:
        user = _user(db, numbers=True)
        _meal(db, user, NOW, 2000)  # today
        _meal(db, user, NOW - timedelta(days=2), 1000)  # Mon 26
        _meal(db, user, NOW - timedelta(days=7), 5000)  # Wed 21: outside
        screen = st.open_view(db, 42, st.WEEK, NOW)

    lines = screen.text.split("\n")
    assert lines[0] == "<b>📊 7 дней, 22–28 октября</b>"
    assert "ср ▰▰▰▰▰▰▰▰ 2 000</blockquote>" in lines
    assert "пн ▰▰▰▰▱▱▱▱ 1 000" in lines
    assert "В среднем за день с записями: ≈ 1 500 ккал" in screen.text
    assert "5 000" not in screen.text and "—" not in screen.text
    assert _labels(screen.markup)[-1] == "✓ 7 дней"


def test_empty_week_has_no_average(Session):
    with Session() as db:
        _user(db)
        screen = st.open_view(db, 42, st.WEEK, NOW)
    assert "За 7 дней записей нет." in screen.text
    assert "В среднем" not in screen.text


@pytest.mark.parametrize("data", ["st:", "st:zz", "st:t:1", "st:w:x"])
def test_unknown_tab_falls_back_to_today(Session, data):
    with Session() as db:
        _user(db)
        screen = st.handle_callback(db, 42, data, NOW)
    assert screen.text.startswith("<b>📊 Сегодня")


@pytest.mark.parametrize("tab", [st.YESTERDAY, st.WEEK])
def test_tab_callback_opens_that_tab(Session, tab):
    with Session() as db:
        _user(db)
        assert (
            st.handle_callback(db, 42, f"st:{tab}", NOW).text
            == st.open_view(db, 42, tab, NOW).text
        )


# --- weight -----------------------------------------------------------------


@pytest.mark.parametrize(
    "text, kg",
    [
        ("72,5", 72.5),
        ("72.5", 72.5),
        (" 72,5 кг ", 72.5),
        ("72 kg", 72.0),
        ("101.25", 101.25),
        ("150 г", None),  # grams: a food portion, not a weight
        ("5", None),
        ("19", None),
        ("401", None),
        ("1000", None),
        ("72,5 гречки", None),
        ("привет", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_kg(text, kg):
    assert wt.parse_kg(text) == kg


def test_record_saves_now_and_offers_undo(Session):
    with Session() as db:
        user = _user(db)
        _weigh(db, user, 73.0, NOW - timedelta(days=1))
        screen = wt.record(db, 42, 72.4)
        (saved,) = db.query(BodyMetric).filter(BodyMetric.weight == 72.4).all()

    assert screen.text == "✅ <b>Записано:</b> 72,4 кг\nС прошлого раза: −0,6 кг"
    assert screen.toast == "Записано"
    assert _callbacks(screen.markup) == [f"wt:u:{saved.id}"]
    assert saved.metrics == {"source": "bot"}


def test_first_record_creates_the_user(Session):
    with Session() as db:
        screen = wt.record(db, 55, 60, username="new")
        assert db.query(User).filter(User.telegram_id == 55).one().username == "new"
    assert "С прошлого раза" not in screen.text


def test_view_shows_latest_change_average_and_history(Session):
    with Session() as db:
        user = _user(db)
        _weigh(db, user, 75.0, NOW - timedelta(days=20))
        _weigh(db, user, 73.0, NOW - timedelta(days=3))
        _weigh(db, user, 72.0, NOW - timedelta(hours=1))
        _weigh(db, user, None, NOW)  # metrics without a weight: not a weighing
        screen = wt.open_view(db, 42, now=NOW)

    lines = screen.text.split("\n")
    assert lines[2] == "<b>72 кг</b> · ср 28 окт"
    assert "С прошлого раза: −1 кг" in lines
    assert "Среднее за 7 дней: 72,5 кг (2 зап.)" in lines
    assert lines.index("вс 25 окт · 73 кг") < lines.index("чт 8 окт · 75 кг")
    assert screen.text.endswith(wt.ASK)
    assert screen.markup is None


def test_empty_view_asks_for_a_weight(Session):
    with Session() as db:
        screen = wt.open_view(db, 42, now=NOW)
    assert "Записей пока нет." in screen.text and screen.text.endswith(wt.ASK)


def test_undo_deletes_then_a_second_tap_gets_a_notice(Session):
    with Session() as db:
        user = _user(db)
        metric = _weigh(db, user, 72.4, NOW)
        undone = wt.handle_callback(db, 42, f"wt:u:{metric.id}")
        again = wt.handle_callback(db, 42, f"wt:u:{metric.id}")
        assert db.query(BodyMetric).count() == 0

    assert "↩️ Отменено: 72,4 кг не записано." in undone.text
    assert undone.toast == "Отменено"
    assert "Эта запись уже отменена." in again.text and again.toast is None


def test_undo_refuses_another_users_and_non_bot_records(Session):
    with Session() as db:
        me, other = _user(db), _user(db, telegram_id=7)
        theirs = _weigh(db, other, 80, NOW)
        imported = _weigh(db, me, 70, NOW, source=None)
        for metric in (theirs, imported):
            screen = wt.handle_callback(db, 42, f"wt:u:{metric.id}")
            assert "Эта запись уже отменена." in screen.text
        assert db.query(BodyMetric).count() == 2


@pytest.mark.parametrize("data", ["wt:", "wt:u", "wt:u:x", "wt:z:1", "wt:u:1:2"])
def test_malformed_weight_data_gets_the_view(Session, data):
    with Session() as db:
        user = _user(db)
        _weigh(db, user, 72, NOW)
        screen = wt.handle_callback(db, 42, data)
        assert db.query(BodyMetric).count() == 1
    assert screen.text.startswith("<b>⚖️ Вес</b>")


def test_callback_data_fits_telegram():
    assert all(len(f"st:{code}".encode()) <= 64 for code, _ in st._TABS)
    assert len(f"wt:u:{2**63}".encode()) <= 64


# --- the bot side -------------------------------------------------------------


class _Msg:
    def __init__(self, text=None):
        self.text = text
        self.replies, self.kwargs = [], []

    async def reply_text(self, text, *args, **kwargs):
        self.replies.append(text)
        self.kwargs.append(kwargs)


def _update(text):
    message = _Msg(text)
    return message, SimpleNamespace(
        message=message, effective_user=SimpleNamespace(id=42, username="owner")
    )


def _run(handler, text):
    message, update = _update(text)
    state = asyncio.run(handler(update, SimpleNamespace(user_data={})))
    return state, message


def test_statistics_button_sends_today_with_tabs(Session):
    with Session() as db:
        _meal(db, _user(db), datetime.now(UTC), 321)
    state, message = _run(tg.show_statistics, "📊 Статистика")
    assert state == tg.CHOOSING_ACTION
    assert message.replies[-1].startswith("<b>📊 Сегодня")
    assert "🕐 " in message.replies[-1]
    assert "ккал" not in message.replies[-1]  # behind «🔢 Цифры» by default
    assert message.kwargs[-1]["parse_mode"] == "HTML"
    assert _callbacks(message.kwargs[-1]["reply_markup"])[-5:] == [
        "st:c:", "st:tn", "st:t", "st:y", "st:w",
    ]  # fmt: skip


def test_weight_button_then_typed_weight_saves_with_undo(Session):
    state, message = _run(tg.show_weight, "⚖️ Мой вес")
    assert state == tg.ENTERING_WEIGHT
    assert message.replies[-1].endswith(wt.ASK)

    state, message = _run(tg.process_weight, "72,5")
    assert state == tg.ENTERING_WEIGHT  # a second number corrects the first
    assert message.replies[-1].startswith("✅ <b>Записано:</b> 72,5 кг")
    assert message.kwargs[-1]["parse_mode"] == "HTML"
    with Session() as db:
        (metric,) = db.query(BodyMetric).all()
    assert metric.weight == 72.5
    assert _callbacks(message.kwargs[-1]["reply_markup"]) == [f"wt:u:{metric.id}"]


def test_not_a_weight_gets_the_hint_and_saves_nothing(Session):
    state, message = _run(tg.process_weight, "гречка 200 г")
    assert state == tg.ENTERING_WEIGHT
    assert message.replies[-1].startswith("Не похоже на вес.")
    with Session() as db:
        assert db.query(BodyMetric).count() == 0


def test_settings_button_shows_the_cost_switch_off(Session):
    state, message = _run(tg.show_settings, "⚙️ Настройки")
    assert state == tg.CHOOSING_ACTION
    assert message.replies[-1].startswith("<b>⚙️ Настройки</b>")
    assert "🕐 Часовой пояс: Тбилиси (UTC+4)" in message.replies[-1]
    markup = message.kwargs[-1]["reply_markup"]
    assert _labels(markup) == [
        "🕐 Часовой пояс",
        "🔢 Цифры сразу: выкл",
        "🗓 Итог недели: вкл",
        "💊 Рутины",
        "ℹ️ Как это работает",
        "💸 Стоимость запросов: выкл",
    ]
    assert _callbacks(markup) == [
        "set:tz",
        "set:num",
        "set:week",
        "rt:open",
        "hw:open",
        "set:costs",
    ]
    assert "Цель" not in message.replies[-1]  # no calorie goal (G5-R03)


def _costs_shown(Session, telegram_id=42):
    with Session() as db:
        return settings_screen.shows_costs(db, telegram_id)


def test_the_cost_switch_flips_and_says_so(Session):
    with Session() as db:
        user = _user(db)
        user.settings = {"diet": "veg"}  # another setting the switch must keep
        db.commit()

    update, query = _query("set:costs")
    asyncio.run(tg.settings_callback(update, SimpleNamespace()))
    (text,), kwargs = query.edit_message_text.await_args
    assert text.startswith("<i>✓ Стоимость запросов включена</i>")
    assert _labels(kwargs["reply_markup"])[-1] == "💸 Стоимость запросов: вкл"
    assert _costs_shown(Session)
    with Session() as db:
        assert db.query(User).one().settings == {"diet": "veg", "show_costs": True}

    asyncio.run(tg.settings_callback(update, SimpleNamespace()))
    (text,), _ = query.edit_message_text.await_args
    assert text.startswith("<i>✓ Стоимость запросов скрыта</i>")
    assert not _costs_shown(Session)


def test_the_cost_switch_works_before_the_first_meal(Session):
    update, _query_ = _query("set:costs")  # the owner, no User row yet
    asyncio.run(tg.settings_callback(update, SimpleNamespace()))
    assert _costs_shown(Session)


def test_an_unknown_settings_tap_changes_nothing(Session):
    with Session() as db:
        _user(db)
    update, query = _query("set:nope")
    asyncio.run(tg.settings_callback(update, SimpleNamespace()))
    (text,), _ = query.edit_message_text.await_args
    assert text.startswith("<b>⚙️ Настройки</b>")  # no notice
    assert not _costs_shown(Session)


def _query(data, telegram_id=42):
    query = SimpleNamespace(
        data=data, answer=AsyncMock(), edit_message_text=AsyncMock(), message=None
    )
    update = SimpleNamespace(
        callback_query=query, effective_user=SimpleNamespace(id=telegram_id)
    )
    return update, query


# --- settings: time zone ------------------------------------------------------


def _tap(Session, data, telegram_id=42):
    with Session() as db:
        return settings_screen.handle_callback(db, telegram_id, data, NOW)


def _rows(markup) -> list[list[str]]:
    return [[b.text for b in row] for row in markup.inline_keyboard]


def _stored_zone(Session, telegram_id=42):
    with Session() as db:
        user = db.query(User).filter(User.telegram_id == telegram_id).one()
        return (user.settings or {}).get(settings_screen.TIMEZONE)


def test_zone_screen_marks_the_current_city_and_goes_back_to_settings(Session):
    screen = _tap(Session, "set:tz")
    assert screen.text.startswith("<b>🕐 Часовой пояс</b>")
    assert "Сейчас: Тбилиси (UTC+4), там 12:00." in screen.text
    assert _rows(screen.markup) == [
        ["✓ Тбилиси", "Ереван", "Баку"],
        ["Москва", "Стамбул", "Киев"],
        ["Минск", "Берлин", "Лондон"],
        ["🌐 Другой: UTC±"],
        ["⬅️ Настройки"],
    ]
    assert _callbacks(screen.markup)[-1] == "set:open"
    back = _tap(Session, "set:open")
    assert back.text.startswith("<b>⚙️ Настройки</b>")  # no notice


def test_picking_a_city_saves_it_and_says_so_on_settings(Session):
    with Session() as db:
        user = _user(db)
        user.settings = {"show_costs": True}  # a setting the pick must keep
        db.commit()
    screen = _tap(Session, "set:tz:Europe/Moscow")
    assert screen.text.startswith("<i>✓ Часовой пояс: Москва (UTC+3)</i>")
    assert "🕐 Часовой пояс: Москва (UTC+3)" in screen.text
    with Session() as db:
        assert db.query(User).one().settings == {
            "show_costs": True,
            "timezone": "Europe/Moscow",
        }
    assert "✓ Москва" in _labels(_tap(Session, "set:tz").markup)


def test_a_pick_before_the_first_meal_creates_the_user(Session):
    _tap(Session, "set:tz:Asia/Yerevan")
    assert _stored_zone(Session) == "Asia/Yerevan"


def test_statistics_days_follow_the_chosen_zone(Session):
    with Session() as db:
        user = _user(db)
        # 00:30 on the 28th in Tbilisi, 23:30 on the 27th in Moscow.
        _meal(db, user, datetime(2026, 10, 27, 20, 30, tzinfo=UTC), 500, "блины")
    with Session() as db:
        assert "🕐 0:30" in st.open_view(db, 42, st.TODAY, NOW).text
    _tap(Session, "set:tz:Europe/Moscow")
    with Session() as db:
        today = st.open_view(db, 42, st.TODAY, NOW)
        yesterday = st.open_view(db, 42, st.YESTERDAY, NOW)
    assert "🕐 Еда" not in today.text
    assert "🕐 23:30" in yesterday.text


def test_weight_dates_follow_the_chosen_zone(Session):
    with Session() as db:
        _weigh(db, _user(db), 72, datetime(2026, 10, 27, 20, 30, tzinfo=UTC))
        assert "72 кг</b> · ср 28 окт" in wt.open_view(db, 42, now=NOW).text
    _tap(Session, "set:tz:Europe/London")
    with Session() as db:
        assert "72 кг</b> · вт 27 окт" in wt.open_view(db, 42, now=NOW).text


def test_a_city_follows_its_summer_time():
    july = datetime(2026, 7, 1, 12, tzinfo=UTC)
    assert settings_screen.zone_label("Europe/Berlin", july) == "Берлин (UTC+2)"
    assert settings_screen.zone_label("Europe/Berlin", NOW) == "Берлин (UTC+1)"
    assert settings_screen.zone_label("Etc/GMT-4", july) == "UTC+4"
    assert settings_screen.zone_label("Etc/GMT+3", july) == "UTC−3"


def test_offsets_page_through_and_pick_a_bare_offset(Session):
    screen = _tap(Session, "set:tzo:1")  # UTC−3 … UTC+5
    assert screen.text.startswith("<b>🕐 Часовой пояс: UTC±</b>")
    assert "Летнее время здесь не учитывается" in screen.text
    rows = _rows(screen.markup)
    assert rows[0] == ["UTC−3", "UTC−2", "UTC−1"]
    assert rows[2] == ["UTC+3", "UTC+4", "UTC+5"]  # Тбилиси is a city, not UTC+4
    assert rows[-2] == ["‹", "2/3", "›"]
    assert rows[-1] == ["⬅️ Часовой пояс"]
    assert _callbacks(screen.markup)[-4:] == [
        "set:tzo:0",
        "set:tzo:1",
        "set:tzo:2",
        "set:tz",
    ]
    picked = _tap(Session, "set:tz:Etc/GMT-5")
    assert picked.text.startswith("<i>✓ Часовой пояс: UTC+5</i>")
    assert _stored_zone(Session) == "Etc/GMT-5"
    # The «Другой» button opens the page that holds the chosen offset.
    assert _callbacks(_tap(Session, "set:tz").markup)[-2] == "set:tzo:1"


def test_the_offset_pages_cover_utc_minus_12_to_plus_14(Session):
    labels = [
        label
        for page in range(3)
        for row in _rows(_tap(Session, f"set:tzo:{page}").markup)[:-2]
        for label in row
    ]
    assert labels[0] == "UTC−12" and labels[-1] == "UTC+14"
    assert len(labels) == 27 and "UTC+0" in labels


@pytest.mark.parametrize(
    "data",
    [
        "set:tz:Asia/Pyongyang",  # a real zone the screen never offers
        "set:tz:Mars/Olympus",
        "set:tz:",
        "set:tzo:3",
        "set:tzo:-1",
        "set:tzo:x",
    ],
)
def test_a_forged_or_stale_zone_tap_changes_nothing(Session, data):
    with Session() as db:
        _user(db)
    screen = _tap(Session, data)
    assert screen.text.startswith("<b>⚙️ Настройки</b>")
    assert _stored_zone(Session) is None


def test_an_unknown_stored_zone_falls_back_to_the_default(Session):
    with Session() as db:
        user = _user(db)
        user.settings = {"timezone": "Mars/Olympus"}
        db.commit()
        assert settings_screen.zone_name(user) == "Asia/Tbilisi"
        assert st.open_view(db, 42, st.TODAY, NOW).text.startswith(
            "<b>📊 Сегодня, ср 28 октября</b>"
        )


def test_zone_screens_fit_telegram(Session):
    screens = [_tap(Session, "set:tz")] + [
        _tap(Session, f"set:tzo:{page}") for page in range(3)
    ]
    for screen in screens:
        rows = _rows(screen.markup)
        assert all(len(row) <= 3 for row in rows)
        assert len(rows) <= 5  # four rows of choices + the back row
        assert all(len(data.encode()) <= 64 for data in _callbacks(screen.markup))


def test_zone_tap_through_the_bot_edits_in_place(Session):
    update, query = _query("set:tz:Asia/Baku")
    asyncio.run(tg.settings_callback(update, SimpleNamespace()))
    (text,), kwargs = query.edit_message_text.await_args
    assert text.startswith("<i>✓ Часовой пояс: Баку (UTC+4)</i>")
    assert kwargs["parse_mode"] == "HTML"
    assert _stored_zone(Session) == "Asia/Baku"


def test_stats_tab_edits_in_place(Session):
    with Session() as db:
        _user(db)
    update, query = _query("st:w")
    asyncio.run(tg.stats_callback(update, SimpleNamespace()))
    query.answer.assert_awaited_once_with(None)
    (text,), kwargs = query.edit_message_text.await_args
    assert text.startswith("<b>📊 7 дней, ")
    assert kwargs["parse_mode"] == "HTML"


def _sidecar(tmp_path, monkeypatch):
    for name in ("in", "out"):
        (tmp_path / name).mkdir()
    (tmp_path / "heartbeat").write_text(str(int(time.time())))
    delivery = CardDelivery(PictureRenderer(tmp_path), fast_wait=0.01)
    monkeypatch.setattr(tg, "_card_delivery", delivery)
    return tmp_path


def test_with_the_sidecar_up_the_day_tab_turns_into_a_picture(
    Session, tmp_path, monkeypatch
):
    root = _sidecar(tmp_path, monkeypatch)
    with Session() as db:
        _user(db)
    update, query = _query("st:y")
    message = SimpleNamespace(
        photo=None,
        chat=SimpleNamespace(id=42),
        message_id=1,
        reply_photo=AsyncMock(
            return_value=SimpleNamespace(
                photo=None, chat=SimpleNamespace(id=42), message_id=2
            )
        ),
        delete=AsyncMock(),
    )
    query.message = message
    asyncio.run(tg.stats_callback(update, SimpleNamespace()))
    query.answer.assert_awaited_once_with(None)
    query.edit_message_text.assert_not_awaited()
    assert message.reply_photo.await_args.kwargs["caption"].startswith("<b>📊 Вчера")
    message.delete.assert_awaited_once()
    (job,) = (root / "in").glob("*.json")
    assert json.loads(job.read_text())["title"] == "Вчера"


def test_with_the_sidecar_up_the_menu_button_sends_a_picture(
    Session, tmp_path, monkeypatch
):
    root = _sidecar(tmp_path, monkeypatch)
    with Session() as db:
        _user(db)
    message, update = _update("📊 Статистика")
    message.reply_photo = AsyncMock(
        return_value=SimpleNamespace(
            photo=None, chat=SimpleNamespace(id=42), message_id=2
        )
    )
    asyncio.run(tg.show_statistics(update, SimpleNamespace(user_data={})))
    (job,) = (root / "in").glob("*.json")
    assert json.loads(job.read_text())["title"] == "Сегодня"
    assert message.reply_photo.await_args.kwargs["caption"].startswith("<b>📊 Сегодня")
    assert message.replies == []


def test_weight_undo_tap_deletes_and_toasts(Session):
    with Session() as db:
        metric = _weigh(db, _user(db), 72.4, NOW)
    update, query = _query(f"wt:u:{metric.id}")
    asyncio.run(tg.weight_callback(update, SimpleNamespace()))
    query.answer.assert_awaited_once_with("Отменено")
    assert "не записано" in query.edit_message_text.await_args.args[0]
    with Session() as db:
        assert db.query(BodyMetric).count() == 0


def test_the_cost_switch_needs_a_subscription(Session, monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "1")
    monkeypatch.setattr(tg, "check_subscription", AsyncMock(return_value=False))
    update, query = _query("set:costs")
    asyncio.run(tg.settings_callback(update, SimpleNamespace()))
    assert "подпиской" in query.answer.await_args.args[0]
    assert not _costs_shown(Session)


@pytest.mark.parametrize("callback", ["stats_callback", "weight_callback"])
def test_taps_without_subscription_change_nothing(Session, monkeypatch, callback):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "1")
    monkeypatch.setattr(tg, "check_subscription", AsyncMock(return_value=False))
    with Session() as db:
        metric = _weigh(db, _user(db), 72.4, NOW)
    update, query = _query(
        "st:w" if callback == "stats_callback" else f"wt:u:{metric.id}"
    )
    asyncio.run(getattr(tg, callback)(update, SimpleNamespace()))
    assert "подпиской" in query.answer.await_args.args[0]
    query.edit_message_text.assert_not_awaited()
    with Session() as db:
        assert db.query(BodyMetric).count() == 1


def test_texts_no_longer_promise_scale_integrations(Session, monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "1")
    monkeypatch.setattr(tg, "check_subscription", AsyncMock(return_value=False))
    _state, start = _run(tg.start, "/start")
    _state, about = _run(tg.handle_subscription_inquiry, "❓ О боте")
    for text in (start.replies[-1], about.replies[-1]):
        assert "Mi Scale" not in text and "Samsung" not in text
    assert "Дневник веса" in about.replies[-1]


# --- registration -------------------------------------------------------------


@pytest.fixture
def conv(monkeypatch):
    from telegram.ext import ConversationHandler

    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:fake")
    application = tg.create_bot_application()
    handlers = [h for group in application.handlers.values() for h in group]
    (conv,) = [h for h in handlers if isinstance(h, ConversationHandler)]
    return conv, handlers


def _first_match(handlers, text):
    """The callback PTB would pick for this text: the first handler that fits."""
    update = _text_update(text)
    for h in handlers:
        if getattr(h, "filters", None) is not None and h.check_update(update):
            return h.callback
    return None


def _text_update(text):
    from telegram import Chat, Message, Update
    from telegram import User as TgUser

    message = Message(
        message_id=1,
        date=NOW,
        chat=Chat(id=42, type="private"),
        from_user=TgUser(id=42, first_name="o", is_bot=False),
        text=text,
    )
    return Update(update_id=1, message=message)


_MENU = {
    "🍽 Добавить приём пищи": "add_meal",
    "⭐ Избранное": "show_favorites",
    "📊 Статистика": "show_statistics",
    "⚖️ Мой вес": "show_weight",
    "⚙️ Настройки": "show_settings",
}


def test_every_menu_button_has_a_handler_everywhere_the_menu_shows(conv):
    conv, _handlers = conv
    buttons = [b.text for row in tg.main_keyboard.keyboard for b in row]
    assert sorted(buttons) == sorted(_MENU)  # the table covers the real keyboard
    for where in (
        conv.entry_points,
        conv.states[tg.CHOOSING_ACTION],
        conv.states[tg.ENTERING_WEIGHT],
    ):
        for text in buttons:
            assert _first_match(where, text) is getattr(tg, _MENU[text]), text


@pytest.mark.parametrize(
    "text, where, expected",
    [
        ("72,5", "entry", "process_weight"),
        ("72,5", "menu", "process_weight"),
        ("72,5", "weight", "process_weight"),
        ("гречка", "weight", "process_weight"),  # gets the hint
        # Free text is not a weight. The typed tick looks at it and stays silent
        # unless it is exactly a routine (tests/test_routines.py).
        ("гречка", "entry", "typed_routine_tick"),
        ("гречка", "menu", "typed_routine_tick"),
        ("150 г", "menu", "typed_routine_tick"),
    ],
)
def test_typed_text_routing(conv, text, where, expected):
    conv, _handlers = conv
    handlers = {
        "entry": conv.entry_points,
        "menu": conv.states[tg.CHOOSING_ACTION],
        "weight": conv.states[tg.ENTERING_WEIGHT],
    }[where]
    found = _first_match(handlers, text)
    assert found is (getattr(tg, expected) if expected else None)


def test_inline_taps_are_registered_outside_the_conversation(conv):
    from telegram.ext import CallbackQueryHandler

    _conv, handlers = conv
    by_callback = {
        h.callback: h for h in handlers if isinstance(h, CallbackQueryHandler)
    }
    assert by_callback[tg.stats_callback].pattern.match("st:w")
    assert not by_callback[tg.stats_callback].pattern.match("wt:u:1")
    assert by_callback[tg.weight_callback].pattern.match("wt:u:1")
    assert not by_callback[tg.weight_callback].pattern.match("fav:p:0")
    assert by_callback[tg.favorites_callback].pattern.match("fav:p:0")
    assert by_callback[tg.settings_callback].pattern.match("set:costs")
    assert not by_callback[tg.settings_callback].pattern.match("st:w")


def test_week_title_spans_a_month_boundary():
    days = [(date(2026, 10, 29) + timedelta(days=i), []) for i in range(7)]
    assert st.week_screen(days, UTC).text.startswith(
        "<b>📊 7 дней, 29 октября – 4 ноября</b>"
    )


def _photo_update(caption=None):
    from telegram import Chat, Message, PhotoSize, Update
    from telegram import User as TgUser

    message = Message(
        message_id=1,
        date=NOW,
        chat=Chat(id=42, type="private"),
        from_user=TgUser(id=42, first_name="o", is_bot=False),
        photo=[PhotoSize("f", "u", 90, 90)],
        caption=caption,
    )
    return Update(update_id=1, message=message)


def _first_match_update(handlers, update):
    for h in handlers:
        if getattr(h, "filters", None) is not None and h.check_update(update):
            return h.callback
    return None


@pytest.mark.parametrize("caption", [None, "Вот такие 4 конфеты сегодня съела"])
@pytest.mark.parametrize("where", ["entry", "menu", "weight", "time"])
def test_a_photo_outside_the_meal_steps_is_a_meal(conv, where, caption):
    conv, _handlers = conv
    handlers = {
        "entry": conv.entry_points,
        "menu": conv.states[tg.CHOOSING_ACTION],
        "weight": conv.states[tg.ENTERING_WEIGHT],
        "time": conv.states[tg.ADDING_MEAL_TIME],
    }[where]
    assert _first_match_update(handlers, _photo_update(caption)) is (
        tg.quick_meal_photo
    )


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Отмена", "cancel"),  # not food: no paid analysis of the word
        ("📊 Статистика", "show_statistics"),
        ("🍽 Добавить приём пищи", "add_meal"),
        ("🍽 Добавить прием пищи", "add_meal"),  # a keyboard sent before the fix
        ("гречка 200 г", "process_meal_input"),  # no finding: food still goes on
    ],
)
def test_meal_step_takes_cancel_and_menu_before_the_analysis(conv, text, expected):
    conv, _handlers = conv
    found = _first_match(conv.states[tg.ADDING_MEAL], text)
    assert found is getattr(tg, expected)


def test_meal_step_photo_still_goes_to_the_analysis(conv):
    conv, _handlers = conv
    found = _first_match_update(conv.states[tg.ADDING_MEAL], _photo_update("обед"))
    assert found is tg.process_meal_input


# --- «🔢 Цифры» (stage 5, G5-R01/R02) ----------------------------------------


def test_numbers_off_day_lists_meals_without_calories_and_offers_the_button(Session):
    with Session() as db:
        user = _user(db)
        _meal(db, user, NOW - timedelta(hours=1), 250, "омлет", proteins=20)
        screen = st.open_view(db, 42, st.TODAY, NOW)
    assert "🕐 11:00" in screen.text
    for hidden in ("ккал", "За день", "Б 20", "омлет"):
        assert hidden not in screen.text
    assert _rows(screen.markup)[-2:] == [
        ["🔢 Цифры"],
        ["✓ Сегодня", "Вчера", "7 дней"],
    ]
    assert "st:tn" in _callbacks(screen.markup)


def test_open_numbers_stay_open_across_tabs_and_close_again(Session):
    with Session() as db:
        user = _user(db)
        _meal(db, user, NOW - timedelta(hours=1), 250, "омлет")
        opened = st.handle_callback(db, 42, "st:tn", NOW)
        assert "11:00 омлет — ≈ 250 ккал" in opened.text
        assert "🔢 За день: ≈ 250 ккал" in opened.text
        assert _rows(opened.markup)[-2] == ["✓ 🔢 Цифры"]
        assert _callbacks(opened.markup)[-5:] == [
            "st:c:n", "st:t", "st:tn", "st:yn", "st:wn",
        ]  # fmt: skip

        closed = st.handle_callback(db, 42, "st:t", NOW)
        assert "ккал" not in closed.text


def test_numbers_off_week_has_no_counts_and_no_days(Session):
    with Session() as db:
        user = _user(db)
        _meal(db, user, NOW, 2000)
        _meal(db, user, NOW - timedelta(days=2), 1000)
        screen = st.open_view(db, 42, st.WEEK, NOW)
    for hidden in ("ккал", "▱", "—", "В среднем", "из 7"):
        assert hidden not in screen.text  # an empty day is not a gap
    assert _callbacks(screen.markup)[0] == "st:wn"


def test_numbers_off_empty_week_says_so_without_a_miss(Session):
    with Session() as db:
        _user(db)
        screen = st.open_view(db, 42, st.WEEK, NOW)
    assert screen.text.endswith("Карточка соберётся с первой записью.")


@pytest.mark.parametrize("data", ["st:tx", "st:q", "st:wnn", "st:"])
def test_malformed_stats_data_falls_back_to_today(Session, data):
    with Session() as db:
        _user(db)
        screen = st.handle_callback(db, 42, data, NOW)
    assert screen.text.startswith("<b>📊 Сегодня")


def test_numbers_setting_flips_and_says_so_keeping_other_settings(Session):
    with Session() as db:
        user = _user(db)
        user.settings = {"diet": "veg"}
        db.commit()
    on = _tap(Session, "set:num")
    assert on.text.startswith("<i>✓ Цифры видны сразу</i>")
    assert "🔢 Цифры сразу: вкл" in _labels(on.markup)
    with Session() as db:
        stored = db.query(User).filter(User.telegram_id == 42).one().settings
        assert stored == {"diet": "veg", SHOW_NUMBERS: True}
        _meal(db, db.query(User).one(), NOW, 400)
        day = st.open_view(db, 42, st.TODAY, NOW)
    assert "400 ккал" in day.text
    assert _rows(day.markup)[-2:] == [
        [st.STEP_FIRST_BUTTON],
        ["✓ Сегодня", "Вчера", "7 дней"],
    ]  # no numbers button

    off = _tap(Session, "set:num")
    assert off.text.startswith("<i>✓ Цифры теперь за кнопкой</i>")
    assert "🔢 Цифры сразу: выкл" in _labels(off.markup)
