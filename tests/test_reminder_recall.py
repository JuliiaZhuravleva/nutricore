"""§3.2 and R16: what a voice or a text after a 🔔 reminder is bound to, through the
real application at the wire (stage 6, slice R1b).

Every message is a real ``Update`` into ``create_bot_application()``; Telegram, the
model and the hearing are fakes (``tests/test_capture_router.py``). No network, no
paid call. The reminder went out on Friday 6 November at 21:00 in Tbilisi (UTC+4).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.crud.crud_reminder_send import crud_reminder_send
from app.models import Meal, MealSource
from app.models.reminder_send import ReminderSend
from app.models.user import User
from app.services import capture, favorites, meal_card, reminders, routines
from app.services import telegram as tg
from app.services import voice
from tests.test_capture_router import (  # noqa: F401 (fixtures)
    CHAT,
    OWNER,
    PHOTO,
    Session,
    _item_names,
    _kb,
    _message,
    db,
    owner,
    reader,
    send,
)

ZONE = ZoneInfo("Asia/Tbilisi")
DAY = date(2026, 11, 6)  # a Friday
REMINDER = 4321  # the reminder's message in the chat
DAY_LINE = "Записано на пт 6 ноября, без времени"


def at(hour: int, minute: int = 0, day: date = DAY) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=ZONE).astimezone(UTC)


def _utc(moment: datetime) -> datetime:
    # SQLite hands back naive datetimes; the columns are written in UTC.
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


@pytest.fixture
def starred(db, owner, evening):
    """A ⭐ «Гречка» tapped on the reminder at 21:03, as the bot does it: the
    reminder's message then holds that meal (telegram.reminders_callback)."""
    old = at(12, 0, DAY - timedelta(days=100))
    source = Meal(
        user_id=owner.id,
        description="Гречка",
        meal_time=old,
        sent_at=old,
        calories=300,
        proteins=10,
        fats=5,
        carbohydrates=40,
    )
    db.add(source)
    db.commit()
    fav, _ = favorites.save_from_meal(db, source, "Гречка")
    data = f"rm:f:{evening.id}:{fav.id}"
    screen = reminders.handle_callback(
        db, OWNER, data, now=at(21, 3), message_id=REMINDER
    )
    capture.tapped(db, CHAT, REMINDER, screen)
    assert screen.meal_id is not None
    return screen.meal_id


@pytest.fixture
def evening(db, owner, reader, monkeypatch):
    """The evening reminder of DAY, sent at 21:00 and not yet answered."""
    row = crud_reminder_send.claim(
        db,
        user_id=owner.id,
        local_day=DAY,
        slot="21:00",
        kind=reminders.EVENING,
        chat_id=CHAT,
        now=at(21),
    )
    row.status = "sent"
    row.message_id = REMINDER
    db.commit()
    return row


def say(when: datetime, body: str, **fields) -> dict:
    return _message(text=body, date=int(when.timestamp()), **fields)


def snap(when: datetime, **fields) -> dict:
    return _message(photo=PHOTO, date=int(when.timestamp()), **fields)


def speak(when: datetime) -> dict:
    voice_note = {"file_id": "v", "file_unique_id": "vu", "duration": 3}
    return _message(voice=voice_note, date=int(when.timestamp()))


def arrive(monkeypatch, *messages):
    """Each message at its own moment: the capture's clock is the message's."""
    wires = []
    for message in messages:
        moment = datetime.fromtimestamp(message["date"], UTC)
        monkeypatch.setattr(capture, "_now", lambda moment=moment: moment)
        wires.append(send(message))
    return wires[0] if len(wires) == 1 else wires


@pytest.fixture
def hears(monkeypatch):
    """The hearing, as the simulator's: what the note says is set by the test."""
    heard = {"words": ""}

    async def hear(audio, *, paid=None, local_only=False, prompt=voice.FOOD_PROMPT):
        return voice.Heard(heard["words"], voice.LOCAL, "test")

    monkeypatch.setattr(voice, "hear", hear)
    return heard


def _meals(db) -> list[Meal]:
    """The meals a reader sees: a joined row is merged and gone."""
    db.expire_all()
    stmt = select(Meal).where(Meal.deleted_at.is_(None)).order_by(Meal.id)
    return list(db.scalars(stmt))


def _send_row(db, send_id: int) -> ReminderSend:
    db.expire_all()
    return db.get(ReminderSend, send_id)


def _local(meal: Meal) -> datetime:
    return _utc(meal.meal_time).astimezone(ZONE)


def _is_day_only(meal: Meal) -> None:
    assert (meal.time_precision, meal.time_source) == ("day_only", "reminder")
    assert _local(meal) == datetime.combine(DAY, time(12), tzinfo=ZONE)


def _is_sent(meal: Meal, moment: datetime) -> None:
    assert (meal.time_precision, meal.time_source) == ("exact", "sent")
    assert _utc(meal.meal_time) == moment


# --- the binding --------------------------------------------------------------------


