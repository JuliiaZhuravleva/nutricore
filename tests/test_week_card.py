"""The week card (stage 5, slice E): what was, so two records never look worse than seven."""

import asyncio
import logging
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker
from telegram.error import BadRequest, Forbidden, NetworkError, TimedOut

from app.core.config import settings
from app.crud.crud_routine_tick import crud_routine_tick
from app.db.base import Base
from app.models.meal import Meal
from app.models.user import User
from app.services import explain_screen, routines, settings_screen
from app.services import stats_screen as st
from app.services import week_card, week_push
from app.services.card_delivery import CardDelivery, may_have_arrived
from app.services.picture_renderer import PictureRenderer
from app.services.settings_screen import SHOW_NUMBERS, WEEK_PUSH, WEEK_PUSH_SENT

ZONE = ZoneInfo("Asia/Tbilisi")
# Fri 30 Oct 2026, 20:00 in Tbilisi (UTC+4): the tab covers 24–30 October.
NOW = datetime(2026, 10, 30, 16, 0, tzinfo=UTC)
# Sun 25 Oct 2026, 22:00 in Tbilisi: the summary covers 19–25 October.
SUNDAY = datetime(2026, 10, 25, 18, 0, tzinfo=UTC)
MONDAY = date(2026, 10, 19)


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(
        week_push.crud_subscription, "is_active_subscription", lambda _db, _id: True
    )
    monkeypatch.setattr(settings, "DEFAULT_TIMEZONE", "Asia/Tbilisi")
    monkeypatch.setattr(settings, "BOT_ACCESS_MODE", "open", raising=False)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine)() as session:
        session.add(User(id=1, telegram_id=42, settings={}))
        session.commit()
        yield session
    engine.dispose()


def _at(day, hour=9, month=10):
    return datetime(2026, month, day, hour, tzinfo=ZONE).astimezone(UTC)


def _item(name, *groups):
    return {"name": name, "groups": list(groups)}


def _meal(db, when, *items, groups=None, calories=400, foods=None):
    known = groups
    if known is None:
        known = sorted({g for item in items for g in item["groups"]})
    meal = Meal(
        user_id=1,
        description="еда",
        meal_time=when,
        calories=calories,
        proteins=10,
        fats=5,
        carbohydrates=50,
        nutrients={"foods": foods} if foods else {},
        ai_analysis=(
            {"food_items": list(items), "food_groups": known}
            if items or groups is not None
            else {}
        ),
    )
    db.add(meal)
    db.commit()
    return meal


def _week(db, opened=False):
    return st.open_view(db, 42, st.WEEK, NOW, opened)


def _rows(screen):
    if screen.markup is None:
        return []
    return [[b.text for b in row] for row in screen.markup.inline_keyboard]


def _user(db):
    return db.get(User, 1)


# --- what the card says -------------------------------------------------------


def test_a_full_week_says_what_was_as_in_the_mock(db):
    _meal(
        db,
        _at(24),
        _item("нут", "legumes", "anchor"),
        _item("брокколи", "veg"),
        _item("булгур", "grain"),
    )
    _meal(db, _at(25), _item("нут", "legumes", "anchor"), _item("яблоко", "fruit"))
    _meal(db, _at(10, month=9), _item("овёс", "grain"))  # history: earlier foods
    text = _week(db).text
    lines = text.split("\n")
    assert lines[0] == "<b>📊 7 дней, 24–30 октября</b>"
    assert "🌱 4 разных растения" in lines  # нут only once
    assert "🫘 бобовые 2 дн. · ⚓ опора 2 дн." in lines
    assert "🥦 овощи 1 дн. · 🍎 фрукты 1 дн." in lines
    assert "🌾 цельные злаки 1 дн." in lines


@pytest.mark.parametrize(
    "count, words",
    [
        (1, "1 растение"),
        (2, "2 разных растения"),
        (5, "5 разных растений"),
        (11, "11 разных растений"),
        (21, "21 разное растение"),
        (23, "23 разных растения"),
    ],
)
def test_the_plant_line_agrees_with_its_number(count, words):
    assert week_card.plants_phrase(count) == words


