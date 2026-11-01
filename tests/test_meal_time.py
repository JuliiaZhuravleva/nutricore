"""Slice N, N5: when a meal was eaten. Chips, typed time, day only and approximate,
on real rows through the real screen function, the typed answer through the handler
the bot registers, and the day and week that are built from the result."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.crud.crud_meal_item import crud_meal_item
from app.models import Meal, RecordEvent
from app.services import day_card, meal_card, meal_items, stats_screen, step_count
from tests.test_meal_card import (  # noqa: F401 (fixtures)
    EATEN,
    Session,
    _ask,
    _button,
    _data,
    _meal,
    _one,
    _rows,
    _send,
    _tap,
    db,
    owner,
    stranger,
)

SATURDAY = datetime(2026, 10, 31, 10, 0, tzinfo=UTC)  # 14:00 local, UTC+4
TIME_TITLE = "<b>🕐 Время приёма</b>"
PLEASE = "Другое время можно написать, например 12:40 или вчера 23:10."
FUTURE = "<i>Это время ещё не наступило. Напиши другое.</i>"
SAME = "<i>Уже так</i>"
UNDO = "↩️ Вернуть"
ZONE = ZoneInfo("Asia/Tbilisi")
CHROME = os.environ.get("CHROME")
RENDER = Path(__file__).resolve().parent.parent / "render"


def _sat(db, data, message_id=7, now=SATURDAY):
    return _tap(db, data, message_id=message_id, now=now)


def _typed(db, text, now=SATURDAY):
    return meal_card.take_typed(db, 42, text, now=now)


def _aware(moment):
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _eaten(db, meal):
    db.refresh(meal)
    return _aware(meal.meal_time)


def _events(db, kind="time"):
    return db.scalars(
        select(RecordEvent).where(RecordEvent.kind == kind).order_by(RecordEvent.id)
    ).all()


def _ts(moment):
    return int(moment.timestamp())


# --- N5 and its results -----------------------------------------------------


def test_n5_is_the_spec_screen_with_absolute_targets(db, owner):
    meal, _item = _one(db, owner)
    screen = _sat(db, f"ml:t:{meal.id}:t")
    assert screen.text == "\n".join(
        [TIME_TITLE, "Записано на: пн 26 октября, 13:20.", PLEASE]
    )
    assert _rows(screen) == [
        ["−15 мин", "−1 ч", "Вчера, 30 окт"],
        ["Не помню"],
        ["⬅️ К приёму"],
    ]
    yesterday = datetime(2026, 10, 30, 13, 20, tzinfo=ZONE)
    noon = datetime(2026, 10, 26, 12, 0, tzinfo=ZONE)
    assert _data(screen) == [
        [
            f"ml:ts:{meal.id}:{_ts(EATEN - timedelta(minutes=15))}:e:t",
            f"ml:ts:{meal.id}:{_ts(EATEN - timedelta(hours=1))}:e:t",
            f"ml:ts:{meal.id}:{_ts(yesterday)}:a:t",
        ],
        [f"ml:ts:{meal.id}:{_ts(noon)}:d:t"],
        [f"ml:o:{meal.id}:t"],
    ]


def test_opening_writes_nothing_but_the_slot(db, owner):
    meal, _item = _one(db, owner)
    rev = meal.rev
    _sat(db, f"ml:t:{meal.id}:t", message_id=55)
    ask = _ask(db, owner)
    assert (ask["kind"], ask["meal_id"], ask["message_id"]) == ("time", meal.id, 55)
    assert ask["back"] == "t"
    db.refresh(meal)
    assert meal.rev == rev
    assert _events(db) == []


@pytest.mark.parametrize(
    "label,clock,minutes",
    [("−15 мин", "13:05", 15), ("−1 ч", "12:20", 60)],
)
def test_n5a_a_chip_on_the_same_day(db, owner, label, clock, minutes):
    meal, _item = _one(db, owner)
    opened = _sat(db, f"ml:t:{meal.id}:t")
    screen = _sat(db, _button(opened, label))
    assert screen.text == "\n".join(
        [
            f"<i>✓ Время: {clock}</i>",
            "",
            TIME_TITLE,
            f"Записано на: пн 26 октября, {clock}.",
            PLEASE,
        ]
    )
    event = _events(db)[-1]
    assert _rows(screen) == [
        ["−15 мин", "−1 ч", "Вчера, 30 окт"],
        ["Не помню", UNDO],
        ["⬅️ К приёму"],
    ]
    assert _button(screen, UNDO) == f"ml:u:{event.id}:t"
    assert _eaten(db, meal) == EATEN - timedelta(minutes=minutes)


def test_what_a_change_writes(db, owner):
    meal, _item = _one(db, owner)
    sent = _aware(meal.sent_at)
    meal.time_source, meal.time_phrase = "said", "в обед"
    rev = meal.rev
    db.commit()
    _sat(db, _button(_sat(db, f"ml:t:{meal.id}:t"), "−15 мин"))
    db.refresh(meal)
    assert meal.time_precision == "exact"
    assert meal.time_source == "edited"
    assert meal.time_phrase is None
    assert meal.rev == rev + 1
    assert _aware(meal.sent_at) == sent
    assert len(_events(db)) == 1
    assert _events(db)[0].meal_id == meal.id


def test_a_moved_meal_keeps_its_sent_line_true(db, owner):
    meal, _item = _one(db, owner)
    _sat(db, _button(_sat(db, f"ml:t:{meal.id}:t"), "−1 ч"))
    card = _sat(db, f"ml:o:{meal.id}:r")
    assert "<i>Отправлено: пн 26 октября, 13:20.</i>" in card.text
    assert "<b>🍽 Приём, пн 26 октября, 12:20</b>" in card.text


def test_an_hour_back_across_midnight_names_the_new_day(db, owner):
    # 0:30 local on the 26th.
    meal, _item = _one(db, owner, meal_time=datetime(2026, 10, 25, 20, 30, tzinfo=UTC))
    screen = _sat(db, _button(_sat(db, f"ml:t:{meal.id}:t"), "−1 ч"))
    assert screen.text.startswith(
        "<i>✓ Время: вс 25 октября, 23:30. Приём теперь в этом дне.</i>\n\n"
    )


def test_n5b_typed_yesterday_lands_on_another_day_and_names_it(db, owner):
    meal, _item = _one(db, owner)
    _sat(db, f"ml:t:{meal.id}:t")
    typed = _typed(db, "вчера 23:10")
    assert typed.screen.text == "\n".join(
        [
            "<i>✓ Время: пт 30 октября, 23:10. Приём теперь в этом дне.</i>",
            "",
            TIME_TITLE,
            "Записано на: пт 30 октября, 23:10.",
            PLEASE,
        ]
    )
    assert _rows(typed.screen)[1] == ["Не помню", UNDO]
    assert _eaten(db, meal) == datetime(2026, 10, 30, 19, 10, tzinfo=UTC)
    db.refresh(meal)
    assert (meal.time_precision, meal.time_source) == ("exact", "edited")


def test_n5d_the_yesterday_chip_is_approximate_and_a_second_tap_is_the_toast(db, owner):
    meal, _item = _one(db, owner)
    chip = _button(_sat(db, f"ml:t:{meal.id}:t"), "Вчера, 30 окт")
    screen = _sat(db, chip)
    assert screen.text == "\n".join(
        [
            "<i>✓ Время примерное: пт 30 октября, ≈ 13:20. Приём теперь в этом дне.</i>",
            "",
            TIME_TITLE,
            "Записано на: пт 30 октября, ≈ 13:20.",
            PLEASE,
        ]
    )
    assert _rows(screen) == [
        ["−15 мин", "−1 ч", "Вчера, 30 окт"],
        ["Не помню", UNDO],
        ["⬅️ К приёму"],
    ]
    db.refresh(meal)
    assert meal.time_precision == "approx"
    # The re-drawn chip is the same target: the second tap changes nothing.
    assert _button(screen, "Вчера, 30 окт") == chip
    again = _sat(db, chip)
    assert (again.text, again.toast) == ("", "Уже так")
    assert len(_events(db)) == 1


def test_a_chip_tapped_twice_keeps_the_slot_the_screen_still_offers_typing(db, owner):
    meal, _item = _one(db, owner)
    chip = _button(_sat(db, f"ml:t:{meal.id}:t"), "−15 мин")
    _sat(db, chip)
    assert _sat(db, chip).toast == "Уже так"
    assert _ask(db, owner)["kind"] == "time"
    assert len(_events(db)) == 1


@pytest.mark.parametrize(
    "now,today_label",
    [
        # 2:30 local on Saturday, still Friday in UTC: yesterday is the 30th.
        (datetime(2026, 10, 30, 22, 30, tzinfo=UTC), "Вчера, 30 окт"),
        # 23:30 local on Friday: yesterday is the 29th.
        (datetime(2026, 10, 30, 19, 30, tzinfo=UTC), "Вчера, 29 окт"),
    ],
)
def test_yesterday_is_counted_from_the_local_date_across_midnight(
    db, owner, now, today_label
):
    meal, _item = _one(db, owner)
    opened = _sat(db, f"ml:t:{meal.id}:t", now=now)
    assert _rows(opened)[0][2] == today_label
    _sat(db, _button(opened, today_label), now=now)
    day = int(today_label.split()[1])
    assert meal_card.meal_day(meal, owner) == date(2026, 10, day)


def test_yesterday_is_today_minus_one_not_the_meals_day_minus_one(db, owner):
    meal, _item = _one(db, owner)  # Monday, opened on Saturday
    screen = _sat(db, _button(_sat(db, f"ml:t:{meal.id}:t"), "Вчера, 30 окт"))
    assert "пт 30 октября" in screen.text
    assert "вс 25 октября" not in screen.text
    assert meal_card.meal_day(meal, owner) == date(2026, 10, 30)


def test_yesterday_keeps_the_clock_and_is_not_minus_24_hours_from_now(db, owner):
    meal, _item = _one(db, owner)
    _sat(db, _button(_sat(db, f"ml:t:{meal.id}:t"), "Вчера, 30 окт"))
    assert _eaten(db, meal).astimezone(ZONE).strftime("%d %H:%M") == "30 13:20"


def test_the_label_and_the_write_agree_in_another_zone(db, owner):
    owner.settings = {"timezone": "America/New_York"}
    db.commit()
    # 21:00 on the 30th in New York, already the 31st in UTC.
    now = datetime(2026, 10, 31, 1, 0, tzinfo=UTC)
    meal, _item = _one(db, owner)
    opened = _sat(db, f"ml:t:{meal.id}:t", now=now)
    assert _rows(opened)[0][2] == "Вчера, 29 окт"
    screen = _sat(db, _button(opened, "Вчера, 29 окт"), now=now)
    assert "чт 29 октября, ≈ 5:20" in screen.text


def test_n5c_not_remembered_keeps_the_day_and_drops_the_clock_chips(db, owner):
    meal, _item = _one(db, owner)
    screen = _sat(db, _button(_sat(db, f"ml:t:{meal.id}:t"), "Не помню"))
    assert screen.text == "\n".join(
        [
            "<i>✓ Время не указано. Приём остаётся в пн 26 октября, без времени.</i>",
            "",
            TIME_TITLE,
            "Записано на: пн 26 октября, без времени.",
            PLEASE,
        ]
    )
    assert _rows(screen) == [["Вчера, 30 окт", UNDO], ["⬅️ К приёму"]]
    db.refresh(meal)
    assert meal.time_precision == "day_only"
    assert meal_card.meal_day(meal, owner) == date(2026, 10, 26)
    # Without a time a meal is held at noon, so every order agrees it is mid-day.
    assert _eaten(db, meal).astimezone(ZONE).strftime("%H:%M") == "12:00"
    # Reopened later, it has no chips to count from and no undo.
    reopened = _sat(db, f"ml:t:{meal.id}:t")
    assert _rows(reopened) == [["Вчера, 30 окт"], ["⬅️ К приёму"]]
    assert "Записано на: пн 26 октября, без времени." in reopened.text


def test_not_remembered_on_today_is_not_refused_as_a_future_time(db, owner):
    # 9:00 local: noon is still ahead, and no clock is being claimed.
    now = datetime(2026, 10, 31, 5, 0, tzinfo=UTC)
    meal, _item = _one(db, owner, meal_time=now - timedelta(hours=1))
    screen = _sat(
        db, _button(_sat(db, f"ml:t:{meal.id}:t", now=now), "Не помню"), now=now
    )
    assert screen.text.startswith(
        "<i>✓ Время не указано. Приём остаётся в сб 31 октября"
    )


def test_not_remembered_twice_is_the_toast(db, owner):
    meal, _item = _one(db, owner)
    chip = _button(_sat(db, f"ml:t:{meal.id}:t"), "Не помню")
    _sat(db, chip)
    assert _sat(db, chip).toast == "Уже так"
    assert len(_events(db)) == 1


def test_an_old_not_remembered_button_stays_on_the_meals_present_day(db, owner):
    meal, _item = _one(db, owner)
    chip = _button(_sat(db, f"ml:t:{meal.id}:t"), "Не помню")
    _sat(db, f"ml:t:{meal.id}:t")
    _typed(db, "вчера 23:10")
    screen = _sat(db, chip)
    assert "Приём остаётся в пт 30 октября, без времени." in screen.text
    assert meal_card.meal_day(meal, owner) == date(2026, 10, 30)


def test_typing_after_not_remembered_gives_the_clock_back(db, owner):
    meal, _item = _one(db, owner)
    _sat(db, _button(_sat(db, f"ml:t:{meal.id}:t"), "Не помню"))
    assert _ask(db, owner)["kind"] == "time"
    typed = _typed(db, "13:20")
    assert typed.screen.text.startswith("<i>✓ Время: 13:20</i>\n\n")
    db.refresh(meal)
    assert meal.time_precision == "exact"


def test_yesterday_after_not_remembered_moves_the_day_and_invents_no_clock(db, owner):
    meal, _item = _one(db, owner)
    screen = _sat(db, _button(_sat(db, f"ml:t:{meal.id}:t"), "Не помню"))
    screen = _sat(db, _button(screen, "Вчера, 30 окт"))
    assert "Записано на: пт 30 октября, без времени." in screen.text
    assert "12:00" not in screen.text
    db.refresh(meal)
    assert meal.time_precision == "day_only"


def test_the_chip_on_a_meal_already_yesterday_says_no_new_day(db, owner):
    meal, _item = _one(db, owner, meal_time=datetime(2026, 10, 30, 9, 20, tzinfo=UTC))
    screen = _sat(db, _button(_sat(db, f"ml:t:{meal.id}:t"), "Вчера, 30 окт"))
    assert screen.text.startswith("<i>✓ Время примерное: ≈ 13:20</i>\n\n")
    assert "в этом дне" not in screen.text


# --- typed ------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,clock",
    [
        ("13:05", "13:05"),
        ("13.05", "13:05"),
        (" 9:05 ", "9:05"),
        ("0:00", "0:00"),
        ("23:59", "23:59"),
    ],
)
def test_a_typed_clock_keeps_the_meals_day(db, owner, text, clock):
    meal, _item = _one(db, owner)
    _sat(db, f"ml:t:{meal.id}:t")
    typed = _typed(db, text)
    assert typed.screen.text.startswith(f"<i>✓ Время: {clock}</i>\n\n")
    assert meal_card.meal_day(meal, owner) == date(2026, 10, 26)


@pytest.mark.parametrize("text", ["Вчера 23:10", "вчера  23.10", "ВЧЕРА 23:10"])
def test_typed_yesterday_any_case_and_separator(db, owner, text):
    meal, _item = _one(db, owner)
    _sat(db, f"ml:t:{meal.id}:t")
    typed = _typed(db, text)
    assert "пт 30 октября, 23:10" in typed.screen.text


def test_typed_yesterday_is_counted_from_today_not_from_the_meal(db, owner):
    meal, _item = _one(db, owner)
    _sat(db, f"ml:t:{meal.id}:t")
    _typed(db, "вчера 23:10")
    assert meal_card.meal_day(meal, owner) == date(2026, 10, 30)


@pytest.mark.parametrize(
    "text",
    [
        "суп",
        "24:00",
        "13:60",
        "1320",
        "13",
        "в 13",
        "час назад",
        "вчера",
        "вчерашний 13:20",
    ],
)
def test_anything_else_closes_the_slot_and_goes_on_down_the_router(db, owner, text):
    meal, _item = _one(db, owner)
    _sat(db, f"ml:t:{meal.id}:t")
    assert _typed(db, text) is None
    assert _ask(db, owner) is None
    assert _eaten(db, meal) == EATEN
    assert _events(db) == []


def test_the_future_is_refused_and_the_slot_stays(db, owner):
    meal, _item = _one(db, owner, meal_time=SATURDAY - timedelta(hours=2))
    _sat(db, f"ml:t:{meal.id}:t")
    typed = _typed(db, "14:01")  # now is 14:00 local
    assert typed.screen.text.startswith(FUTURE + "\n\n" + TIME_TITLE)
    assert _ask(db, owner)["kind"] == "time"
    assert _eaten(db, meal) == SATURDAY - timedelta(hours=2)
    assert _events(db) == []
    # Control: the minute that has come is taken.
    typed = _typed(db, "14:00")
    assert typed.screen.text.startswith("<i>✓ Время: 14:00</i>")
    assert len(_events(db)) == 1


def test_a_chip_whose_target_is_ahead_is_refused_too(db, owner):
    meal, _item = _one(db, owner)
    ahead = _ts(SATURDAY + timedelta(minutes=5))
    screen = _sat(db, f"ml:ts:{meal.id}:{ahead}:e:t")
    assert screen.text.startswith(FUTURE)
    assert _eaten(db, meal) == EATEN
    # Control: the same chip a minute in the past is applied.
    past = _ts(SATURDAY - timedelta(minutes=1))
    assert _sat(db, f"ml:ts:{meal.id}:{past}:e:t").text.startswith("<i>✓ Время")


def test_a_typed_clock_already_there_changes_nothing_and_says_so(db, owner):
    meal, _item = _one(db, owner)
    _sat(db, f"ml:t:{meal.id}:t")
    typed = _typed(db, "13:20")
    assert typed.screen.text.startswith(SAME + "\n\n" + TIME_TITLE)
    assert _events(db) == []
    assert _ask(db, owner)["kind"] == "time"


def test_a_typed_exact_clock_on_an_approximate_time_is_a_change(db, owner):
    meal, _item = _one(db, owner)
    meal.time_precision = "approx"
    db.commit()
    _sat(db, f"ml:t:{meal.id}:t")
    typed = _typed(db, "13:20")
    assert typed.screen.text.startswith("<i>✓ Время: 13:20</i>")
    db.refresh(meal)
    assert meal.time_precision == "exact"


def test_after_typing_the_screen_still_takes_another_time(db, owner):
    meal, _item = _one(db, owner)
    _sat(db, f"ml:t:{meal.id}:t")
    _typed(db, "13:05")
    assert _ask(db, owner)["kind"] == "time"
    typed = _typed(db, "12:40")
    assert typed.screen.text.startswith("<i>✓ Время: 12:40</i>")
    assert _eaten(db, meal).astimezone(ZONE).strftime("%H:%M") == "12:40"


def test_an_expired_slot_is_not_an_answer(db, owner):
    meal, _item = _one(db, owner)
    _sat(db, f"ml:t:{meal.id}:t")
    assert _typed(db, "13:05", now=SATURDAY + timedelta(minutes=11)) is None
    assert _eaten(db, meal) == EATEN


def test_a_slot_on_a_meal_deleted_meanwhile_answers_with_today(db, owner):
    meal, _item = _one(db, owner)
    _sat(db, f"ml:t:{meal.id}:t")
    meal_items.delete_meal(db, owner.id, meal.id)
    typed = _typed(db, "13:05")
    assert typed.screen.text.startswith("<i>Этого приёма уже нет")
    assert _events(db) == []


# --- typed, through the handler the bot registers ---------------------------


def _recent(db, owner, hours=3):
    return _one(db, owner, meal_time=datetime.now(UTC) - timedelta(hours=hours))


def test_handler_answers_strips_the_old_keyboard_and_the_new_message_is_the_slots(
    db, owner
):
    meal, _item = _recent(db, owner, hours=30)
    _tap(db, f"ml:t:{meal.id}:t", message_id=55, now=datetime.now(UTC))
    message, bot, consumed = _send("вчера 0:01")
    assert consumed
    assert "Время приёма" in message.replies[0]
    bot.edit_message_reply_markup.assert_awaited_once_with(
        chat_id=42, message_id=55, reply_markup=None
    )
    # The answer holds the live keyboard now: the next answer retires this one.
    assert _ask(db, owner)["message_id"] == message.sent_ids[0]
    _message, bot, consumed = _send("вчера 0:02")
    assert consumed
    bot.edit_message_reply_markup.assert_awaited_once_with(
        chat_id=42, message_id=message.sent_ids[0], reply_markup=None
    )


def test_handler_a_refused_time_stays_in_the_slot(db, owner):
    meal, _item = _recent(db, owner, hours=0)
    _tap(db, f"ml:t:{meal.id}:t", message_id=55, now=datetime.now(UTC))
    moment = (datetime.now(UTC) + timedelta(minutes=30)).astimezone(ZONE)
    if moment.date() != datetime.now(UTC).astimezone(ZONE).date():
        pytest.skip("half an hour from now is tomorrow")
    message, _bot, consumed = _send(f"{moment.hour}:{moment.minute:02d}")
    assert consumed
    assert message.replies[0].startswith(FUTURE)
    assert _ask(db, owner)["kind"] == "time"


def test_handler_a_clock_with_a_dot_is_a_time_here_not_a_weigh_in(db, owner):
    meal, _item = _recent(db, owner, hours=30)
    _tap(db, f"ml:t:{meal.id}:t", now=datetime.now(UTC))
    _message, _bot, consumed = _send("вчера 13.20")
    assert consumed
    assert len(_events(db)) == 1


def test_handler_a_word_closes_the_slot_and_goes_on(db, owner):
    meal, _item = _recent(db, owner)
    _tap(db, f"ml:t:{meal.id}:t", now=datetime.now(UTC))
    message, _bot, consumed = _send("суп с хлебом")
    assert not consumed
    assert message.replies == []
    assert _ask(db, owner) is None
    assert _events(db) == []


# --- back, by where it was tapped -------------------------------------------


def test_from_the_line_the_way_back_is_the_line(db, owner):
    meal, _item = _one(db, owner)
    line = _sat(db, f"ml:r:{meal.id}")
    assert _button(line, "🕐 Время") == f"ml:t:{meal.id}:r"
    screen = _sat(db, f"ml:t:{meal.id}:r")
    assert _rows(screen)[-1] == ["⬅️ К ответу бота"]
    assert _data(screen)[-1] == [f"ml:r:{meal.id}"]
    # And it is kept through a change.
    changed = _sat(db, _button(screen, "−15 мин"))
    assert _rows(changed)[-1] == ["⬅️ К ответу бота"]
    undone = _sat(db, _button(changed, UNDO))
    assert _rows(undone)[-1] == ["⬅️ К ответу бота"]


def test_from_a_card_opened_on_the_line_the_way_back_is_the_card(db, owner):
    meal, _item = _one(db, owner)
    card = _sat(db, f"ml:o:{meal.id}:r")
    screen = _sat(db, _button(card, "🕐 Время"))
    assert _rows(screen)[-1] == ["⬅️ К приёму"]
    assert _data(screen)[-1] == [f"ml:o:{meal.id}:r"]
    changed = _sat(db, _button(screen, "−15 мин"))
    assert _data(changed)[-1] == [f"ml:o:{meal.id}:r"]
    undone = _sat(db, _button(changed, UNDO))
    assert _data(undone)[-1] == [f"ml:o:{meal.id}:r"]
    # The card keeps its own way out.
    assert _rows(_sat(db, f"ml:o:{meal.id}:r"))[-1] == ["⬅️ К ответу бота"]


@pytest.mark.parametrize("origin", ["t", "y", "w", "h12"])
def test_from_a_card_with_any_other_origin_the_origin_travels(db, owner, origin):
    meal, _item = _one(db, owner)
    card = _sat(db, f"ml:o:{meal.id}:{origin}")
    screen = _sat(db, _button(card, "🕐 Время"))
    assert _data(screen)[-1] == [f"ml:o:{meal.id}:{origin}"]
    assert _data(screen)[0][0].endswith(f":e:{origin}")


def test_a_typed_answer_keeps_the_way_back(db, owner):
    meal, _item = _one(db, owner)
    _sat(db, f"ml:t:{meal.id}:r")
    assert _rows(_typed(db, "13:05").screen)[-1] == ["⬅️ К ответу бота"]


# --- undo -------------------------------------------------------------------


def test_undo_restores_the_clock_precision_source_and_words(db, owner):
    meal, _item = _one(db, owner)
    meal.time_precision, meal.time_source, meal.time_phrase = "approx", "said", "ночью"
    db.commit()
    changed = _sat(db, _button(_sat(db, f"ml:t:{meal.id}:t"), "Не помню"))
    screen = _sat(db, _button(changed, UNDO))
    assert screen.text.startswith("<i>✓ Возвращено</i>\n\n" + TIME_TITLE)
    assert "Записано на: пн 26 октября, ≈ 13:20." in screen.text
    db.refresh(meal)
    assert _aware(meal.meal_time) == EATEN
    assert (meal.time_precision, meal.time_source, meal.time_phrase) == (
        "approx",
        "said",
        "ночью",
    )
    assert _ask(db, owner)["kind"] == "time"


def test_the_undo_can_itself_be_undone(db, owner):
    meal, _item = _one(db, owner)
    changed = _sat(db, _button(_sat(db, f"ml:t:{meal.id}:t"), "−15 мин"))
    undone = _sat(db, _button(changed, UNDO))
    assert _eaten(db, meal) == EATEN
    redone = _sat(db, _button(undone, UNDO))
    assert "13:05" in redone.text
    assert _eaten(db, meal) == EATEN - timedelta(minutes=15)
    events = _events(db)
    assert len(events) == 3
    assert events[1].undoes_event_id == events[0].id
    assert events[2].undoes_event_id == events[1].id


def test_undo_after_a_later_time_change_is_refused(db, owner):
    meal, _item = _one(db, owner)
    first = _sat(db, _button(_sat(db, f"ml:t:{meal.id}:t"), "−15 мин"))
    _sat(db, _button(first, "−1 ч"))
    moved = _eaten(db, meal)
    screen = _sat(db, _button(first, UNDO))
    assert screen.text.startswith(meal_items.UNDO_REFUSED + "\n\n" + TIME_TITLE)
    assert _eaten(db, meal) == moved
    assert len(_events(db)) == 2


def test_undo_after_a_change_elsewhere_in_the_meal_still_applies(db, owner):
    meal, item = _one(db, owner)
    changed = _sat(db, _button(_sat(db, f"ml:t:{meal.id}:t"), "−15 мин"))
    meal_items.set_quantity(db, owner.id, item.id, 2.0)
    screen = _sat(db, _button(changed, UNDO))
    assert screen.text.startswith("<i>✓ Возвращено</i>")
    assert _eaten(db, meal) == EATEN
    db.refresh(item)
    assert item.factor == 2.0


def test_a_dish_undo_is_not_stopped_by_a_time_change(db, owner):
    meal, item = _one(db, owner)
    change = meal_items.set_grams(db, owner.id, item.id, 100.0)
    _sat(db, _button(_sat(db, f"ml:t:{meal.id}:t"), "−15 мин"))
    assert meal_items.undo(db, owner.id, change.event.id).status == meal_items.DONE
    assert _eaten(db, meal) == EATEN - timedelta(minutes=15)


def test_a_second_tap_on_undo_shows_the_time_screen_with_nothing_new(db, owner):
    meal, _item = _one(db, owner)
    changed = _sat(db, _button(_sat(db, f"ml:t:{meal.id}:t"), "−15 мин"))
    undo = _button(changed, UNDO)
    _sat(db, undo)
    again = _sat(db, undo)
    assert again.text.startswith(TIME_TITLE)
    assert len(_events(db)) == 2


def test_undo_of_a_time_on_a_meal_deleted_since_is_today(db, owner):
    meal, _item = _one(db, owner)
    changed = _sat(db, _button(_sat(db, f"ml:t:{meal.id}:t"), "−15 мин"))
    meal_items.delete_meal(db, owner.id, meal.id)
    screen = _sat(db, _button(changed, UNDO))
    assert screen.text.startswith("<i>Этого приёма уже нет")
    assert _eaten(db, meal) == EATEN - timedelta(minutes=15)


def test_a_dish_undo_with_the_cards_time_token_reads_as_the_line(db, owner):
    meal, item = _one(db, owner)
    change = meal_items.set_quantity(db, owner.id, item.id, 2.0)
    screen = _sat(db, f"ml:u:{change.event.id}:c")
    assert _rows(screen)[-1] == ["⬅️ К ответу бота"]
    ends = [data for row in _data(screen) for data in row if data.endswith(":c")]
    assert ends == [f"ml:t:{meal.id}:c"]  # only the card's own 🕐 carries it


# --- stale, foreign, malformed ----------------------------------------------


def test_a_deleted_meal_answers_with_today(db, owner):
    meal, _item = _one(db, owner)
    meal_items.delete_meal(db, owner.id, meal.id)
    for data in (
        f"ml:t:{meal.id}:r",
        f"ml:ts:{meal.id}:{_ts(EATEN)}:e:r",
        f"ml:ts:{meal.id}:{_ts(EATEN)}:d:r",
    ):
        assert _sat(db, data).text.startswith("<i>Этого приёма уже нет"), data
    assert _events(db) == []
    assert _ask(db, owner) is None


def test_a_foreign_meal_changes_nothing_and_warns(db, owner, stranger, caplog):
    meal, _item = _one(db, stranger)
    for data, label in (
        (f"ml:t:{meal.id}:r", "open"),
        (f"ml:ts:{meal.id}:{_ts(EATEN - timedelta(hours=1))}:e:r", "exact"),
        (f"ml:ts:{meal.id}:{_ts(EATEN)}:d:r", "day only"),
    ):
        with caplog.at_level(logging.WARNING, logger="app.services.meal_card"):
            caplog.clear()
            screen = _sat(db, data)
        assert screen.text.startswith("<b>📊 Сегодня"), label
        assert "unknown or foreign" in caplog.text, label
    db.refresh(meal)
    assert _aware(meal.meal_time) == EATEN
    assert meal.time_precision == "exact"
    assert _events(db) == []
    # Control: the same verb on the owner's meal does change it.
    own, _item = _one(db, owner)
    _sat(db, f"ml:ts:{own.id}:{_ts(EATEN - timedelta(hours=1))}:e:r")
    assert len(_events(db)) == 1


def test_a_foreign_time_undo_changes_nothing(db, owner, stranger):
    meal, _item = _one(db, stranger)
    change = meal_items.set_time(
        db, stranger.id, meal.id, EATEN - timedelta(hours=1), "exact"
    )
    screen = _sat(db, f"ml:u:{change.event.id}:r")
    assert screen.text.startswith("<b>📊 Сегодня")
    assert _eaten(db, meal) == EATEN - timedelta(hours=1)


def test_a_timestamp_no_calendar_holds_is_unknown_data(db, owner, caplog):
    meal, _item = _one(db, owner)
    with caplog.at_level(logging.WARNING, logger="app.services.meal_card"):
        screen = _sat(db, f"ml:ts:{meal.id}:999999999999:e:r")
    assert screen.text.startswith("<b>📊 Сегодня")
    assert "unknown or foreign" in caplog.text
    assert _eaten(db, meal) == EATEN


@pytest.mark.parametrize(
    "data",
    [
        "ml:t:1",
        "ml:t:x:r",
        "ml:t:1:q",
        "ml:ts:1:abc:e:r",
        "ml:ts:1:100:x:r",
        "ml:ts:1:100:e",
        "ml:ts:1:1234567890123:e:r",
    ],
)
def test_malformed_time_data_is_not_a_known_shape(data):
    assert not meal_card.is_known_shape(data)


@pytest.mark.parametrize(
    "data",
    [
        "ml:t:1:r",
        "ml:t:1:c",
        "ml:t:1:h9",
        "ml:ts:1:100:e:r",
        "ml:ts:1:100:a:c",
        "ml:ts:1:100:d:w",
    ],
)
def test_the_time_verbs_are_known_shapes(data):
    assert meal_card.is_known_shape(data)


def test_a_card_token_is_only_for_the_time_verbs():
    assert not meal_card.is_known_shape("ml:o:1:c")
    assert not meal_card.is_known_shape("ml:i:1:c")


# --- layout -----------------------------------------------------------------


def test_every_time_screen_keeps_the_layout_limits(db, owner):
    worst = 10**17
    meal, _item = _one(db, owner)
    screens = [_sat(db, f"ml:t:{meal.id}:h{worst}")]
    screens.append(_sat(db, _button(screens[0], "−15 мин")))
    screens.append(_sat(db, _button(screens[1], "Не помню")))
    for screen in screens:
        rows = _rows(screen)
        assert len(rows) <= 5
        for row in rows:
            assert len(row) <= 3
            budget = {1: 34, 2: 20, 3: 13}[len(row)]
            assert all(len(label) <= budget for label in row), row
    worst_data = f"ml:ts:{worst}:{_ts(SATURDAY)}:e:h{worst}"
    assert len(worst_data.encode()) <= 64
    assert meal_card.is_known_shape(worst_data)


@pytest.mark.parametrize(
    "month,label",
    [
        (1, "Вчера, 14 янв"),
        (2, "Вчера, 14 фев"),
        (3, "Вчера, 14 мар"),
        (4, "Вчера, 14 апр"),
        (5, "Вчера, 14 мая"),
        (6, "Вчера, 14 июн"),
        (7, "Вчера, 14 июл"),
        (8, "Вчера, 14 авг"),
        (9, "Вчера, 14 сен"),
        (10, "Вчера, 14 окт"),
        (11, "Вчера, 14 ноя"),
        (12, "Вчера, 14 дек"),
    ],
)
def test_the_yesterday_label_fits_its_budget_every_month(db, owner, month, label):
    meal, _item = _one(db, owner)
    now = datetime(2026, month, 15, 10, 0, tzinfo=UTC)
    assert _rows(_sat(db, f"ml:t:{meal.id}:t", now=now))[0][2] == label
    assert len(label) <= 13


def test_a_hard_day_sees_the_same_screen(db, owner):
    meal, _item = _one(db, owner)
    plain = _sat(db, f"ml:t:{meal.id}:t")
    owner.settings = {**owner.settings, "energy": "simple"}
    db.commit()
    hard = _sat(db, f"ml:t:{meal.id}:t")
    assert (hard.text, _rows(hard)) == (plain.text, _rows(plain))


# --- day and week, rebuilt --------------------------------------------------


def _card(db, tab, now=SATURDAY):
    return stats_screen.open_view(db, 42, tab, now, opened=True)


def test_day_cards_move_with_the_meal(db, owner):
    meal, _item = _one(db, owner, "творог", meal_time=SATURDAY - timedelta(hours=1))
    before = _card(db, stats_screen.TODAY)
    assert "творог" in before.text.lower()
    assert "13:00" in before.text
    assert "творог" not in _card(db, stats_screen.YESTERDAY).text.lower()

    _sat(db, f"ml:t:{meal.id}:t")
    _typed(db, "вчера 23:10")
    assert "творог" not in _card(db, stats_screen.TODAY).text.lower()
    yesterday = _card(db, stats_screen.YESTERDAY)
    assert "творог" in yesterday.text.lower()
    assert "23:10" in yesterday.text


def test_undo_moves_it_back(db, owner):
    meal, _item = _one(db, owner, "творог", meal_time=SATURDAY - timedelta(hours=1))
    _sat(db, f"ml:t:{meal.id}:t")
    typed = _typed(db, "вчера 23:10")
    _sat(db, _button(typed.screen, UNDO))
    assert "творог" in _card(db, stats_screen.TODAY).text.lower()


def test_the_week_leaves_a_meal_moved_out_of_it_and_takes_it_back_on_undo(db, owner):
    # 0:30 on Sunday the 25th, the week's first day, seen on Saturday the 31st.
    meal, _item = _one(db, owner, meal_time=datetime(2026, 10, 24, 20, 30, tzinfo=UTC))
    nothing = "записей нет"
    assert nothing not in _card(db, stats_screen.WEEK).text
    moved = _sat(db, _button(_sat(db, f"ml:t:{meal.id}:t"), "−1 ч"))
    assert "сб 24 октября, 23:30" in moved.text
    assert nothing in _card(db, stats_screen.WEEK).text
    _sat(db, _button(moved, UNDO))
    assert nothing not in _card(db, stats_screen.WEEK).text


def test_day_only_leaves_the_days_line_and_stays_in_the_day(db, owner):
    first, _i = _one(db, owner, "овсянка", meal_time=SATURDAY - timedelta(hours=5))
    meal, _i = _one(db, owner, "яблоко", meal_time=SATURDAY - timedelta(hours=2))
    _sat(db, _button(_sat(db, f"ml:t:{meal.id}:t"), "Не помню"))
    card = _card(db, stats_screen.TODAY)
    assert "🕐 9:00\n" in card.text + "\n"
    assert "яблоко" in card.text.lower()
    assert [mark["t"] for mark in card.picture["meals"]] == ["9:00"]
    assert "без времени яблоко" in card.text
    listed = _sat(db, f"ml:dl:{stats_screen._day_code(date(2026, 10, 31))}:t")
    assert "без времени · яблоко" in listed.text


def test_approximate_shows_its_mark_on_the_line_the_picture_and_the_numbers(db, owner):
    meal, _i = _one(db, owner, "яблоко", meal_time=SATURDAY - timedelta(hours=2))
    meal.time_precision = "approx"
    db.commit()
    card = _card(db, stats_screen.TODAY)
    assert "🕐 ≈ 12:00" in card.text
    assert card.picture["meals"][0]["t"] == "≈ 12:00"
    assert "≈ 12:00 яблоко" in card.text


@pytest.mark.skipif(not CHROME, reason="no CHROME: real drawing did not run")
def test_real_chrome_draws_the_day_of_an_approximate_and_a_timeless_meal(
    db, owner, tmp_path
):
    meal, _i = _one(db, owner, "яблоко", meal_time=SATURDAY - timedelta(hours=2))
    meal.time_precision = "approx"
    gone, _i = _one(db, owner, "суп", meal_time=SATURDAY - timedelta(hours=3))
    gone.time_precision = "day_only"
    db.commit()
    picture = _card(db, stats_screen.TODAY).picture
    assert picture["meals"][0]["t"] == "≈ 12:00" and len(picture["meals"]) == 1
    report = tmp_path / "report.json"
    report.write_text(json.dumps(picture, ensure_ascii=False))
    out = tmp_path / "card.png"
    done = subprocess.run(
        [sys.executable, RENDER / "watcher.py", "once", report, out],
        env={**os.environ, "RENDER_WARM": "0"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert out.exists()


def test_an_only_day_only_meal_leaves_no_clock_line(db, owner):
    meal, _i = _one(db, owner, "яблоко", meal_time=SATURDAY - timedelta(hours=2))
    meal.time_precision = "day_only"
    db.commit()
    card = _card(db, stats_screen.TODAY)
    assert "🕐" not in card.text
    assert card.picture["meals"] == []
    assert "яблоко" in card.text.lower()


# --- the readers of the clock -----------------------------------------------


def _at(db, owner, day, hour, precision="exact"):
    moment = datetime(2026, 10, day, hour, 0, tzinfo=ZONE).astimezone(UTC)
    meal, _item = _one(db, owner, "суп", meal_time=moment)
    meal.time_precision = precision
    db.commit()
    return meal


def test_day_only_is_out_of_moments_control_exact_and_approx_are_in(db, owner):
    meals = [
        _at(db, owner, 26, 9),
        _at(db, owner, 26, 12, "approx"),
        _at(db, owner, 26, 13, "day_only"),
    ]
    assert [m.hour for m in step_count._moments(meals, ZONE)] == [9, 12]


def _late_pattern(db, owner, precision="exact"):
    """Three days whose first food is late, the pattern ``early_clock`` looks for."""
    meals = []
    for day in (24, 25, 26):
        meals.append(_at(db, owner, day, 15, precision))
        meals.append(_at(db, owner, day, 23, precision))
    return meals


def test_early_clock_reads_only_exact_times(db, owner):
    # Control: the exact pattern gives an hour.
    assert step_count.early_clock(_late_pattern(db, owner), ZONE) is not None


@pytest.mark.parametrize("precision", ["approx", "day_only"])
def test_early_clock_ignores_what_is_not_a_clock(db, owner, precision):
    assert step_count.early_clock(_late_pattern(db, owner, precision), ZONE) is None


def test_recent_clock_control_and_approx_out(db, owner):
    today = date(2026, 10, 26)
    meals = _late_pattern(db, owner)
    assert stats_screen._recent_clock(db, owner, today, ZONE) is not None
    for meal in meals:
        meal.time_precision = "approx"
    db.commit()
    assert stats_screen._recent_clock(db, owner, today, ZONE) is None


def test_a_day_only_meal_still_makes_its_day_a_logged_one(db, owner):
    first, last = date(2026, 10, 26), date(2026, 10, 27)
    meals = [_at(db, owner, 26, 13, "day_only"), _at(db, owner, 27, 13)]
    assert step_count._logged(meals, ZONE, first, last) == 2


def test_day_only_is_out_of_the_long_gap_control_it_is_there_with_a_clock(db, owner):
    names = lambda meal: meal.description  # noqa: E731
    exact = [_at(db, owner, 26, 8), _at(db, owner, 26, 15), _at(db, owner, 26, 16)]
    assert day_card.facts(exact, ZONE, names).gap is not None
    gone = [*exact]
    gone[1] = _at(db, owner, 26, 9)
    gone[2] = _at(db, owner, 26, 16, "day_only")
    assert day_card.facts(gone, ZONE, names).gap is None


def test_day_only_says_no_part_of_day_in_the_group_lines(db, owner):
    meal = _at(db, owner, 26, 12, "day_only")
    meal.ai_analysis = {
        "food_groups": ["anchor"],
        "food_items": [{"name": "тофу", "groups": ["anchor"]}],
    }
    db.commit()
    names = lambda m: m.description  # noqa: E731
    assert day_card.facts([meal], ZONE, names).anchor == "тофу"
    # Control: with a clock the part of the day is named.
    meal.time_precision = "exact"
    assert day_card.facts([meal], ZONE, names).anchor == "тофу в обед"


def test_items_are_untouched_by_a_time_change(db, owner):
    meal = _meal(db, owner, ["рис", "курица"])
    before = [(i.id, i.name, i.base_calories) for i in crud_meal_item.live(db, meal.id)]
    calories = meal.calories
    _sat(db, _button(_sat(db, f"ml:t:{meal.id}:t"), "−15 мин"))
    db.refresh(meal)
    assert meal.calories == calories
    assert [
        (i.id, i.name, i.base_calories) for i in crud_meal_item.live(db, meal.id)
    ] == before


def test_set_time_refuses_a_meal_not_this_users(db, owner, stranger):
    meal, _item = _one(db, stranger)
    change = meal_items.set_time(db, owner.id, meal.id, EATEN, "day_only")
    assert change.status == meal_items.STALE
    assert isinstance(meal, Meal)