def test_soup_ten_minutes_after_is_the_reminders_day_without_a_clock(
    db, evening, monkeypatch
):
    wire = arrive(monkeypatch, say(at(21, 10), "суп"))
    (meal,) = _meals(db)
    _is_day_only(meal)
    assert wire.sent()[0]["text"].startswith(DAY_LINE + ": суп"), wire.sent()
    row = _send_row(db, evening.id)
    assert row.answer == "words" and _utc(row.answered_at) == at(21, 10)
    assert row.closed_at is None  # more may come in the window
    assert reminders.outcome(row) == reminders.ANSWERED


def test_a_said_clock_wins_over_the_reminder(db, evening, monkeypatch):
    arrive(monkeypatch, say(at(21, 10), "в 13 суп"))
    (meal,) = _meals(db)
    assert (meal.time_precision, meal.time_source) == ("exact", "said")
    assert _local(meal).time() == time(13)
    assert _send_row(db, evening.id).answer == "words"


def test_a_voice_morning_porridge_is_about_nine(db, evening, hears, monkeypatch):
    hears["words"] = "утром каша"
    arrive(monkeypatch, speak(at(21, 5)))
    (meal,) = _meals(db)
    assert (meal.time_precision, meal.time_source) == ("approx", "said")
    assert _local(meal) == datetime.combine(DAY, time(9), tzinfo=ZONE)
    assert _send_row(db, evening.id).answer == "words"


def test_a_photo_is_food_in_front_of_the_person_now(db, evening, monkeypatch):
    arrive(monkeypatch, snap(at(21, 10)))
    (meal,) = _meals(db)
    _is_sent(meal, at(21, 10))
    assert _send_row(db, evening.id).answer is None


def test_a_photo_in_reply_to_the_reminder_is_bound(db, evening, monkeypatch):
    arrive(monkeypatch, snap(at(21, 10), reply_to_message=_reminder_message()))
    (meal,) = _meals(db)
    _is_day_only(meal)


def test_yoghurt_at_forty_minutes_with_no_answer_before_is_its_own_time(
    db, evening, monkeypatch
):
    arrive(monkeypatch, say(at(21, 40), "йогурт"))
    (meal,) = _meals(db)
    _is_sent(meal, at(21, 40))
    assert _send_row(db, evening.id).answer is None


def test_an_answer_renews_the_window(db, evening, monkeypatch):
    arrive(monkeypatch, say(at(21, 10), "суп"), say(at(21, 35), "хлеб"))
    soup, bread = _meals(db)
    _is_day_only(soup)
    _is_day_only(bread)  # 21:35 is past 30 minutes, inside the renewed window
    assert _utc(_send_row(db, evening.id).answered_at) == at(21, 35)


def test_the_window_is_never_longer_than_ninety_minutes(db, evening, monkeypatch):
    arrive(
        monkeypatch,
        say(at(21, 10), "суп"),
        say(at(21, 35), "хлеб"),
        say(at(22, 0), "сыр"),
        say(at(22, 20), "яблоко"),
        say(at(22, 35), "чай"),
    )
    *bound, tea = _meals(db)
    for meal in bound:  # the last at +80, inside the cap
        _is_day_only(meal)
    _is_sent(tea, at(22, 35))  # +95: answered at +80, still past the cap
    assert reminders.window_end(_send_row(db, evening.id)) == at(22, 30)


def test_food_eaten_now_keeps_the_sent_time(db, evening, monkeypatch):
    arrive(monkeypatch, say(at(21, 5), "ем сейчас пасту"))
    (meal,) = _meals(db)
    _is_sent(meal, at(21, 5))
    assert _send_row(db, evening.id).answer is None


def test_a_voice_of_food_eaten_now_keeps_the_sent_time(db, evening, hears, monkeypatch):
    hears["words"] = "только что съела пасту"
    arrive(monkeypatch, speak(at(21, 5)))
    (meal,) = _meals(db)
    _is_sent(meal, at(21, 5))
    row = _send_row(db, evening.id)  # bound at birth, taken back by its words
    assert row.answer is None and row.answered_at is None
    assert reminders.window_end(row) == at(21, 30)


def test_a_voice_of_food_eaten_now_keeps_an_earlier_answer(
    db, evening, hears, monkeypatch
):
    hears["words"] = "только что съела пасту"
    arrive(monkeypatch, say(at(21, 5), "суп"), speak(at(21, 20)))
    soup, pasta = _meals(db)
    _is_day_only(soup)
    _is_sent(pasta, at(21, 20))
    row = _send_row(db, evening.id)
    assert row.answer == "words" and _utc(row.answered_at) == at(21, 5)


def test_right_now_keeps_the_sent_time(db, evening, monkeypatch):
    arrive(monkeypatch, say(at(21, 5), "прямо сейчас ем суп"))
    (meal,) = _meals(db)
    _is_sent(meal, at(21, 5))
    assert _send_row(db, evening.id).answer is None