def test_a_group_absent_all_week_is_not_named_and_nothing_counts_days(db):
    _meal(db, _at(24), _item("брокколи", "veg"))
    text = _week(db).text
    for word in ("бобовые", "опора", "фрукты", "злаки", "орехи", "кальций", "омега"):
        assert word not in text
    assert not re.search(r"из 7|из\s+\d|с записями из|Среднее по", text)


def test_a_plant_is_counted_by_its_food_not_by_animal_or_calcium_tags(db):
    _meal(
        db,
        _at(24),
        _item("курица", "anchor"),
        _item("йогурт", "anchor", "calcium"),
        _item("тофу", "legumes", "anchor", "calcium"),
    )
    assert "🌱 1 растение" in _week(db).text


def test_spelling_and_case_of_one_food_count_once(db):
    _meal(db, _at(24), _item("Свёкла", "veg"))
    _meal(db, _at(25), _item("свекла ", "veg"))
    assert "🌱 1 растение" in _week(db).text


def test_a_group_the_user_unticked_hides_its_plant_too(db):
    _meal(db, _at(24), _item("брокколи", "veg"), groups=[])
    screen = _week(db)
    assert "растени" not in screen.text and "овощи" not in screen.text


def test_records_without_tags_say_so_and_never_claim_an_empty_week(db):
    _meal(db, _at(24))
    screen = _week(db)
    assert week_card.NO_TAGS in screen.text
    assert "записей нет" not in screen.text
    assert _rows(screen)[0] == ["🔢 Цифры"]


def test_two_records_show_the_same_shape_as_seven_and_no_empty_grid(db):
    _meal(db, _at(26), _item("брокколи", "veg"))
    _meal(db, _at(28), _item("рис", "grain"))
    screen = _week(db)
    assert "—" not in screen.text and "▱" not in screen.text
    assert [g["key"] for g in screen.picture["groups"]] == ["veg", "grain"]


# --- one real finding ----------------------------------------------------------


def test_new_food_is_the_first_one_never_seen_before(db):
    _meal(db, _at(23), _item("овёс", "grain"))
    _meal(db, _at(25), _item("овёс", "grain"))
    _meal(db, _at(26), _item("булгур", "grain"), _item("кускус", "grain"))
    assert "✨ Новое за 7 дней: булгур" in _week(db).text
    assert "кускус" not in _week(db).text  # at most one new food a week


def test_a_food_known_only_from_an_older_untagged_meal_is_not_new(db):
    _meal(db, _at(23), foods=["Булгур"])
    _meal(db, _at(26), _item("булгур", "grain"))
    assert "✨" not in _week(db).text


def test_without_any_history_nothing_is_called_new(db):
    _meal(db, _at(26), _item("булгур", "grain"))
    assert "✨" not in _week(db).text


def test_coming_back_after_a_pause_is_the_finding(db):
    _meal(db, _at(10), _item("овёс", "grain"))
    _meal(db, _at(26), _item("булгур", "grain"))
    screen = _week(db)
    assert "✨ Снова с записями — после паузы" in screen.text
    assert "Новое" not in screen.text  # one finding, not two
    assert screen.picture["win"] == {
        "kick": "После паузы",
        "text": "Снова с записями",
    }


def test_a_short_gap_is_not_a_pause(db):
    _meal(db, _at(20), _item("овёс", "grain"))
    _meal(db, _at(26), _item("овёс", "grain"))
    assert "✨" not in _week(db).text


def test_nothing_to_find_means_no_line_at_all(db):
    _meal(db, _at(23), _item("овёс", "grain"))
    _meal(db, _at(26), _item("овёс", "grain"))
    assert "✨" not in _week(db).text
    assert _week(db).picture.get("win") is None


# --- routines -----------------------------------------------------------------


def test_ticked_routines_are_named_without_a_count_and_the_rest_never(db):
    user = _user(db)
    user.settings = {
        routines.ROUTINES: [
            {"id": "a", "name": "Витамин D", "icon": "💊", "days": "all"},
            {"id": "b", "name": "Прогулка", "icon": "🚶", "days": "all"},
        ]
    }
    db.commit()
    crud_routine_tick.tick(db, 1, "a", date(2026, 10, 26))
    crud_routine_tick.tick(db, 1, "a", date(2026, 10, 27))
    crud_routine_tick.tick(db, 1, "b", date(2026, 10, 23))  # before the period
    _meal(db, _at(26), _item("овёс", "grain"))
    screen = _week(db)
    assert "Витамин D ✓" in screen.text
    assert "Прогулка" not in screen.text
    assert screen.picture["routines"] == ["Витамин D"]


