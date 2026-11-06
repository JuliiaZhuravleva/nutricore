"""§3.2 and R16: what a voice or a text after a 🔔 reminder is bound to, through the
real application at the wire (stage 6, slice R1b).

Every message is a real ``Update`` into ``create_bot_application()``; Telegram, the
model and the hearing are fakes (``tests/test_capture_router.py``). No network, no
paid call. The reminder went out on Friday 6 November at 21:00 in Tbilisi (UTC+4).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select

from app.crud.crud_reminder_send import crud_reminder_send
from app.models import Meal, MealSource
from app.models.reminder_send import ReminderSend
from app.services import capture, meal_card, openai_service, reminders, voice
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


def test_a_reply_three_hours_later_is_still_the_reminders_day(db, evening, monkeypatch):
    arrive(monkeypatch, say(at(0, 5, DAY + timedelta(days=1)), "суп", **_reply()))
    (meal,) = _meals(db)
    _is_day_only(meal)
    assert _send_row(db, evening.id).answer == "words"


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
    arrive(monkeypatch, say(at(21, 5), "суп"), say(at(21, 8), "а ещё днём был йогурт"))
    soup, yoghurt = _meals(db)
    _is_day_only(soup)
    assert (yoghurt.time_precision, yoghurt.time_source) == ("approx", "said")
    assert _local(yoghurt) == datetime.combine(DAY, time(14), tzinfo=ZONE)
    assert reader.text == ["суп", "был йогурт"]
    assert _utc(_send_row(db, evening.id).answered_at) == at(21, 8)


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


def test_nothing_outside_a_window_takes_the_usual_way(db, owner, reader, monkeypatch):
    """The control: the same words with no reminder are read as any text."""
    wire = arrive(monkeypatch, say(at(21, 10), "почти ничего не ела"))
    assert reader.text == ["почти ничего не ела"]
    assert wire.edits() == [] or all(e["message_id"] != REMINDER for e in wire.edits())


def test_nothing_after_the_window_takes_the_usual_way(db, evening, reader, monkeypatch):
    arrive(monkeypatch, say(at(21, 45), "ничего"))
    assert reader.text == ["ничего"]
    assert _send_row(db, evening.id).answer is None


def test_nothing_after_a_star_keeps_the_stars_lines(db, evening, monkeypatch):
    evening.answer = "fav"
    db.commit()
    wire = arrive(monkeypatch, say(at(21, 10), "больше ничего"))
    assert wire.edits() == []  # the ✓ line of the ⭐ and its ↩️ stay
    row = _send_row(db, evening.id)
    assert row.answer == "fav" and row.closed_at is not None


def test_nothing_with_food_in_it_is_food(db, evening, reader, monkeypatch):
    arrive(monkeypatch, say(at(21, 10), "ничего кроме супа"))
    assert reader.text == ["ничего кроме супа"]
    (meal,) = _meals(db)
    _is_day_only(meal)


def test_nothing_costs_no_model_call(db, evening, monkeypatch):
    calls = []

    def refuse(*args, **kwargs):
        calls.append(args)
        raise AssertionError("a model call")

    monkeypatch.setattr(openai_service.OpenAIService, "__init__", refuse)
    monkeypatch.setattr(httpx.AsyncClient, "send", refuse)
    arrive(monkeypatch, say(at(21, 10), "почти ничего не ела"))
    assert calls == []
    with pytest.raises(AssertionError, match="a model call"):
        openai_service.OpenAIService()  # the control: the guard does catch one


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
    arrive(monkeypatch, speak(at(21, 5)))
    (meal,) = _meals(db)
    # Two times in one message: meal_words' own rule says the day and no clock.
    assert meal.time_precision == "day_only"
    assert _local(meal) == datetime.combine(DAY, time(12), tzinfo=ZONE)
    assert reader.text == ["утром овсянка, днём суп"]  # nothing cut from the read
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


def _time_rows(db, meal: Meal) -> list:
    screen = meal_card.handle_callback(db, OWNER, f"ml:t:{meal.id}:r", now=at(21, 21))
    return screen.markup.inline_keyboard