@pytest.mark.parametrize("words", ["нет, не ел", "70.5", "12:30", "Витамин"])
def test_never_bound_inside_the_window(db, evening, reader, words, monkeypatch):
    """§3.2: «нет», a weight, a bare clock and a routine name keep their own routes."""
    routine = {"id": "r1", "name": "Витамин", "schedule": routines.DAILY, "icon": "💊"}
    routines._save(db, OWNER, [routine])
    wire = arrive(monkeypatch, say(at(21, 5), words))
    assert reader.text == [] and _meals(db) == []
    row = _send_row(db, evening.id)
    assert row.answer is None and row.answered_at is None
    if words == "Витамин":  # a tick, not a dish nor an answer
        assert [m["text"] for m in wire.sent()] == ["✓ Витамин"], wire.sent()


def test_a_reply_three_hours_later_is_still_the_reminders_day(db, evening, monkeypatch):
    arrive(monkeypatch, say(at(0, 5, DAY + timedelta(days=1)), "суп", **_reply()))
    (meal,) = _meals(db)
    _is_day_only(meal)
    assert _send_row(db, evening.id).answer == "words"


@pytest.mark.parametrize(
    ("hour", "words", "clock", "precision"),
    [(10, "утром каша", time(9), "approx"), (14, "в 13 суп", time(13), "exact")],
)
def test_a_time_in_a_reply_the_next_day_is_of_the_reminders_day(
    db, evening, hour, words, clock, precision, monkeypatch
):
    arrive(monkeypatch, say(at(hour, 0, DAY + timedelta(days=1)), words, **_reply()))
    (meal,) = _meals(db)
    assert (meal.time_precision, meal.time_source) == (precision, "said")
    assert _local(meal) == datetime.combine(DAY, clock, tzinfo=ZONE)


def test_a_voice_reply_the_next_day_is_of_the_reminders_day(
    db, evening, hears, monkeypatch
):
    hears["words"] = "утром каша"
    voice_note = speak(at(10, 0, DAY + timedelta(days=1)))
    arrive(monkeypatch, {**voice_note, **_reply()})
    (meal,) = _meals(db)
    assert (meal.time_precision, meal.time_source) == ("approx", "said")
    assert _local(meal) == datetime.combine(DAY, time(9), tzinfo=ZONE)


def test_yesterday_in_a_reply_the_next_day_is_still_yesterday(db, evening, monkeypatch):
    """A day the words name is theirs: «вчера» on the 7th is the 6th."""
    arrive(
        monkeypatch,
        say(at(10, 0, DAY + timedelta(days=1)), "вчера в 13 суп", **_reply()),
    )
    (meal,) = _meals(db)
    assert _local(meal) == datetime.combine(DAY, time(13), tzinfo=ZONE)


def test_an_album_caption_in_reply_times_the_bound_meal(db, evening, monkeypatch):
    """§3.2: the album's caption re-times a meal the reminder dated (capture._join_album)."""
    monkeypatch.setattr(capture, "ALBUM_WAIT_SECONDS", 0.05)
    reply = {"reply_to_message": _reminder_message(), "media_group_id": "a1"}
    arrive(
        monkeypatch,
        snap(at(21, 10), **reply),
        snap(at(21, 10), caption="в 13", **reply),
    )
    (meal,) = _meals(db)
    assert (meal.time_precision, meal.time_source) == ("exact", "said")
    assert _local(meal) == datetime.combine(DAY, time(13), tzinfo=ZONE)
    assert len(meal.photos) == 2


def test_a_reply_three_days_later_is_not_bound(db, evening, monkeypatch):
    late = at(10, 0, DAY + timedelta(days=3))
    arrive(monkeypatch, say(late, "суп", **_reply()))
    (meal,) = _meals(db)
    _is_sent(meal, late)


def test_a_forward_is_never_bound(db, evening, monkeypatch):
    forwarded = int(at(13).timestamp())
    origin = {"type": "hidden_user", "sender_user_name": "x", "date": forwarded}
    arrive(monkeypatch, say(at(21, 10), "суп", forward_origin=origin))
    (meal,) = _meals(db)
    assert meal.time_source == "forward"
    assert _send_row(db, evening.id).answer is None


def test_a_closed_reminder_binds_nothing(db, evening, monkeypatch):
    evening.answer, evening.closed_at = "ok", at(21, 2)
    db.commit()
    arrive(monkeypatch, say(at(21, 10), "суп"))
    (meal,) = _meals(db)
    _is_sent(meal, at(21, 10))


def test_an_older_meal_never_joins_through_the_reminder(db, evening, monkeypatch):
    arrive(monkeypatch, say(at(19, 50), "гречка"), say(at(21, 10), "суп"))
    buckwheat, soup = _meals(db)
    _is_sent(buckwheat, at(19, 50))
    _is_day_only(soup)


# --- the corrective words inside a window -------------------------------------------


def test_forgot_yoghurt_is_a_new_meal_not_the_one_at_two(db, evening, monkeypatch):
    """F1: the last record at 14:00; «забыла: йогурт» at 21:02."""
    arrive(monkeypatch, say(at(14), "гречка"), say(at(21, 2), "забыла: йогурт"))
    lunch, yoghurt = _meals(db)
    _is_day_only(yoghurt)
    assert _item_names(db, lunch) == ["рис", "курица", "овощи"]  # the fake's read