# --- numbers ------------------------------------------------------------------


def test_numbers_wait_behind_the_button_and_open_as_a_quote_of_logged_days(db):
    _meal(db, _at(26), _item("овёс", "grain"), calories=1000)
    _meal(db, _at(28), _item("овёс", "grain"), calories=2000)
    closed = _week(db)
    assert "ккал" not in closed.text
    assert _rows(closed) == [["🔢 Цифры"], ["Сегодня", "Вчера", "✓ 7 дней"]]
    opened = _week(db, opened=True)
    assert _rows(opened)[0] == ["✓ 🔢 Цифры"]
    quote = re.search(
        r"<blockquote expandable>(.*)</blockquote>", opened.text, re.S
    ).group(1)
    lines = quote.split("\n")
    assert lines[0].startswith("🔢 В среднем за день с записями: ≈ 1 500 ккал · Б 10 г")
    assert lines[1:] == ["пн ▰▰▰▰▱▱▱▱ 1 000", "ср ▰▰▰▰▰▰▰▰ 2 000"]


def test_settings_showing_numbers_leaves_no_button(db):
    _user(db).settings = {SHOW_NUMBERS: True}
    db.commit()
    _meal(db, _at(26), _item("овёс", "grain"))
    screen = _week(db)
    assert "ккал" in screen.text
    assert _rows(screen) == [["Сегодня", "Вчера", "✓ 7 дней"]]


def test_no_weight_on_the_card(db):
    _meal(db, _at(26), _item("овёс", "grain"))
    assert not re.search(r"кг|вес", _week(db, opened=True).text)


# --- empty --------------------------------------------------------------------


def test_an_empty_week_offers_a_record_and_no_numbers(db):
    screen = _week(db)
    assert screen.text.split("\n") == [
        "<b>📊 7 дней, 24–30 октября</b>",
        "",
        "🍽 За 7 дней записей нет. Карточка соберётся с первой записью.",
    ]
    assert _rows(screen) == [
        ["🍽 Добавить приём пищи"],
        ["Сегодня", "Вчера", "✓ 7 дней"],
    ]
    assert screen.picture["empty"] is True


def test_the_period_spans_a_month_boundary():
    days = [(date(2026, 9, 28) + timedelta(days=i), []) for i in range(7)]
    assert st.week_screen(days, ZONE).text.startswith(
        "<b>📊 7 дней, 28 сентября – 4 октября</b>"
    )


# --- the picture --------------------------------------------------------------


def test_the_picture_carries_the_same_facts_as_the_caption(db):
    _meal(db, _at(23), _item("овёс", "grain"))
    _meal(db, _at(25), _item("овёс", "grain"))
    _meal(db, _at(26), _item("булгур", "grain"), _item("нут", "legumes"))
    picture = _week(db).picture
    assert picture["view"] == "week"
    assert picture["title"] == "7 дней"
    assert picture["period"] == "24–30 октября"
    assert picture["plants"] == 3
    assert picture["plantsLabel"] == "разных растения"
    assert picture["groups"] == [
        {"key": "legumes", "days": 1},
        {"key": "grain", "days": 2},
    ]
    assert picture["win"] == {
        "kick": "Новое за 7 дней",
        "text": "Булгур — впервые в записях",
    }


# --- the summary: Monday to Sunday --------------------------------------------


def test_the_summary_is_the_calendar_week_under_its_own_name(db):
    _meal(db, _at(19), _item("овёс", "grain"))
    _meal(db, _at(25), _item("нут", "legumes"))
    _meal(db, _at(26), _item("рис", "grain"))  # next Monday: not this week
    screen = st.summary_view(db, _user(db), MONDAY, SUNDAY)
    assert screen.text.startswith("<b>📊 Неделя, 19–25 октября</b>")
    assert "🌱 2 разных растения" in screen.text
    assert _rows(screen) == [["🔢 Цифры"]]
    assert screen.picture["title"] == "Неделя"