def test_and_also_yoghurt_in_the_day_is_a_second_meal_at_about_two(
    db, evening, reader, monkeypatch
):
    """F4: 21:05 «суп»; 21:08 «а ещё днём был йогурт»."""
    wires = arrive(
        monkeypatch, say(at(21, 5), "суп"), say(at(21, 8), "а ещё днём был йогурт")
    )
    soup, yoghurt = _meals(db)
    _is_day_only(soup)
    assert (yoghurt.time_precision, yoghurt.time_source) == ("approx", "said")
    assert _local(yoghurt) == datetime.combine(DAY, time(14), tzinfo=ZONE)
    assert reader.text == ["суп", "йогурт"]  # the verb is no dish
    line = wires[1].sent()[0]["text"]
    assert line.startswith("Записано на пт 6 ноября, ≈ 14:00: йогурт."), line
    assert _utc(_send_row(db, evening.id).answered_at) == at(21, 8)


def test_and_also_now_an_apple_is_its_own_meal_at_its_sent_time(
    db, evening, reader, monkeypatch
):
    """«сейчас» is never bound, and never a dish of the recalled answer either."""
    arrive(monkeypatch, say(at(21, 5), "суп"), say(at(21, 8), "а ещё сейчас яблоко"))
    soup, apple = _meals(db)
    _is_day_only(soup)
    assert apple.time_precision == "exact" and _utc(apple.meal_time) == at(21, 8)
    assert apple.time_source != reminders.SOURCE
    assert reader.text == ["суп", "сейчас яблоко"]  # read as «ем сейчас пасту» is
    assert _item_names(db, soup) == ["рис", "курица", "овощи"]  # no apple in it
    assert _utc(_send_row(db, evening.id).answered_at) == at(21, 5)


def test_and_also_bread_joins_the_first_answer(db, evening, reader, monkeypatch):
    arrive(monkeypatch, say(at(21, 5), "суп"), say(at(21, 8), "а ещё хлеб"))
    (soup,) = _meals(db)
    _is_day_only(soup)


def test_and_also_with_a_time_outside_a_window_joins_as_before(
    db, reader, owner, monkeypatch
):
    """The control: no reminder, so the old rule of row 8 stands."""
    arrive(monkeypatch, say(at(21, 5), "суп"), say(at(21, 8), "а ещё днём был йогурт"))
    assert len(_meals(db)) == 1


# --- «нет» and «ничего» (R16) ---------------------------------------------------------


def test_no_is_silent_and_leaves_the_reminder_unanswered(db, evening, monkeypatch):
    """F2."""
    wire = arrive(monkeypatch, say(at(21, 1), "нет"))
    assert wire.sent() == [] and wire.edits() == []
    assert _meals(db) == []
    row = _send_row(db, evening.id)
    assert row.answer is None and reminders.outcome(row) == reminders.UNANSWERED_SEND


@pytest.mark.parametrize(
    "words",
    [
        "почти ничего не ела",
        "Ничего",
        "больше ничего",
        "не ел",
        "не было",
        "сегодня ничего.",
        "вообще  ничего не ели!",
        "нет, ничего",
        "Нет ничего",
        "ничего, спасибо",
        "нет, больше ничего, спасибо.",
    ],
)
def test_nothing_answers_and_closes_with_no_read(
    db, evening, reader, words, monkeypatch
):
    """F3: no read, no line; the reminder says «✓ Принято.» with no buttons."""
    wire = arrive(monkeypatch, say(at(21, 10), words))
    assert reader.text == [] and reader.image == []
    assert _meals(db) == []
    assert wire.sent() == []
    (edit,) = wire.edits()
    assert edit["message_id"] == REMINDER
    assert edit["text"] == "🔔 <b>Еда за сегодня</b>\n✓ Принято."
    assert "reply_markup" not in edit or not _kb(edit)
    row = _send_row(db, evening.id)
    assert row.answer == "none"
    assert _utc(row.answered_at) == at(21, 10) and _utc(row.closed_at) == at(21, 10)
    assert reminders.outcome(row) == reminders.ANSWERED
    # what came is kept, as every message is
    assert len(list(db.scalars(select(MealSource)))) == 1


def test_no_nothing_after_soup_closes_it_and_leaves_the_soup(
    db, evening, reader, monkeypatch
):
    """«нет, ничего» is R16, not a correction «это было …» of the soup."""
    wires = arrive(monkeypatch, say(at(21, 5), "суп"), say(at(21, 8), "нет, ничего"))
    (soup,) = _meals(db)
    _is_day_only(soup)
    assert _item_names(db, soup) == ["рис", "курица", "овощи"]  # the fake's read
    assert reader.text == ["суп"]
    assert wires[1].sent() == []
    (edit,) = wires[1].edits()
    assert edit["message_id"] == REMINDER
    assert edit["text"] == "🔔 <b>Еда за сегодня</b>\n✓ Принято."
    row = _send_row(db, evening.id)
    assert row.answer == "none" and _utc(row.closed_at) == at(21, 8)