def test_the_summary_says_where_to_turn_it_off_only_the_first_time(db):
    _meal(db, _at(19), _item("овёс", "grain"))
    first = st.summary_view(db, _user(db), MONDAY, SUNDAY, first=True)
    later = st.summary_view(db, _user(db), MONDAY, SUNDAY)
    note = "Итог приходит по воскресеньям в 22:00 без звука. Выключить — ⚙️ Настройки."
    assert first.text.endswith(f"<i>{note}</i>")
    assert note not in later.text


def test_a_new_food_on_the_summary_says_this_week(db):
    _meal(db, _at(15), _item("овёс", "grain"))
    _meal(db, _at(20), _item("булгур", "grain"))
    screen = st.summary_view(db, _user(db), MONDAY, SUNDAY)
    assert "✨ Новое на этой неделе: булгур" in screen.text


def test_numbers_on_the_summary_stay_on_that_week_days_later(db):
    _meal(db, _at(19), _item("овёс", "grain"), calories=900)
    _meal(db, _at(29), _item("рис", "grain"), calories=5000)  # after that week
    closed = st.summary_view(db, _user(db), MONDAY, SUNDAY)
    data = closed.markup.inline_keyboard[0][0].callback_data
    opened = st.handle_callback(db, 42, data, NOW)
    assert opened.text.startswith("<b>📊 Неделя, 19–25 октября</b>")
    assert "900" in opened.text and "5 000" not in opened.text
    assert _rows(opened) == [["✓ 🔢 Цифры"]]
    back = opened.markup.inline_keyboard[0][0].callback_data
    assert "ккал" not in st.handle_callback(db, 42, back, NOW).text


@pytest.mark.parametrize(
    "data", ["st:i:", "st:i:banana", "st:i:20261399", "st:i:2026:n"]
)
def test_a_broken_summary_button_lands_on_today(db, data):
    screen = st.handle_callback(db, 42, data, NOW)
    assert screen.text.startswith("<b>📊 Сегодня")


# --- when it is sent ----------------------------------------------------------


@pytest.mark.parametrize(
    "moment, monday",
    [
        (datetime(2026, 10, 25, 22, 0), MONDAY),
        (datetime(2026, 10, 25, 23, 59), MONDAY),
        (datetime(2026, 10, 25, 21, 59), None),
        (datetime(2026, 10, 26, 0, 1), None),  # Monday: too late to be Sunday's
        (datetime(2026, 10, 24, 22, 0), None),
    ],
)
def test_the_window_is_sunday_evening(moment, monday):
    assert week_push.due_monday(moment) == monday


def _never_made():
    error = NetworkError("httpx.ConnectError: no route")
    error.__cause__ = httpx.ConnectError("no route")
    return error


def _delivery():
    return SimpleNamespace(push=AsyncMock())


def _run(db, delivery, now=SUNDAY):
    return asyncio.run(week_push.run(db, object(), delivery, now))


def test_sunday_evening_sends_the_week_once_with_the_note_first_time(db):
    _meal(db, _at(19), _item("овёс", "grain"))
    delivery = _delivery()
    assert _run(db, delivery)["sent"] == 1
    assert _run(db, delivery, SUNDAY + timedelta(minutes=15))["sent"] == 0
    assert delivery.push.await_count == 1
    _bot, chat_id, screen = delivery.push.await_args.args
    assert chat_id == 42
    assert screen.text.startswith("<b>📊 Неделя, 19–25 октября</b>")
    assert "Выключить — ⚙️ Настройки" in screen.text
    assert _user(db).settings[WEEK_PUSH_SENT] == "2026-10-19"


def test_the_next_week_has_no_note(db):
    _user(db).settings = {WEEK_PUSH_SENT: "2026-10-12"}
    db.commit()
    _meal(db, _at(19), _item("овёс", "grain"))
    delivery = _delivery()
    _run(db, delivery)
    assert "Выключить" not in delivery.push.await_args.args[2].text


def test_a_week_with_no_records_is_not_sent_or_marked(db):
    delivery = _delivery()
    assert _run(db, delivery)["sent"] == 0
    delivery.push.assert_not_awaited()
    assert WEEK_PUSH_SENT not in _user(db).settings


def test_the_hour_follows_the_users_zone(db):
    user = _user(db)
    user.settings = {settings_screen.TIMEZONE: "Asia/Tokyo"}
    db.commit()
    _meal(db, _at(19), _item("овёс", "grain"))
    delivery = _delivery()
    # 18:00 UTC on Sunday is already 03:00 Monday in Tokyo.
    assert _run(db, delivery)["sent"] == 0
    # 13:00 UTC on Sunday is 22:00 in Tokyo, and only 17:00 in Tbilisi.
    assert _run(db, delivery, datetime(2026, 10, 25, 13, 0, tzinfo=UTC))["sent"] == 1


def test_turned_off_means_not_sent(db):
    _user(db).settings = {WEEK_PUSH: False}
    db.commit()
    _meal(db, _at(19), _item("овёс", "grain"))
    delivery = _delivery()
    assert _run(db, delivery)["sent"] == 0
    delivery.push.assert_not_awaited()


def test_a_failed_send_is_tried_again_and_stays_first(db):
    _meal(db, _at(19), _item("овёс", "grain"))
    delivery = _delivery()
    delivery.push.side_effect = _never_made()
    assert _run(db, delivery)["failed"] == 1
    assert WEEK_PUSH_SENT not in _user(db).settings
    delivery.push.side_effect = None
    assert _run(db, delivery)["sent"] == 1
    assert "Выключить" in delivery.push.await_args.args[2].text


def test_a_user_who_blocked_the_bot_is_not_tried_all_evening(db):
    _meal(db, _at(19), _item("овёс", "grain"))
    delivery = _delivery()
    delivery.push.side_effect = Forbidden("blocked")
    assert _run(db, delivery)["blocked"] == 1
    assert _user(db).settings[WEEK_PUSH_SENT] == "2026-10-19"


def test_one_failed_user_does_not_stop_the_next(db):
    db.add(User(id=2, telegram_id=43, settings={}))
    db.commit()
    _meal(db, _at(19), _item("овёс", "grain"))
    db.add(Meal(user_id=2, description="еда", meal_time=_at(19), ai_analysis={}))
    db.commit()
    delivery = _delivery()
    delivery.push.side_effect = [_never_made(), None]
    assert _run(db, delivery) == {"sent": 1, "failed": 1, "blocked": 0}


def test_an_answer_lost_on_the_way_is_not_sent_twice(db):
    _meal(db, _at(19), _item("овёс", "grain"))
    delivery = _delivery()
    delivery.push.side_effect = TimedOut("no answer")
    assert _run(db, delivery)["failed"] == 1
    assert _user(db).settings[WEEK_PUSH_SENT] == "2026-10-19"


def test_who_may_not_use_the_bot_gets_no_summary(db, monkeypatch):
    monkeypatch.setattr(settings, "BOT_ACCESS_MODE", "closed")
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "[]", raising=False)
    _meal(db, _at(19), _item("овёс", "grain"))
    delivery = _delivery()
    assert _run(db, delivery)["sent"] == 0
    delivery.push.assert_not_awaited()


def test_the_worker_does_not_log_the_token():
    from celery_app.tasks import week_push as task  # noqa: F401

    assert logging.getLogger("httpx").level >= logging.WARNING


def test_nobody_due_means_no_bot_is_started(db, monkeypatch):
    from celery_app.tasks import week_push as task

    _user(db).settings = {WEEK_PUSH: False}
    db.commit()
    monkeypatch.setattr(task, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        task.custom_emoji,
        "build_bot",
        lambda *a: pytest.fail("a bot was started for nobody"),
    )
    assert task.send_week_summaries() == {"sent": 0, "failed": 0, "blocked": 0}


# --- the silent send ----------------------------------------------------------


def _png_delivery(tmp_path, drawn=True):
    root = tmp_path / "render"
    for part in ("in", "out"):
        (root / part).mkdir(parents=True)
    renderer = PictureRenderer(root, poll=0.01)
    if drawn:
        (root / "heartbeat").write_text(str(int(datetime.now().timestamp())))
    return CardDelivery(renderer, slow_wait=0.05), renderer, root