def test_nothing_outside_a_window_takes_the_usual_way(db, owner, reader, monkeypatch):
    """The control: the same words with no reminder are read as any text."""
    wire = arrive(monkeypatch, say(at(21, 10), "почти ничего не ела"))
    assert reader.text == ["почти ничего не ела"]
    assert wire.edits() == [] or all(e["message_id"] != REMINDER for e in wire.edits())


def test_nothing_after_the_window_takes_the_usual_way(db, evening, reader, monkeypatch):
    arrive(monkeypatch, say(at(21, 45), "ничего"))
    assert reader.text == ["ничего"]
    assert _send_row(db, evening.id).answer is None


def test_nothing_after_a_star_keeps_the_stars_lines(
    db, evening, starred, reader, monkeypatch
):
    """R16 after a ⭐: its ✓ line, ✅ ОК first and ↩️ stay, «✓ Принято.» under them."""
    wire = arrive(monkeypatch, say(at(21, 10), "больше ничего"))
    assert reader.text == [] and wire.sent() == []
    _accepted_under_the_star(wire)
    assert _item_names(db, _meal(db, starred)) == ["Гречка"]
    row = _send_row(db, evening.id)
    assert row.answer == reminders.FAV_NONE and _utc(row.closed_at) == at(21, 10)
    assert reminders.outcome(row) == reminders.ANSWERED
    assert reminders.holds_star(row)  # the next send keeps the message (R4)


def test_undo_of_the_star_after_nothing_keeps_the_nothing(
    db, evening, starred, reader, monkeypatch
):
    """↩️ on the ⭐ after «больше ничего»: still answered, and R16's own message."""
    arrive(monkeypatch, say(at(21, 10), "больше ничего"))
    data = f"rm:u:{evening.id}:{starred}"
    screen = reminders.handle_callback(
        db, OWNER, data, now=at(21, 12), message_id=REMINDER
    )
    assert screen.text == "🔔 <b>Еда за сегодня</b>\n✓ Принято."
    assert screen.markup is None
    row = _send_row(db, evening.id)
    assert row.answer == "none" and reminders.outcome(row) == reminders.ANSWERED
    assert _meal(db, starred).deleted_at is not None


def test_a_stale_chip_after_nothing_keeps_it_accepted(
    db, evening, starred, reader, monkeypatch
):
    """A chip from before «ничего» still logs; «✓ Принято.» stays, no chip comes back."""
    arrive(monkeypatch, say(at(21, 10), "больше ничего"))
    fav_id = _meal(db, starred).resolution_signals["favorite_id"]
    data = f"rm:f:{evening.id}:{fav_id}"
    screen = reminders.handle_callback(
        db, OWNER, data, now=at(21, 20), message_id=REMINDER
    )
    lines = screen.text.split("\n")
    assert lines[-1] == "✓ Принято." and len(lines) == 4, lines
    callbacks = [b.callback_data for row in screen.markup.inline_keyboard for b in row]
    assert not any(c.startswith("rm:f:") for c in callbacks), callbacks
    assert _send_row(db, evening.id).answer == reminders.FAV_NONE


def test_nothing_in_reply_after_a_star_is_no_dish_of_it(
    db, evening, starred, reader, monkeypatch
):
    """The reminder's message holds the ⭐ meal; «ничего» in reply is still R16."""
    wire = arrive(monkeypatch, say(at(21, 10), "почти ничего не ела", **_reply()))
    assert reader.text == [] and reader.image == [] and wire.sent() == []
    _accepted_under_the_star(wire)
    assert _item_names(db, _meal(db, starred)) == ["Гречка"]
    assert _new_meals(db, starred) == []
    row = _send_row(db, evening.id)
    assert row.answer == reminders.FAV_NONE and _utc(row.closed_at) == at(21, 10)


def test_soup_in_reply_after_a_star_is_its_own_meal(
    db, evening, starred, reader, monkeypatch
):
    """§3.2(a): food in reply is a meal of its own on that day, not a dish of the ⭐."""
    wire = arrive(monkeypatch, say(at(21, 10), "суп", **_reply()))
    assert reader.text == ["суп"]
    assert _item_names(db, _meal(db, starred)) == ["Гречка"]
    (soup,) = _new_meals(db, starred)
    _is_day_only(soup)
    assert wire.sent()[0]["text"].startswith(DAY_LINE + ": суп"), wire.sent()
    row = _send_row(db, evening.id)
    assert row.answer == "fav" and _utc(row.answered_at) == at(21, 10)


def test_a_bare_time_in_reply_after_a_star_times_the_star(
    db, evening, starred, reader, monkeypatch
):
    arrive(monkeypatch, say(at(21, 10), "в 13", **_reply()))
    star = _meal(db, starred)
    assert (star.time_precision, _local(star).time()) == ("exact", time(13))
    assert _new_meals(db, starred) == [] and reader.text == []


def test_a_photo_in_reply_after_a_star_is_its_own_meal(
    db, evening, starred, reader, monkeypatch
):
    arrive(monkeypatch, snap(at(21, 10), reply_to_message=_reminder_message()))
    (plate,) = _new_meals(db, starred)
    _is_day_only(plate)
    star = _meal(db, starred)
    assert _item_names(db, star) == ["Гречка"] and not star.photos


def test_nothing_in_reply_to_a_reminder_turned_off_leaves_it_as_it_is(
    db, evening, reader, monkeypatch
):
    """✖ took it: its ↩️ Вернуть stays, and nothing says the reminders are on."""
    evening.answer, evening.closed_at = "off", at(21, 2)
    db.commit()
    wire = arrive(monkeypatch, say(at(21, 10), "ничего", **_reply()))
    assert wire.edits() == [] and wire.sent() == [] and reader.text == []
    row = _send_row(db, evening.id)
    assert row.answer == "off" and _utc(row.closed_at) == at(21, 2)


def test_food_in_reply_to_a_reminder_turned_off_keeps_it_off(
    db, evening, reader, monkeypatch
):
    """As «ничего» there: ✖'s answer stands, so its ↩️ Вернуть still restores."""
    evening.answer, evening.closed_at = "off", at(21, 2)
    db.commit()
    arrive(monkeypatch, say(at(21, 10), "суп", **_reply()))
    (soup,) = _meals(db)
    _is_day_only(soup)
    assert _send_row(db, evening.id).answer == "off"


def test_nothing_with_food_in_it_is_food(db, evening, reader, monkeypatch):
    arrive(monkeypatch, say(at(21, 10), "ничего кроме супа"))
    assert reader.text == ["ничего кроме супа"]
    (meal,) = _meals(db)
    _is_day_only(meal)


def test_nothing_costs_no_model_call(db, evening, monkeypatch):
    """The guard sits on the read's own entries, so the control goes the same way."""
    calls = []

    async def refuse_text(*, kind, payload, input_ref, telegram_id):
        calls.append(payload)
        raise AssertionError("a model call")

    async def refuse_image(payload, **kwargs):
        calls.append("photo")
        raise AssertionError("a model call")

    monkeypatch.setattr(tg, "_analyze_and_parse", refuse_text)
    monkeypatch.setattr(tg, "resolve_meal_nutrition", refuse_image)
    arrive(monkeypatch, say(at(21, 10), "почти ничего не ела"))
    assert calls == []
    # the control: a text that is read, in the same window, reaches the guard
    arrive(monkeypatch, say(at(21, 15), "суп"))
    assert calls == ["суп"]


# --- the acceptance scenarios D1–D3 -------------------------------------------------


def test_d1_voice_then_text_then_photo(db, evening, hears, monkeypatch):
    hears["words"] = "утром каша"
    wires = arrive(
        monkeypatch, speak(at(21, 5)), say(at(21, 12), "суп"), snap(at(21, 20))
    )
    porridge, soup, plate = _meals(db)
    assert (porridge.time_precision, porridge.time_source) == ("approx", "said")
    assert _local(porridge).time() == time(9)
    _is_day_only(soup)
    assert wires[1].sent()[0]["text"].startswith(DAY_LINE + ": суп")
    _is_sent(plate, at(21, 20))


def test_d2_a_day_told_in_one_voice_is_one_meal_with_no_clock(
    db, evening, hears, reader, monkeypatch
):
    # «днём», not the spec's «в обед»: meal_words knows no «в обед» (left as it is)
    hears["words"] = "утром овсянка, днём суп"
    wire = arrive(monkeypatch, speak(at(21, 5)))
    (meal,) = _meals(db)
    # Two times in one message: meal_words' own rule says the day and no clock.
    assert meal.time_precision == "day_only"
    assert _local(meal) == datetime.combine(DAY, time(12), tzinfo=ZONE)
    assert reader.text == ["утром овсянка, днём суп"]  # nothing cut from the read
    # the voice's line: «слушаю…» first, then edited to what the read made
    line = wire.edits()[-1]["text"]
    assert line.startswith(DAY_LINE), line
    labels = [b.text for row in _time_rows(db, meal) for b in row]
    assert {"Утром", "Днём", "Вечером"} <= set(labels), labels


def test_d3_yoghurt_at_twenty_one_forty_is_exact(db, evening, monkeypatch):
    arrive(monkeypatch, say(at(21, 40), "йогурт"))
    (meal,) = _meals(db)
    _is_sent(meal, at(21, 40))


def test_d3_at_twenty_one_twenty_the_clock_chip_makes_it_exact(
    db, evening, monkeypatch
):
    arrive(monkeypatch, say(at(21, 20), "йогурт"))
    (meal,) = _meals(db)
    _is_day_only(meal)
    chip = next(b for row in _time_rows(db, meal) for b in row if b.text == "В 21:20")
    meal_card.handle_callback(db, OWNER, chip.callback_data, now=at(21, 21))
    (meal,) = _meals(db)
    assert meal.time_precision == "exact"
    assert _utc(meal.meal_time) == at(21, 20)


# --- R1b review: a day the words name, a correction's renewal, a voice «ничего» -----


SATURDAY = DAY + timedelta(days=1)