def _bot():
    return SimpleNamespace(send_photo=AsyncMock(), send_message=AsyncMock())


def _push_screen(db):
    _meal(db, _at(19), _item("овёс", "grain"))
    return st.summary_view(db, _user(db), MONDAY, SUNDAY)


def test_the_picture_goes_silently_with_the_whole_text(db, tmp_path):
    delivery, renderer, root = _png_delivery(tmp_path)
    screen = _push_screen(db)
    ticket = renderer.request(screen.picture)
    (root / "out" / f"{ticket.name}.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    bot = _bot()
    asyncio.run(delivery.push(bot, 42, screen))
    kwargs = bot.send_photo.await_args.kwargs
    assert kwargs["chat_id"] == 42
    assert kwargs["caption"] == screen.text
    assert kwargs["disable_notification"] is True
    assert kwargs["reply_markup"] is screen.markup
    bot.send_message.assert_not_awaited()


def test_a_stopped_renderer_sends_the_text_silently(db, tmp_path):
    delivery, _renderer, _root = _png_delivery(tmp_path, drawn=False)
    screen = _push_screen(db)
    bot = _bot()
    asyncio.run(delivery.push(bot, 42, screen))
    bot.send_photo.assert_not_awaited()
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["text"] == screen.text
    assert kwargs["disable_notification"] is True


def test_a_card_not_drawn_in_time_sends_the_text_and_no_loader(db, tmp_path):
    delivery, _renderer, _root = _png_delivery(tmp_path)
    bot = _bot()
    asyncio.run(delivery.push(bot, 42, _push_screen(db)))
    bot.send_photo.assert_not_awaited()
    bot.send_message.assert_awaited_once()


def test_a_photo_whose_answer_was_lost_gets_no_text_on_top(db, tmp_path):
    delivery, renderer, root = _png_delivery(tmp_path)
    screen = _push_screen(db)
    ticket = renderer.request(screen.picture)
    (root / "out" / f"{ticket.name}.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    bot = _bot()
    bot.send_photo.side_effect = TimedOut("no answer")
    with pytest.raises(TimedOut):
        asyncio.run(delivery.push(bot, 42, screen))
    bot.send_message.assert_not_awaited()


def test_a_failed_photo_falls_back_to_silent_text(db, tmp_path):
    delivery, renderer, root = _png_delivery(tmp_path)
    screen = _push_screen(db)
    ticket = renderer.request(screen.picture)
    (root / "out" / f"{ticket.name}.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    bot = _bot()
    bot.send_photo.side_effect = BadRequest("no such photo")
    asyncio.run(delivery.push(bot, 42, screen))
    assert bot.send_message.await_args.kwargs["disable_notification"] is True


def test_a_broken_connection_on_the_photo_gets_no_text_on_top(db, tmp_path):
    delivery, renderer, root = _png_delivery(tmp_path)
    screen = _push_screen(db)
    ticket = renderer.request(screen.picture)
    (root / "out" / f"{ticket.name}.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    bot = _bot()
    bot.send_photo.side_effect = NetworkError("httpx.ReadError: gone")
    with pytest.raises(NetworkError):
        asyncio.run(delivery.push(bot, 42, screen))
    bot.send_message.assert_not_awaited()


@pytest.mark.parametrize(
    "cause, arrived",
    [
        (httpx.ReadError("gone"), True),
        (httpx.RemoteProtocolError("gone"), True),
        (httpx.ReadTimeout("slow"), True),
        (httpx.ConnectError("no route"), False),
        (httpx.ConnectTimeout("no route"), False),
        (httpx.PoolTimeout("busy"), False),
    ],
)
def test_what_may_have_arrived(cause, arrived):
    error = TimedOut("t") if isinstance(cause, httpx.TimeoutException) else None
    error = error or NetworkError("n")
    error.__cause__ = cause
    assert may_have_arrived(error) is arrived
    assert may_have_arrived(BadRequest("no")) is False


def test_a_run_left_queued_finds_the_first_runs_mark(db, monkeypatch):
    _meal(db, _at(19), _item("овёс", "grain"))
    stale = week_push.users_due(db, SUNDAY)
    monkeypatch.setattr(week_push, "users_due", lambda _db, _now: stale)
    db.execute(
        update(User).values(settings={WEEK_PUSH_SENT: "2026-10-19"})
    )  # the other run, past this session's identity map
    db.commit()
    delivery = _delivery()
    assert _run(db, delivery) == {"sent": 0, "failed": 0, "blocked": 0}
    delivery.push.assert_not_awaited()


def test_one_users_bad_data_costs_nobody_else_their_summary(db, monkeypatch):
    _meal(db, _at(19), _item("овёс", "grain"))
    real = week_push._due_week
    calls = []

    def flaky(session, user, now):
        calls.append(user.telegram_id)
        if user.telegram_id == 7:
            raise ValueError("bad row")
        return real(session, user, now)

    monkeypatch.setattr(week_push, "_due_week", flaky)
    db.add(User(id=0, telegram_id=7, settings={}))  # checked first
    db.commit()
    assert [user.telegram_id for user, _ in week_push.users_due(db, SUNDAY)] == [42]
    assert calls == [7, 42]


def test_no_subscription_no_summary_as_with_the_screens_themselves(db, monkeypatch):
    monkeypatch.setattr(
        week_push.crud_subscription, "is_active_subscription", lambda _db, _id: False
    )
    _meal(db, _at(19), _item("овёс", "grain"))
    assert week_push.users_due(db, SUNDAY) == []
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "[42]", raising=False)
    assert 42 in settings.admin_ids
    assert len(week_push.users_due(db, SUNDAY)) == 1


# --- ⚙️ -----------------------------------------------------------------------


def test_settings_turn_the_summary_off_with_one_tap_and_back(db):
    screen = settings_screen.open_view(db, 42)
    assert "🗓 Итог недели: воскресенье, 22:00, без звука." in screen.text
    labels = [b.text for row in screen.markup.inline_keyboard for b in row]
    assert "🗓 Итог недели: вкл" in labels
    assert len(screen.markup.inline_keyboard) <= 5

    off = settings_screen.handle_callback(db, 42, "set:week")
    assert off.text.startswith("<i>✓ Итог недели выключен</i>")
    assert "🗓 Итог недели: не присылаю." in off.text
    assert _user(db).settings[WEEK_PUSH] is False
    on = settings_screen.handle_callback(db, 42, "set:week")
    assert on.text.startswith("<i>✓ Итог недели включён</i>")
    assert _user(db).settings[WEEK_PUSH] is True


# --- explained, scheduled, packaged -------------------------------------------


def test_the_bot_explains_the_week_and_owns_up_to_its_choices():
    topic = explain_screen.TOPICS["week"]
    assert "30" in topic.why  # the famous target, and why there is none
    assert "проект" in topic.why


def test_beat_runs_the_send_often_enough_for_every_zone():
    from celery_app.celery_app import app as celery

    entry = celery.conf.beat_schedule["week-summaries"]
    assert entry["task"] == "celery_app.tasks.week_push.send_week_summaries"
    assert entry["schedule"].minute == {0, 15, 30, 45}
    assert entry["options"]["expires"] < 15 * 60
    assert "celery_app.tasks.week_push" in celery.conf.include


@pytest.mark.parametrize("name", ["docker-compose.prod.yml", "docker-compose.yml"])
def test_the_worker_shares_the_job_directory(name):
    compose = (Path(__file__).parent.parent / name).read_text()
    worker = compose[
        compose.index("\n  celery_worker:\n") : compose.index("\n  celery_beat:\n")
    ]
    assert "render_jobs:/render" in worker


def test_a_long_new_name_keeps_its_end_on_the_picture():
    week = week_card.WeekFacts(new_food="Очень длинное название продукта")
    text = week_card.finding_block(week, "Новое за 7 дней")["text"]
    assert text.startswith("Очень длинное") and "впервые" not in text
    short_one = week_card.WeekFacts(new_food="булгур")
    assert (
        week_card.finding_block(short_one, "Новое за 7 дней")["text"]
        == "Булгур — впервые в записях"
    )


def test_one_plant_on_the_picture_reads_as_one_plant():
    assert week_card.plants_label(1) == "растение"
    assert week_card.plants_label(21) == "разное растение"