def test_yesterday_and_evening_in_a_reply_the_next_day_is_the_reminders_day(
    db, evening, monkeypatch
):
    """«вчера» on Saturday names Friday: no re-anchor to the reminder's day (Thursday)."""
    words = "вчера утром каша, вечером суп"
    arrive(monkeypatch, say(at(10, 0, SATURDAY), words, **_reply()))
    (meal,) = _meals(db)
    assert meal.time_precision == "day_only"
    assert _local(meal) == datetime.combine(DAY, time(12), tzinfo=ZONE)


def test_today_in_a_reply_the_next_day_is_that_day(db, evening, monkeypatch):
    """«сегодня утром» on Saturday at 10:00 is Saturday ≈ 9:00, as before R0."""
    arrive(monkeypatch, say(at(10, 0, SATURDAY), "сегодня утром каша", **_reply()))
    (meal,) = _meals(db)
    assert (meal.time_precision, meal.time_source) == ("approx", "said")
    assert _local(meal) == datetime.combine(SATURDAY, time(9), tzinfo=ZONE)


def test_today_in_a_voice_reply_the_next_day_is_that_day(
    db, evening, hears, monkeypatch
):
    hears["words"] = "сегодня утром каша"
    voice_note = speak(at(10, 0, SATURDAY))
    arrive(monkeypatch, {**voice_note, **_reply()})
    (meal,) = _meals(db)
    assert (meal.time_precision, meal.time_source) == ("approx", "said")
    assert _local(meal) == datetime.combine(SATURDAY, time(9), tzinfo=ZONE)


def test_and_also_bread_renews_the_window(db, evening, reader, monkeypatch):
    """§3.2: a correction inside the window is bound too, and renews it."""
    arrive(
        monkeypatch,
        say(at(21, 5), "суп"),
        say(at(21, 30), "а ещё хлеб"),
    )
    (soup,) = _meals(db)
    _is_day_only(soup)
    assert _utc(_send_row(db, evening.id).answered_at) == at(21, 30)
    arrive(monkeypatch, say(at(21, 45), "йогурт"))
    soup, yoghurt = _meals(db)
    _is_day_only(yoghurt)  # +45, inside the window «а ещё хлеб» renewed
    assert _utc(_send_row(db, evening.id).answered_at) == at(21, 45)


SOUP_LINE = 5001  # the first line the wire sends (Wire.next_id + 1)


@pytest.mark.parametrize("words", ["а ещё хлеб", "хлеб"])
def test_a_reply_to_the_soup_line_renews_the_window(
    db, evening, reader, words, monkeypatch
):
    """§3.2: words in reply to an answered meal's line change it, are bound, and run
    the window on: a correction and a plain addition alike."""
    arrive(monkeypatch, say(at(21, 5), "суп"))
    line = {
        "message_id": SOUP_LINE,
        "date": int(at(21, 5).timestamp()),
        "chat": {"id": CHAT, "type": "private"},
        "from": {"id": 1, "is_bot": True, "first_name": "bot", "username": "b"},
        "text": "суп",
    }
    arrive(monkeypatch, say(at(21, 30), words, reply_to_message=line))
    (soup,) = _meals(db)  # the reply joined the soup
    _is_day_only(soup)
    assert "хлеб" in reader.text, reader.text
    assert _utc(_send_row(db, evening.id).answered_at) == at(21, 30)
    arrive(monkeypatch, say(at(21, 45), "йогурт"))
    soup, yoghurt = _meals(db)
    _is_day_only(yoghurt)  # +40 from the reply: inside the window it renewed


def test_nothing_after_a_renewing_correction_is_r16(db, evening, reader, monkeypatch):
    wires = arrive(
        monkeypatch,
        say(at(21, 5), "суп"),
        say(at(21, 30), "а ещё хлеб"),
        say(at(21, 45), "йогурт"),
        say(at(21, 50), "ничего"),
    )
    assert reader.text == ["суп", "хлеб", "йогурт"]  # «ничего» is never read
    assert wires[-1].sent() == []
    (edit,) = wires[-1].edits()
    assert edit["message_id"] == REMINDER
    assert edit["text"] == "🔔 <b>Еда за сегодня</b>\n✓ Принято."
    row = _send_row(db, evening.id)
    assert row.answer == "none" and _utc(row.closed_at) == at(21, 50)


@pytest.mark.parametrize("words", ["почти ничего не ела", "ничего"])
def test_a_voice_of_nothing_is_not_read_and_closes_the_reminder(
    db, evening, hears, reader, words, monkeypatch
):
    """R16 for a voice: heard, never read; no meal in the day, «✓ Принято.»."""
    hears["words"] = words
    wire = arrive(monkeypatch, speak(at(21, 5)))
    assert reader.text == [] and reader.image == []
    assert _meals(db) == []
    edits = {edit["message_id"]: edit for edit in wire.edits()}
    assert edits[REMINDER]["text"] == "🔔 <b>Еда за сегодня</b>\n✓ Принято."
    assert "reply_markup" not in edits[REMINDER] or not _kb(edits[REMINDER])
    (line,) = [edit for mid, edit in edits.items() if mid != REMINDER]
    assert line["text"] == reminders.ACCEPTED, line  # no longer «слушаю…»
    assert "reply_markup" not in line or not _kb(line)  # no ✏️ to a «ничего»
    row = _send_row(db, evening.id)
    assert row.answer == "none"
    assert _utc(row.answered_at) == at(21, 5) and _utc(row.closed_at) == at(21, 5)
    assert reminders.outcome(row) == reminders.ANSWERED


def test_a_voice_of_food_under_the_reminder_is_still_read(
    db, evening, hears, reader, monkeypatch
):
    """The control of the one above: a food transcript goes to the read."""
    hears["words"] = "суп"
    wire = arrive(monkeypatch, speak(at(21, 5)))
    assert reader.text == ["суп"]
    (meal,) = _meals(db)
    _is_day_only(meal)
    assert all(edit["message_id"] != REMINDER for edit in wire.edits())
    assert _send_row(db, evening.id).answer == "words"


# --- helpers --------------------------------------------------------------------------


def _reminder_message() -> dict:
    return {
        "message_id": REMINDER,
        "date": int(at(21).timestamp()),
        "chat": {"id": CHAT, "type": "private"},
        "from": {"id": 1, "is_bot": True, "first_name": "bot", "username": "b"},
        "text": "🔔 Еда за сегодня",
    }


def _reply() -> dict:
    return {"reply_to_message": _reminder_message()}


def _meal(db, meal_id: int) -> Meal:
    db.expire_all()
    return db.get(Meal, meal_id)


def _new_meals(db, star_id: int) -> list[Meal]:
    """The meals after the ⭐ meal: what the answer itself made."""
    return [meal for meal in _meals(db) if meal.id > star_id]


def _accepted_under_the_star(wire) -> None:
    (edit,) = wire.edits()
    assert edit["message_id"] == REMINDER
    lines = edit["text"].split("\n")
    assert lines[0] == "🔔 <b>Еда за сегодня</b>"
    assert lines[1].startswith("✓ Записано на") and "Гречка" in lines[1]
    assert lines[-1] == "✓ Принято."
    markup = edit["reply_markup"]
    markup = json.loads(markup) if isinstance(markup, str) else markup
    labels = [[b["text"] for b in row] for row in markup["inline_keyboard"]]
    assert labels[0] == ["✅ ОК"], labels
    assert "↩️ Отменить" in labels[1], labels
    assert len(labels) == 2, labels  # no chip under «✓ Принято.» (R16)
    callbacks = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert not any(c.startswith("rm:f:") for c in callbacks), callbacks


def _time_rows(db, meal: Meal) -> list:
    screen = meal_card.handle_callback(db, OWNER, f"ml:t:{meal.id}:r", now=at(21, 21))
    return screen.markup.inline_keyboard


# --- re-entry binds nothing (D6, §9 L2) ---------------------------------------------


@pytest.fixture
def reentry(db, owner, reader):
    """Re-entry #1 of DAY, sent at 21:00 as the sweep sends it: no recall window."""
    row = crud_reminder_send.claim(
        db,
        user_id=owner.id,
        local_day=DAY,
        slot=reminders.REENTRY_FIRST,
        kind=reminders.REENTRY,
        chat_id=CHAT,
        now=at(21),
    )
    row.status = "sent"
    row.message_id = REMINDER
    db.commit()
    return row


def test_l2_soup_ten_minutes_after_a_reentry_is_eaten_now(db, reentry, monkeypatch):
    """§9 L2: an exact-time record at its arrival, no day_only; the send stays
    neutral."""
    wire = arrive(monkeypatch, say(at(21, 10), "суп"))
    (meal,) = _meals(db)
    _is_sent(meal, at(21, 10))
    assert DAY_LINE not in wire.sent()[0]["text"]
    row = _send_row(db, reentry.id)
    assert (row.answer, row.answered_at, row.closed_at) == (None, None, None)
    assert reminders.outcome(row) == reminders.NEUTRAL


def test_a_reply_to_a_reentry_is_an_ordinary_capture(db, reentry, monkeypatch):
    arrive(monkeypatch, say(at(21, 10), "суп", reply_to_message=_reminder_message()))
    (meal,) = _meals(db)
    _is_sent(meal, at(21, 10))
    assert _send_row(db, reentry.id).answer is None


def test_capture_finds_no_reminder_behind_a_reentry(db, reentry):
    """The seams capture asks (bound_send, answering, replied_send): a reply to
    the re-entry and a text inside what would be its window both find none."""
    person = db.get(User, reentry.user_id)
    for reply_to in (REMINDER, None):
        came = SimpleNamespace(
            kind="text",
            chat_id=CHAT,
            text="суп",
            sent_at=at(21, 10),
            reply_to=reply_to,
            forward_date=None,
        )
        assert reminders.bound_send(db, person, came) is None
        assert reminders.answering(db, person, came) is None
    assert reminders.replied_send(db, person, CHAT, REMINDER, at(21, 10)) is None
