"""The words of sections 5.3 and 5.4, and the replies M5, M6: through the real handlers."""

from __future__ import annotations

import asyncio
import itertools
import logging
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from telegram import Update

from app.crud.crud_meal_item import crud_meal_item
from app.models import BodyMetric, Meal, MealItem, MealMessage, MealSource, RecordEvent
from app.services import capture, close_row, meal_items
from app.services import telegram as tg
from tests.test_capture_router import (  # noqa: F401 (fixtures)
    CHAT,
    NOW,
    NOW_TS,
    OWNER,
    Session,
    Wire,
    _kb,
    _meal_now,
    _meals,
    _message,
    _sources,
    _voice,
    _with_app,
    clock,
    db,
    owner,
    photo,
    reader,
    send,
    text,
)

SOUP = {"foods": ["суп"], "calories": 100.0, "protein": 1, "fats": 1, "carbs": 1}


@pytest.fixture(autouse=True)
def _message_ids_go_on(monkeypatch):
    """As in a real chat: the bot's next message never reuses an id of an earlier one."""
    numbers = itertools.count(5000, 500)
    start = Wire.__init__

    def go_on(self):
        start(self)
        self.next_id = next(numbers)

    monkeypatch.setattr(Wire, "__init__", go_on)
    # What a message shows, per process: no screen is left from another test's message.
    monkeypatch.setattr(tg._card_delivery, "_screens", {})


def _item_names(db, meal) -> list:
    return [item.name for item in crud_meal_item.live(db, meal.id)]


def _live(db) -> list:
    return [meal for meal in _meals(db) if meal.deleted_at is None]


def _mapped(db, meal, message_id=777, view="line") -> dict:
    db.add(MealMessage(chat_id=CHAT, message_id=message_id, meal_id=meal.id, view=view))
    db.commit()
    return {
        "message_id": message_id,
        "date": NOW_TS,
        "chat": {"id": CHAT, "type": "private"},
    }


def tap(data: str, message_id: int = 4900):
    box = {}

    async def steps(application, wire):
        box["wire"] = wire
        message = _message()
        await application.process_update(
            Update.de_json(
                {
                    "update_id": message["message_id"],
                    "callback_query": {
                        "id": "1",
                        "from": message["from"],
                        "chat_instance": "c",
                        "data": data,
                        "message": {
                            "message_id": message_id,
                            "date": NOW_TS,
                            "chat": message["chat"],
                            "text": "x",
                        },
                    },
                },
                application.bot,
            )
        )
        await asyncio.gather(*list(tg._meal_reads))

    asyncio.run(_with_app(steps))
    return box["wire"]


def _toast(wire) -> str:
    return next(p for n, p in wire.calls if n == "answerCallbackQuery").get("text")


def _local(meal) -> datetime:
    """The stored moment, naive UTC on SQLite, on the owner's clock (UTC+4)."""
    return meal.meal_time.replace(tzinfo=None) + timedelta(hours=4)


# --- 5.3 time words --------------------------------------------------------------


@pytest.mark.parametrize(
    "words, local, precision, phrase, head",
    [
        (
            "13:00 суп",
            datetime(2026, 10, 26, 13, 0),
            "exact",
            "13:00",
            "пн 26 октября, 13:00",
        ),
        (
            "в 13 суп",
            datetime(2026, 10, 26, 13, 0),
            "exact",
            "в 13",
            "пн 26 октября, 13:00",
        ),
        (
            "суп в 13:10",
            datetime(2026, 10, 26, 13, 10),
            "exact",
            "в 13:10",
            "пн 26 октября, 13:10",
        ),
        (
            "суп в 14:10",  # ahead of the message: yesterday
            datetime(2026, 10, 25, 14, 10),
            "exact",
            "в 14:10",
            "вс 25 октября, 14:10",
        ),
        (
            "час назад суп",
            datetime(2026, 10, 26, 12, 20),
            "approx",
            "час назад",
            "пн 26 октября, ≈ 12:20",
        ),
        (
            "суп полчаса назад",
            datetime(2026, 10, 26, 12, 50),
            "approx",
            "полчаса назад",
            "пн 26 октября, ≈ 12:50",
        ),
        (
            "вчера суп",
            datetime(2026, 10, 25, 12, 0),
            "day_only",
            "вчера",
            "вс 25 октября, без времени",
        ),
        (
            "вчера вечером суп",
            datetime(2026, 10, 25, 20, 0),
            "approx",
            "вчера вечером",
            "вс 25 октября, ≈ 20:00",
        ),
        (
            "суп вчера днём",
            datetime(2026, 10, 25, 14, 0),
            "approx",
            "вчера днём",
            "вс 25 октября, ≈ 14:00",
        ),
        (
            "Вчера утром суп",
            datetime(2026, 10, 25, 9, 0),
            "approx",
            "Вчера утром",
            "вс 25 октября, ≈ 9:00",
        ),
        (
            "ночью суп",
            datetime(2026, 10, 26, 3, 0),
            "approx",
            "ночью",
            "пн 26 октября, ≈ 3:00",
        ),
        (
            "утром суп",
            datetime(2026, 10, 26, 9, 0),
            "approx",
            "утром",
            "пн 26 октября, ≈ 9:00",
        ),
        (
            "суп, вечером",
            datetime(2026, 10, 25, 20, 0),
            "approx",
            "вечером",
            "вс 25 октября, ≈ 20:00",
        ),
    ],
)
def test_time_words_move_the_meal_and_leave_the_name(
    db, owner, reader, clock, words, local, precision, phrase, head
):
    reader.answer = dict(SOUP)
    wire = send(text(words))
    meal = _meals(db)[0]
    assert _local(meal) == local
    assert meal.time_precision == precision
    assert meal.time_source == "said"
    assert meal.time_phrase == phrase
    assert meal.sent_at.replace(tzinfo=None) == NOW.replace(tzinfo=None)
    assert reader.text == ["суп"]  # the model never sees the phrase
    assert meal.description == "суп"
    assert wire.sent()[0]["text"].split("\n")[0] == f"Записано на {head}: суп."
    # What came stays as it came.
    assert _sources(db)[0].text == words


def test_a_part_of_the_day_just_ahead_is_now_and_keeps_the_sent_time(
    db, owner, reader, clock
):
    # R0 review: 14:00 is 40 minutes ahead of 13:20, so «днём» is this afternoon going
    # on, not yesterday's. The sent moment stays, and the words reach the model whole.
    reader.answer = dict(SOUP)
    wire = send(text("днём суп"))
    meal = _meals(db)[0]
    assert (meal.time_source, meal.time_precision) == ("sent", "exact")
    assert meal.meal_time.replace(tzinfo=None) == NOW.replace(tzinfo=None)
    assert reader.text == ["днём суп"]
    assert wire.sent()[0]["text"].startswith("Записано: ")


@pytest.mark.parametrize(
    "words",
    [
        "вчерашний суп",
        "утренний кофе",
        "ночной перекус",
        "вечерний чай",
        "суп в 200 г",
        "в 3 раза больше супа",
        "3 часа назад суп",
    ],
)
def test_words_that_only_look_like_time_stay_food(db, owner, reader, clock, words):
    reader.answer = dict(SOUP)
    wire = send(text(words))
    meal = _meals(db)[0]
    assert meal.time_source == "sent"
    assert meal.time_precision == "exact"
    assert meal.time_phrase is None
    assert meal.meal_time.replace(tzinfo=None) == NOW.replace(tzinfo=None)
    assert reader.text == [words]
    assert wire.sent()[0]["text"].startswith("Записано: ")


@pytest.mark.parametrize("words", ["утром каша, в обед суп", "утром каша, в 13:00 суп"])
def test_r0_two_times_in_one_message_keep_the_day_and_claim_no_clock(
    db, owner, reader, clock, words
):
    reader.answer = dict(SOUP)
    wire = send(text(words))
    meal = _meals(db)[0]
    assert meal.time_precision == "day_only"
    assert meal.time_source == "said"
    assert meal.time_phrase is None
    assert _local(meal) == datetime(2026, 10, 26, 12, 0)
    # No phrase is cut: neither is the meal's, and the words keep both times.
    assert reader.text == [words]
    assert meal.description == words
    head = wire.sent()[0]["text"].split("\n")[0]
    assert head == f"Записано на пн 26 октября, без времени: {words}."


def test_r0_control_one_time_in_the_message_is_still_its_time(db, owner, reader, clock):
    reader.answer = dict(SOUP)
    wire = send(text("в обед суп"))
    meal = _meals(db)[0]
    assert (meal.time_precision, meal.time_phrase) == ("approx", "в обед")
    assert _local(meal) == datetime(2026, 10, 26, 13, 0)
    assert reader.text == ["суп"]
    head = wire.sent()[0]["text"].split("\n")[0]
    assert head == "Записано на пн 26 октября, ≈ 13:00: суп."


def test_a_time_word_alone_is_no_dish(db, owner, reader, clock):
    """Nothing is left of the text to read, so the words stay what the model gets."""
    reader.answer = {"foods": []}
    send(text("утром"))
    assert reader.text == ["утром"]
    assert _item_names(db, _meals(db)[0]) == []


def test_a_caption_says_the_time_of_a_photo(db, owner, reader, clock):
    reader.answer = dict(SOUP)
    wire = send(photo(caption="вчера вечером"))
    meal = _meals(db)[0]
    assert _local(meal) == datetime(2026, 10, 25, 20, 0)
    assert meal.time_source == "said"
    assert reader.image[0]["caption"] is None
    assert wire.edits()[-1]["text"].startswith("Записано на вс 25 октября, ≈ 20:00: ")


def test_a_forward_keeps_its_own_time_over_the_words(db, owner, reader, clock):
    reader.answer = dict(SOUP)
    moment = NOW_TS - 86400
    send(
        text(
            "вчера суп",
            forward_origin={
                "type": "hidden_user",
                "date": moment,
                "sender_user_name": "x",
            },
        )
    )
    meal = _meals(db)[0]
    assert meal.time_source == "forward"
    assert meal.time_phrase is None


def test_an_exact_dish_with_a_time_word_is_free_and_said(db, owner, reader, clock):
    _meal_now(db, owner, minutes_ago=2000, names=("суп",))
    wire = send(text("вчера вечером суп"))
    assert reader.text == []
    meal = _live(db)[-1]
    assert _local(meal) == datetime(2026, 10, 25, 20, 0)
    assert meal.time_source == "said"
    assert meal.time_phrase == "вчера вечером"
    assert wire.seen()[0].startswith("Записано на вс 25 октября, ≈ 20:00: суп.")


def test_a_read_again_after_a_failure_has_no_phrase_in_it(db, owner, reader, clock):
    reader.error = RuntimeError("down")
    send(text("вчера вечером суп"))
    meal = _meals(db)[0]
    job = capture.read_again(db, meal.id)
    assert job.text == "суп"


def test_back_to_the_line_names_the_said_time(db, owner, reader, clock):
    reader.answer = dict(SOUP)
    send(text("вчера вечером суп"))
    meal = _meals(db)[0]
    wire = tap(f"ml:r:{meal.id}")
    assert wire.edits()[0]["text"] == "Записано на вс 25 октября, ≈ 20:00: суп."


# --- row 7 ---------------------------------------------------------------------


def test_row_7_a_clock_with_its_preposition_is_bare_too(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=30)
    send(text("в 12:40"))
    db.expire_all()
    assert _local(meal) == datetime(2026, 10, 26, 12, 40)
    assert len(_meals(db)) == 1


# --- 5.4 the words ----------------------------------------------------------------


@pytest.mark.parametrize(
    "words",
    [
        "ещё сыр",
        "еще сыр",
        "Ещё сыр",
        "и ещё сыр",
        "а ещё сыр",
        "плюс сыр",
        "добавь сыр",
        "забыл сыр",
        "забыла сыр",
        "забыли сыр",
        "забыло сыр",
        "я забыла сыр",
        "забыл добавить сыр",
        "забыла написать сыр",
        "забыла про сыр",
        "забыла, сыр",
    ],
)
def test_add_words_join_the_last_meal(db, owner, reader, clock, words):
    meal = _meal_now(db, owner, minutes_ago=30)
    wire = send(text(words))
    assert _item_names(db, meal) == ["суп", "сыр"]
    assert wire.sent()[0]["text"].startswith("Добавлено к приёму пн 26 октября, ")
    assert wire.sent()[0]["text"].endswith(": сыр.")
    assert reader.image == []


@pytest.mark.parametrize(
    "words",
    [
        "ещёсыр",
        "плюсик",
        "добавьте",
        "сыр ещё",
        "сыр, плюс хлеб",
        "забылась",
        "эточтоль",
        "этот суп",
        "это суп",
        "нет сахара",
        "неполный суп",
        "убранный суп",
        "уберите",
        "вторая",
        "одна порция",
    ],
)
def test_corrective_words_fire_only_as_words_at_the_start(
    db, owner, reader, clock, words
):
    meal = _meal_now(db, owner, minutes_ago=30)
    reader.answer = dict(SOUP)
    send(text(words))
    assert _item_names(db, meal) == ["суп"]
    assert len(_meals(db)) == 2


def test_a_question_is_no_correction(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=30)
    wire = send(text("ещё суп?"))
    assert _item_names(db, meal) == ["суп"]
    assert wire.seen() == [capture.M3B_QUESTION]


def test_after_three_hours_the_words_are_a_new_meal_and_the_miss_has_no_text(
    db, owner, reader, clock, caplog
):
    meal = _meal_now(db, owner, minutes_ago=240)
    reader.answer = dict(SOUP)
    with caplog.at_level(logging.INFO, logger="app.services.capture"):
        send(text("ещё суперсекретный суп"))
    assert _item_names(db, meal) == ["суп"]
    assert len(_meals(db)) == 2
    misses = [r.getMessage() for r in caplog.records if "no meal" in r.getMessage()]
    assert misses  # known-positive: the miss was logged
    assert not any("суперсекретный" in r.getMessage() for r in caplog.records)


def test_just_under_three_hours_control_the_words_still_join(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=179)
    send(text("ещё суп"))
    assert _item_names(db, meal) == ["суп", "суп"]


def test_a_deleted_last_meal_is_not_the_target(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=30)
    meal_items.delete_meal(db, owner.id, meal.id)
    reader.answer = dict(SOUP)
    send(text("ещё суп"))
    assert len(_live(db)) == 1
    assert _live(db)[0].id != meal.id


def test_a_weigh_in_and_a_routine_stay_what_they_are(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=30)
    moved = meal.meal_time
    wire = send(text("72,5"), text("13.20"))
    # 72,5 is saved; 13.20 went the weight's way, whose own range refuses it.
    assert len(db.scalars(select(BodyMetric)).all()) == 1
    assert not any(t.startswith("Время приёма") for t in wire.seen())
    assert len(_meals(db)) == 1
    db.expire_all()
    assert meal.meal_time == moved
    assert _item_names(db, meal) == ["суп"]


# --- born first, then joined --------------------------------------------------------


def test_an_add_is_born_as_its_own_meal_then_joined(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=30)
    wire = send(text("ещё сыр час назад"))
    rows = _meals(db)
    assert len(rows) == 2
    born = next(m for m in rows if m.id != meal.id)
    # Merged away, its own time kept on its own row for ➖ Отделить.
    assert born.merged_into_id == meal.id
    assert born.deleted_at is not None
    assert _local(born) == datetime(2026, 10, 26, 12, 20)
    assert born.time_source == "said"
    assert born.time_phrase == "час назад"
    # The moved item, marked with the source it came with.
    source = _sources(db)[0]
    assert source.meal_id == meal.id
    assert source.origin_meal_id == born.id
    assert source.text == "ещё сыр час назад"
    item = db.scalars(select(MealItem).where(MealItem.name == "сыр")).one()
    assert item.meal_id == meal.id
    assert item.source_id == source.id
    event = db.scalars(select(RecordEvent).where(RecordEvent.kind == "join")).one()
    assert event.meal_id == meal.id
    db.expire_all()
    assert meal.description == "суп, сыр"
    assert _item_names(db, meal) == ["суп", "сыр"]
    assert _kb(wire.sent()[0]) == [
        [close_row.CALLBACK],
        [f"ml:z:{source.id}:r", f"ml:o:{meal.id}:r"],
    ]


def test_the_born_meal_counts_in_no_reader(db, owner, reader, clock):
    from app.crud.crud_meal import crud_meal

    meal = _meal_now(db, owner, minutes_ago=30)
    send(text("ещё сыр"))
    db.expire_all()
    assert [m.id for m in crud_meal.get_user_meals(db, owner.id)] == [meal.id]


def test_a_new_name_gets_its_numbers_on_the_meal_it_joined(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=30)
    reader.answer = {
        "foods": ["сыр"],
        "calories": 110.0,
        "protein": 7.0,
        "fats": 9.0,
        "carbs": 0.0,
        "portion": "30г",
    }
    wire = send(text("ещё сыр"))
    assert reader.text == ["сыр"]
    item = db.scalars(select(MealItem).where(MealItem.name == "сыр")).one()
    assert item.numbers_source == "model"
    db.expire_all()
    assert meal.calories == 110.0
    assert not [p for p in wire.edits()]


def test_a_plate_joined_keeps_its_totals(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=30, names=("суп",), calories=100.0)
    reader.answer = {
        "foods": ["рис", "курица"],
        "calories": 500.0,
        "protein": 30.0,
        "fats": 10.0,
        "carbs": 60.0,
    }
    send(photo(reply_to_message=_mapped(db, meal)))
    db.expire_all()
    assert meal.calories == 600.0
    assert meal.numbers_basis == "plate"
    assert _item_names(db, meal) == ["суп", "рис", "курица"]


def test_a_meal_from_before_items_keeps_its_line_and_totals_when_joined(
    db, owner, reader, clock
):
    meal = Meal(
        user_id=owner.id,
        description="старый обед",
        meal_time=NOW - timedelta(minutes=30),
        sent_at=NOW - timedelta(minutes=30),
        calories=400.0,
    )
    db.add(meal)
    db.commit()
    reader.answer = {"foods": ["сыр"], "calories": 100.0}
    send(text("ещё сыр"))
    db.expire_all()
    assert _item_names(db, meal) == ["старый обед", "сыр"]
    assert meal.calories == 500.0  # the old totals stand, and the new dish counts


def test_a_join_event_has_no_way_back_but_separate(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=30)
    send(text("ещё сыр"))
    event = db.scalars(select(RecordEvent).where(RecordEvent.kind == "join")).one()
    change = meal_items.undo(db, owner.id, event.id)
    assert change.status == meal_items.REFUSED
    assert _item_names(db, meal) == ["суп", "сыр"]


# --- second helping -----------------------------------------------------------------


@pytest.mark.parametrize(
    "words",
    [
        "ещё порция",
        "еще порция",
        "ещё порцию",
        "добавка",
        "добавку",
        "вторая порция",
        "ещё одна",
    ],
)
def test_a_second_helping_is_a_new_item_and_both_captures_stay(
    db, owner, reader, clock, words
):
    reader.answer = dict(SOUP)
    wire = send(text("суп"), text(words))
    first = _meals(db)[0]
    assert _item_names(db, first) == ["суп", "суп"]
    items = list(db.scalars(select(MealItem).where(MealItem.meal_id == first.id)))
    assert len(items) == 2
    assert items[0].personal_food_id == items[1].personal_food_id
    sources = _sources(db)
    assert [s.text for s in sources] == ["суп", words]
    assert {s.meal_id for s in sources} == {first.id}
    said = wire.sent()[-1]["text"]
    assert said.startswith("Добавлено к приёму пн 26 октября, ")
    assert said.endswith(": суп.")


def test_no_string_of_a_second_helping_counts(db, owner, reader, clock):
    reader.answer = dict(SOUP)
    wire = send(text("суп"), text("добавка"), text("добавка"))
    meal = _meals(db)[0]
    assert _item_names(db, meal) == ["суп", "суп", "суп"]
    shown = " ".join(wire.seen())
    for count in ("×2", "×3", "2 порции", "3 порции", "порции", "третья", "вторая"):
        assert count not in shown
    assert "Добавлено" in shown  # known-positive


def test_a_second_helping_of_several_dishes_asks_which(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=30, names=("рис", "курица"))
    wire = send(text("добавка"))
    assert wire.sent()[0]["text"] == (
        "<b>Какое блюдо добавить ещё раз?</b>\n"
        "Можно не отвечать, ничего не изменится.\n"
        "«добавка», приём пн 26 октября, 12:50"
    )
    source = _sources(db)[0]
    items = list(db.scalars(select(MealItem).order_by(MealItem.id)))
    assert _kb(wire.sent()[0]) == [
        [f"ml:ka:{source.id}:{items[0].id}", f"ml:ka:{source.id}:{items[1].id}"],
        [f"ml:kw:{source.id}"],
        [f"ml:kn:{source.id}"],
    ]
    assert _item_names(db, meal) == ["рис", "курица"]
    assert source.meal_id is None


# --- M6a the chooser ------------------------------------------------------------------


def _chooser(db, owner, words, names=("рис", "курица", "овощи")):
    meal = _meal_now(db, owner, minutes_ago=30, names=names)
    wire = send(text(words))
    rows = _kb(wire.sent()[0])
    line_id = wire.sent()[0]
    items = list(db.scalars(select(MealItem).order_by(MealItem.id)))
    return meal, items, rows, _sources(db)[0], wire, line_id


def test_m6a_the_screen_and_nothing_applied(db, owner, reader, clock):
    meal, items, rows, source, wire, _ = _chooser(db, owner, "это была индейка")
    assert wire.sent()[0]["text"] == (
        "<b>Какое блюдо это меняет?</b>\n"
        "Можно не отвечать, ничего не изменится.\n"
        "«это была индейка», приём пн 26 октября, 12:50"
    )
    assert rows == [
        [f"ml:ka:{source.id}:{items[0].id}", f"ml:ka:{source.id}:{items[1].id}"],
        [f"ml:ka:{source.id}:{items[2].id}"],
        [f"ml:kn:{source.id}"],
    ]
    assert _item_names(db, meal) == ["рис", "курица", "овощи"]
    assert source.meal_id is None
    assert reader.text == []


def test_m6a_a_chip_replaces_that_dish(db, owner, reader, clock):
    meal, items, _, source, _, _ = _chooser(db, owner, "это была индейка")
    wire = tap(f"ml:ka:{source.id}:{items[1].id}", 5001)
    assert _item_names(db, meal) == ["рис", "индейка", "овощи"]
    shown = wire.edits()[0]
    assert shown["text"].startswith("Заменено в приёме пн 26 октября, ")
    assert shown["text"].endswith(": курица → индейка.")
    assert _kb(shown)[1][0].startswith("ml:u:")
    assert _kb(shown)[1][1] == f"ml:o:{meal.id}:r"
    db.expire_all()
    assert db.get(MealSource, source.id).meal_id == meal.id


def test_m6a_a_second_tap_changes_nothing_and_says_so(db, owner, reader, clock):
    meal, items, _, source, _, _ = _chooser(db, owner, "это была индейка")
    tap(f"ml:ka:{source.id}:{items[1].id}", 5001)
    wire = tap(f"ml:ka:{source.id}:{items[0].id}", 5001)
    assert _toast(wire) == meal_items.SAME_TOAST
    assert wire.edits() == []
    assert _item_names(db, meal) == ["рис", "индейка", "овощи"]


def test_m6a_a_new_name_from_a_chip_gets_its_numbers(db, owner, reader, clock):
    meal, items, _, source, _, _ = _chooser(db, owner, "это была индейка")
    reader.answer = {"foods": ["индейка"], "calories": 200.0, "protein": 30.0}
    tap(f"ml:ka:{source.id}:{items[1].id}", 5001)
    assert reader.text == ["индейка"]
    item = db.scalars(select(MealItem).where(MealItem.name == "индейка")).one()
    assert item.numbers_source == "model"


def test_m6a_separate_makes_its_own_meal_from_the_words(db, owner, reader, clock):
    meal, _, _, source, _, _ = _chooser(db, owner, "это была индейка")
    wire = tap(f"ml:kn:{source.id}", 5001)
    assert _item_names(db, meal) == ["рис", "курица", "овощи"]
    other = next(m for m in _live(db) if m.id != meal.id)
    assert _item_names(db, other) == ["индейка"]
    db.expire_all()
    assert db.get(MealSource, source.id).meal_id == other.id
    assert db.get(MealSource, source.id).origin_meal_id == other.id
    assert other.sent_at is not None
    item = db.scalars(select(MealItem).where(MealItem.name == "индейка")).one()
    assert wire.edits()[0]["text"] == "Записано: индейка."
    assert _kb(wire.edits()[0]) == [[f"ml:i:{item.id}:r", f"ml:t:{other.id}:r"]]
    # Its message answers for the new meal now.
    row = db.scalars(select(MealMessage).where(MealMessage.message_id == 5001)).one()
    assert row.meal_id == other.id


def test_m6a_second_helping_of_one_dish_and_of_the_whole_meal(db, owner, reader, clock):
    meal, items, _, source, _, _ = _chooser(db, owner, "добавка", names=("рис", "суп"))
    wire = tap(f"ml:ka:{source.id}:{items[1].id}", 5001)
    assert _item_names(db, meal) == ["рис", "суп", "суп"]
    assert wire.edits()[0]["text"].endswith(": суп.")
    assert wire.edits()[0]["text"].startswith("Добавлено к приёму пн 26 октября, ")
    db.expire_all()
    assert _kb(wire.edits()[0])[1][0] == f"ml:z:{source.id}:r"
    assert db.get(MealSource, source.id).meal_id == meal.id


def test_m6a_second_helping_of_the_whole_meal(db, owner, reader, clock):
    meal, _, _, source, _, _ = _chooser(db, owner, "добавка", names=("рис", "суп"))
    wire = tap(f"ml:kw:{source.id}", 5001)
    assert _item_names(db, meal) == ["рис", "суп", "рис", "суп"]
    assert wire.edits()[0]["text"].endswith(": рис и суп.")
    db.expire_all()
    assert db.get(MealSource, source.id).meal_id == meal.id


def test_m6a_second_helping_separate_is_its_own_meal_of_the_same_dishes(
    db, owner, reader, clock
):
    meal, _, _, source, _, _ = _chooser(db, owner, "добавка", names=("рис", "суп"))
    wire = tap(f"ml:kn:{source.id}", 5001)
    assert _item_names(db, meal) == ["рис", "суп"]
    other = next(m for m in _live(db) if m.id != meal.id)
    assert _item_names(db, other) == ["рис", "суп"]
    assert wire.edits()[0]["text"] == "Записано: рис и суп."


def test_a_removal_it_cannot_place_asks_with_no_separate_meal(db, owner, reader, clock):
    meal, items, rows, source, wire, _ = _chooser(db, owner, "убери хлеб")
    assert wire.sent()[0]["text"].startswith("<b>Какое блюдо это меняет?</b>\n")
    assert rows == [
        [f"ml:ka:{source.id}:{items[0].id}", f"ml:ka:{source.id}:{items[1].id}"],
        [f"ml:ka:{source.id}:{items[2].id}"],
    ]
    wire = tap(f"ml:ka:{source.id}:{items[0].id}", 5001)
    assert _item_names(db, meal) == ["курица", "овощи"]
    assert wire.edits()[0]["text"].endswith(": рис.")
    assert wire.edits()[0]["text"].startswith("Убрано из приёма пн 26 октября, ")


def test_m6a_a_foreign_source_or_item_gets_todays_card_and_a_warning(
    db, owner, reader, clock, caplog
):
    from app.models import User

    meal, items, _, source, _, _ = _chooser(db, owner, "это была индейка")
    other = User(telegram_id=99, settings={})
    db.add(other)
    db.commit()
    stranger = MealSource(
        user_id=other.id, kind="text", chat_id=99, message_id=1, text="добавка"
    )
    db.add(stranger)
    db.commit()
    with caplog.at_level(logging.WARNING, logger="app.services.meal_card"):
        tap(f"ml:ka:{stranger.id}:{items[0].id}", 5001)
        tap(f"ml:kw:{stranger.id}", 5001)
        tap(f"ml:kn:{stranger.id}", 5001)
    warnings = [r for r in caplog.records if "unknown or foreign" in r.getMessage()]
    assert len(warnings) == 3
    assert _item_names(db, meal) == ["рис", "курица", "овощи"]


def test_m6a_a_gone_meal_says_so(db, owner, reader, clock):
    meal, items, _, source, _, _ = _chooser(db, owner, "это была индейка")
    meal_items.delete_meal(db, owner.id, meal.id)
    wire = tap(f"ml:ka:{source.id}:{items[0].id}", 5001)
    assert "Этого приёма уже нет" in wire.seen()[0]


def test_m6a_a_dish_gone_since_says_so(db, owner, reader, clock):
    meal, items, _, source, _, _ = _chooser(db, owner, "это была индейка")
    meal_items.remove_item(db, owner.id, items[0].id)
    wire = tap(f"ml:ka:{source.id}:{items[0].id}", 5001)
    assert "Этого блюда в приёме уже нет" in wire.seen()[0]
    assert _item_names(db, meal) == ["курица", "овощи"]


# --- M6 replace and remove -----------------------------------------------------------


@pytest.mark.parametrize(
    "words, kept",
    [
        ("это был рис", None),
        ("это была индейка", None),
        ("это было рагу", None),
        ("это были котлеты", None),
        ("нет, индейка", None),
    ],
)
def test_replace_words_with_one_dish_go_straight(db, owner, reader, clock, words, kept):
    meal = _meal_now(db, owner, minutes_ago=30, names=("курица",))
    wire = send(text(words))
    name = words.rsplit(" ", 1)[-1]
    assert _item_names(db, meal) == [name]
    assert wire.sent()[0]["text"].startswith("Заменено в приёме пн 26 октября, ")
    assert wire.sent()[0]["text"].endswith(f": курица → {name}.")


def test_replace_on_a_stem_among_several_dishes(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=30, names=("рис", "курица"))
    send(text("не курицу, а индейку"))
    assert _item_names(db, meal) == ["рис", "индейку"]


def test_a_replace_with_the_same_dish_writes_nothing_and_answers(
    db, owner, reader, clock
):
    meal = _meal_now(db, owner, minutes_ago=30, names=("рис", "курица"))
    wire = send(text("это была курица"))
    assert _item_names(db, meal) == ["рис", "курица"]
    assert wire.seen() == [meal_items.SAME_TOAST]
    assert not db.scalars(select(RecordEvent)).all()
    assert _sources(db)[0].meal_id == meal.id


def test_removing_the_last_dish_takes_the_meal_softly_and_comes_back(
    db, owner, reader, clock
):
    meal = _meal_now(db, owner, minutes_ago=30, names=("сыр",))
    wire = send(text("убрать сыр"))
    db.expire_all()
    assert meal.deleted_at is not None
    assert wire.sent()[0]["text"].startswith("Убрано из приёма пн 26 октября, ")
    assert wire.sent()[0]["text"].endswith(": сыр.")
    undo = _kb(wire.sent()[0])[1][0]
    assert undo.startswith("ml:u:")
    tap(undo, 5001)
    db.expire_all()
    assert meal.deleted_at is None


# --- row 4: a reply -------------------------------------------------------------------


@pytest.mark.parametrize("view", ["line", "card", "source"])
def test_a_reply_finds_the_meal_by_any_of_its_messages(db, owner, reader, clock, view):
    meal = _meal_now(db, owner, minutes_ago=2000)
    wire = send(text("оливковое масло", reply_to_message=_mapped(db, meal, view=view)))
    assert _item_names(db, meal) == ["суп", "оливковое масло"]
    assert wire.sent()[0]["text"].endswith(": оливковое масло.")
    assert _kb(wire.sent()[0])[1][0].startswith("ml:z:")


def test_a_reply_to_the_persons_own_real_message(db, owner, reader, clock):
    reader.answer = dict(SOUP)
    first = text("суп")
    send(first)
    meal = _meals(db)[0]
    reply_to = {
        "message_id": first["message_id"],
        "date": NOW_TS,
        "chat": {"id": CHAT, "type": "private"},
        "text": "суп",
    }
    send(text("хлеб", reply_to_message=reply_to))
    assert _item_names(db, meal) == ["суп", "хлеб"]


def test_a_reply_to_a_message_of_a_joined_meal_reaches_where_it_went(
    db, owner, reader, clock
):
    meal = _meal_now(db, owner, minutes_ago=30)
    second = text("ещё сыр")
    send(second)
    reply_to = {
        "message_id": second["message_id"],
        "date": NOW_TS,
        "chat": {"id": CHAT, "type": "private"},
    }
    send(text("хлеб", reply_to_message=reply_to))
    assert _item_names(db, meal) == ["суп", "сыр", "хлеб"]


def test_a_mapping_left_on_a_merged_meal_is_followed(db, owner, reader, clock):
    """The two commits of a join are not one: a row can still point at the born meal."""
    meal = _meal_now(db, owner, minutes_ago=30)
    born = _meal_now(db, owner, minutes_ago=20, names=("сыр",))
    meal_items.join(db, owner.id, meal.id, born.id)
    send(text("хлеб", reply_to_message=_mapped(db, born)))
    assert _item_names(db, meal) == ["суп", "сыр", "хлеб"]


def test_a_mapping_into_someone_elses_meal_is_nobodys(db, owner, reader, clock):
    from app.models import User

    other = User(telegram_id=99, settings={})
    db.add(other)
    db.commit()
    theirs = _meal_now(db, other, minutes_ago=30)
    reader.answer = dict(SOUP)
    send(text("хлеб", reply_to_message=_mapped(db, theirs)))
    assert _item_names(db, theirs) == ["суп"]


def test_the_original_line_is_edited_to_the_new_list(db, owner, reader, clock):
    reader.answer = dict(SOUP)
    wire = send(text("суп"))
    line_id = wire.sent()[0]
    meal = _meals(db)[0]
    row = db.scalars(select(MealMessage).where(MealMessage.view == "line")).one()
    wire = send(
        text(
            "хлеб",
            reply_to_message={
                "message_id": row.message_id,
                "date": NOW_TS,
                "chat": {"id": CHAT, "type": "private"},
            },
        )
    )
    edit = wire.edits()[0]
    assert edit["message_id"] == row.message_id
    assert edit["text"] == "Записано: суп и хлеб."
    assert _kb(edit) == [[f"ml:o:{meal.id}:r", f"ml:t:{meal.id}:r"]]
    assert line_id


def test_words_of_row_8_edit_the_original_line_too(db, owner, reader, clock):
    reader.answer = dict(SOUP)
    wire = send(text("суп"), text("ещё сыр"))
    assert wire.edits()[-1]["text"] == "Записано: суп и сыр."


def test_a_refused_edit_of_the_line_costs_nothing(
    db, owner, reader, clock, monkeypatch
):
    from telegram.error import BadRequest
    from telegram.ext import ExtBot

    reader.answer = dict(SOUP)
    send(text("суп"))
    meal = _meals(db)[0]

    async def refused(self, *args, **kwargs):
        raise BadRequest("message can't be edited")

    monkeypatch.setattr(ExtBot, "edit_message_text", refused)
    wire = send(text("ещё сыр"))
    assert _item_names(db, meal) == ["суп", "сыр"]
    assert wire.sent()[0]["text"].endswith(": сыр.")


def test_grams_do_not_touch_the_line_when_it_says_the_same(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=30, names=("рис",))
    _mapped(db, meal, message_id=700, view="line")
    wire = send(text("200", reply_to_message=_mapped(db, meal)))
    assert wire.edits() == []


def test_a_line_on_a_screen_the_person_opened_stays_that_screen(
    db, owner, reader, clock
):
    meal = _meal_now(db, owner, minutes_ago=30)
    db.add(MealMessage(chat_id=CHAT, message_id=700, meal_id=meal.id, view="line"))
    db.commit()
    tap(f"ml:o:{meal.id}:r", 700)
    wire = send(text("ещё сыр"))
    assert wire.edits() == []
    assert _item_names(db, meal) == ["суп", "сыр"]


def test_back_on_the_line_it_is_the_lines_again(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=30)
    db.add(MealMessage(chat_id=CHAT, message_id=700, meal_id=meal.id, view="line"))
    db.commit()
    tap(f"ml:o:{meal.id}:r", 700)
    tap(f"ml:r:{meal.id}", 700)
    wire = send(text("ещё сыр"))
    assert wire.edits()[0]["message_id"] == 700


def test_a_card_opened_from_a_list_can_be_replied_to(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=2000)
    tap(f"ml:o:{meal.id}:t", 800)
    row = db.scalars(select(MealMessage).where(MealMessage.message_id == 800)).one()
    assert row.meal_id == meal.id
    assert row.view == "card"
    send(
        text(
            "хлеб",
            reply_to_message={
                "message_id": 800,
                "date": NOW_TS,
                "chat": {"id": CHAT, "type": "private"},
            },
        )
    )
    assert _item_names(db, meal) == ["суп", "хлеб"]


def test_a_message_gone_back_to_a_list_answers_for_no_meal(db, owner, reader, clock):
    """A wrong merge costs more than a new meal."""
    meal = _meal_now(db, owner, minutes_ago=2000)
    tap(f"ml:o:{meal.id}:t", 800)
    tap("ml:dl:20261025:t", 800)
    assert not db.scalars(
        select(MealMessage).where(MealMessage.message_id == 800)
    ).first()
    reader.answer = dict(SOUP)
    send(
        text(
            "хлеб",
            reply_to_message={
                "message_id": 800,
                "date": NOW_TS,
                "chat": {"id": CHAT, "type": "private"},
            },
        )
    )
    assert _item_names(db, meal) == ["суп"]
    assert len(_live(db)) == 2


def test_a_message_gone_back_to_the_stats_answers_for_no_meal(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=2000)
    tap(f"ml:o:{meal.id}:t", 800)
    tap("st:t", 800)
    assert not db.scalars(
        select(MealMessage).where(MealMessage.message_id == 800)
    ).first()


def test_a_reply_to_a_line_still_read_loses_neither_the_plate_nor_the_words(
    db, owner, reader, clock
):
    """Words added to a photo still being read leave the read its place: the line goes
    on saying «читаю» and the plate lands beside the words."""
    reader.gate = asyncio.Event()

    async def steps(application, wire):
        first = photo()
        await application.process_update(
            Update.de_json(
                {"update_id": first["message_id"], "message": first},
                application.bot,
            )
        )
        try:
            line_id = wire.sent()[0]
            assert line_id["text"] == capture.M1_PENDING
            row = db.scalars(
                select(MealMessage).where(MealMessage.view == "pending")
            ).one()
            reply = text(
                "ещё сыр",
                reply_to_message={
                    "message_id": row.message_id,
                    "date": NOW_TS,
                    "chat": {"id": CHAT, "type": "private"},
                },
            )
            await application.process_update(
                Update.de_json(
                    {"update_id": reply["message_id"], "message": reply},
                    application.bot,
                )
            )
            assert wire.edits() == []
            assert wire.sent()[-1]["text"].startswith("Добавлено")
        finally:
            # A failed assertion must not leave the read waiting for ever.
            reader.gate.set()
        await asyncio.gather(*list(tg._meal_reads))
        assert wire.edits()[-1]["message_id"] == row.message_id
        assert "рис" in wire.edits()[-1]["text"] and "сыр" in wire.edits()[-1]["text"]

    asyncio.run(_with_app(steps))
    assert sorted(_item_names(db, _live(db)[0])) == ["курица", "овощи", "рис", "сыр"]


def test_a_stop_word_in_reply_is_no_dish(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=30)
    wire = send(text("спасибо", reply_to_message=_mapped(db, meal)))
    assert _item_names(db, meal) == ["суп"]
    assert wire.seen() == []
    assert _sources(db)[0].meal_id is None


def test_corrective_words_in_a_reply_act_on_that_meal_whatever_its_age(
    db, owner, reader, clock
):
    old = _meal_now(db, owner, minutes_ago=2000, names=("рис", "курица"))
    _meal_now(db, owner, minutes_ago=5, names=("чай",))
    wire = send(text("убери рис", reply_to_message=_mapped(db, old)))
    assert _item_names(db, old) == ["курица"]
    assert wire.sent()[0]["text"].startswith("Убрано из приёма вс 25 октября, ")


def test_m5b_a_replacement_in_a_reply(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=30, names=("рис", "курица"))
    wire = send(text("не курица, а индейка", reply_to_message=_mapped(db, meal)))
    assert wire.sent()[0]["text"].startswith("Заменено в приёме пн 26 октября, ")
    assert wire.sent()[0]["text"].endswith(": курица → индейка.")
    assert _kb(wire.sent()[0])[1][0].startswith("ml:u:")
    assert _kb(wire.sent()[0])[1][1] == f"ml:o:{meal.id}:r"


def test_m5c_undo_and_the_screen(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=30, names=("рис",))
    wire = send(text("200", reply_to_message=_mapped(db, meal)))
    assert wire.sent()[0]["text"].startswith("Рис в приёме пн 26 октября, ")
    assert wire.sent()[0]["text"].endswith(": 200 г.")
    assert _kb(wire.sent()[0])[1][1] == f"ml:o:{meal.id}:r"
    tap(_kb(wire.sent()[0])[1][0], 5001)
    item = db.scalars(select(MealItem)).one()
    db.refresh(item)
    assert item.grams is None


def test_m5d_a_chip_applies_the_typed_grams_and_gives_m5c(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=30, names=("рис", "курица", "овощи"))
    wire = send(text("200", reply_to_message=_mapped(db, meal)))
    assert wire.sent()[0]["text"] == (
        "<b>200 г: у какого блюда?</b>\n"
        "Можно не отвечать, ничего не изменится.\n"
        "Приём пн 26 октября, 12:50: рис, курица и овощи."
    )
    chip = _kb(wire.sent()[0])[0][1]
    wire = tap(chip, 5001)
    item = db.scalars(select(MealItem).where(MealItem.name == "курица")).one()
    assert item.grams == 200
    shown = wire.edits()[0]
    assert shown["text"].startswith("Курица в приёме пн 26 октября, ")
    assert shown["text"].endswith(": 200 г.")
    assert _kb(shown)[1][0].startswith("ml:u:")
    assert _kb(shown)[1][1] == f"ml:o:{meal.id}:r"


def test_m5d_fractional_grams_survive_the_chip(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=30, names=("рис", "курица"))
    wire = send(text("150,5", reply_to_message=_mapped(db, meal)))
    tap(_kb(wire.sent()[0])[0][0], 5001)
    item = db.scalars(select(MealItem).where(MealItem.name == "рис")).one()
    assert item.grams == 150.5


def test_m5d_back_to_the_line(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=30, names=("рис", "курица"))
    wire = send(text("200", reply_to_message=_mapped(db, meal)))
    wire = tap(_kb(wire.sent()[0])[-1][0], 5001)
    assert wire.edits()[0]["text"] == "Записано: рис и курица."
    assert meal.deleted_at is None


@pytest.mark.parametrize(
    "words, local, precision, head",
    [
        ("12:10", datetime(2026, 10, 26, 12, 10), "exact", "12:10"),
        ("в 12:10", datetime(2026, 10, 26, 12, 10), "exact", "12:10"),
        ("в 12", datetime(2026, 10, 26, 12, 0), "exact", "12:00"),
        ("час назад", datetime(2026, 10, 26, 12, 20), "approx", "≈ 12:20"),
        ("утром", datetime(2026, 10, 26, 9, 0), "approx", "≈ 9:00"),
    ],
)
def test_m5e_a_bare_time_in_a_reply_is_a_time_and_no_dish(
    db, owner, reader, clock, words, local, precision, head
):
    meal = _meal_now(db, owner, minutes_ago=300)
    wire = send(text(words, reply_to_message=_mapped(db, meal)))
    db.expire_all()
    assert _local(meal) == local
    assert meal.time_precision == precision
    assert meal.time_source == "edited"
    assert _item_names(db, meal) == ["суп"]
    assert wire.sent()[0]["text"] == f"Время приёма теперь пн 26 октября, {head}: суп."
    assert _kb(wire.sent()[0])[1][0].startswith("ml:u:")


@pytest.mark.parametrize(
    "words, now, local, head",
    [
        # The part is still ahead of the sent clock, so going on now: its own clock
        # still goes on the replied meal's day, never the minute it was typed.
        ("вечером", (18, 30), datetime(2026, 10, 26, 20, 0), "≈ 20:00"),
        ("утром", (8, 0), datetime(2026, 10, 26, 9, 0), "≈ 9:00"),
        ("в обед", (11, 30), datetime(2026, 10, 26, 13, 0), "≈ 13:00"),
        ("вечером", (21, 0), datetime(2026, 10, 26, 20, 0), "≈ 20:00"),  # control
    ],
)
def test_a_part_in_reply_to_yesterdays_meal_keeps_its_own_clock(
    db, owner, reader, clock, monkeypatch, words, now, local, head
):
    meal = _meal_now(db, owner, minutes_ago=0)  # пн 26 октября, 13:20
    sent = datetime(2026, 10, 27, *now, tzinfo=NOW.tzinfo) - timedelta(hours=4)
    monkeypatch.setattr(capture, "_now", lambda: sent)
    reply = _mapped(db, meal)
    wire = send(text(words, date=int(sent.timestamp()), reply_to_message=reply))
    db.expire_all()
    assert (_local(meal), meal.time_precision) == (local, "approx")
    assert wire.sent()[0]["text"] == f"Время приёма теперь пн 26 октября, {head}: суп."


def test_m5e_yesterday_alone_in_a_reply_takes_the_clock_away(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=300)
    wire = send(text("вчера", reply_to_message=_mapped(db, meal)))
    db.expire_all()
    assert meal.time_precision == "day_only"
    assert wire.sent()[0]["text"] == (
        "Время приёма теперь вс 25 октября, без времени: суп."
    )


def test_a_reply_with_food_and_a_time_word_keeps_that_time_on_the_born_meal(
    db, owner, reader, clock
):
    meal = _meal_now(db, owner, minutes_ago=2000)
    send(text("хлеб утром", reply_to_message=_mapped(db, meal)))
    assert _item_names(db, meal) == ["суп", "хлеб"]
    born = next(m for m in _meals(db) if m.id != meal.id)
    assert _local(born) == datetime(2026, 10, 26, 9, 0)
    assert born.time_phrase == "утром"


# --- a reply by photo or voice -------------------------------------------------------


def test_a_photo_in_reply_joins_when_it_is_read(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=2000)
    db.add(MealMessage(chat_id=CHAT, message_id=700, meal_id=meal.id, view="line"))
    db.commit()
    reader.answer = {
        "foods": ["рис", "курица"],
        "calories": 500.0,
        "protein": 30.0,
        "fats": 10.0,
        "carbs": 60.0,
    }
    wire = send(photo(reply_to_message=_mapped(db, meal)))
    assert wire.sent()[0]["text"] == capture.M1_PENDING
    assert _item_names(db, meal) == ["суп", "рис", "курица"]
    line = wire.edits()[0]
    assert line["text"].startswith("Добавлено к приёму вс 25 октября, ")
    assert line["text"].endswith(": рис и курица.")
    source = _sources(db)[0]
    born = next(m for m in _meals(db) if m.id != meal.id)
    assert source.kind == "photo"
    assert source.meal_id == meal.id
    assert source.origin_meal_id == born.id
    assert born.merged_into_id == meal.id
    assert _kb(line) == [
        [close_row.CALLBACK],
        [f"ml:z:{source.id}:r", f"ml:o:{meal.id}:r"],
    ]
    # And the original line, with the new list.
    assert wire.edits()[1]["message_id"] == 700
    assert wire.edits()[1]["text"] == "Записано: суп, рис и курица."


def test_a_photo_in_reply_with_no_food_stays_its_own(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=30)
    reader.answer = {"foods": []}
    wire = send(photo(reply_to_message=_mapped(db, meal)))
    assert _item_names(db, meal) == ["суп"]
    assert capture.M7D_NO_FOOD in wire.seen()
    assert len(_live(db)) == 2


def test_a_photo_in_reply_with_the_model_down_stays_its_own(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=30)
    reader.error = RuntimeError("down")
    wire = send(photo(reply_to_message=_mapped(db, meal)))
    assert _item_names(db, meal) == ["суп"]
    assert capture.M7_FAILED in wire.seen()
    assert len(_live(db)) == 2
    assert _sources(db)[0].reply_to_message_id == 777


def test_a_read_again_after_a_restart_still_joins(db, owner, reader, clock):
    """The target comes from the kept source, so a restart does not lose it."""
    meal = _meal_now(db, owner, minutes_ago=30)
    reader.error = RuntimeError("down")
    send(photo(reply_to_message=_mapped(db, meal)))
    born = next(m for m in _live(db) if m.id != meal.id)
    job = capture.read_again(db, born.id)
    done = capture.read_done(db, job, dict(SOUP))
    assert done.line.text.startswith("Добавлено к приёму ")
    assert _item_names(db, meal) == ["суп", "суп"]


def test_a_voice_in_reply_joins_when_it_is_read(db, owner, reader, clock, monkeypatch):
    meal = _meal_now(db, owner, minutes_ago=30)

    async def heard(bot, job):
        return dict(SOUP), None

    monkeypatch.setattr(tg, "_reading", heard)
    wire = send(_voice() | {"reply_to_message": _mapped(db, meal)})
    assert _item_names(db, meal) == ["суп", "суп"]
    assert wire.edits()[0]["text"].startswith("Добавлено к приёму ")


def test_a_voice_in_reply_with_nothing_listening_is_never_silent(
    db, owner, reader, clock
):
    meal = _meal_now(db, owner, minutes_ago=30)
    wire = send(_voice() | {"reply_to_message": _mapped(db, meal)})
    assert _item_names(db, meal) == ["суп"]
    assert wire.sent()[0]["text"] == capture.M4_PENDING
    assert capture.M4D_FAILED in wire.seen()


def test_a_photo_with_no_reply_control_joins_nothing(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=5)
    reader.answer = dict(SOUP)
    wire = send(photo())
    assert _item_names(db, meal) == ["суп"]
    assert len(_live(db)) == 2
    assert not any(t.startswith("Добавлено") for t in wire.seen())


# --- M5a a deleted target -------------------------------------------------------------


def test_m5a_text(db, owner, reader, clock):
    reader.answer = dict(SOUP)
    meal = _meal_now(db, owner, minutes_ago=30)
    reply_to = _mapped(db, meal)
    meal_items.delete_meal(db, owner.id, meal.id)
    wire = send(text("суп", reply_to_message=reply_to))
    other = next(m for m in _live(db) if m.id != meal.id)
    assert wire.sent()[0]["text"].split("\n")[0] == (
        "Записано: суп. Тот приём был удалён раньше, поэтому это отдельная запись."
    )
    assert other.merged_into_id is None
    item = db.scalars(select(MealItem).where(MealItem.meal_id == other.id)).one()
    assert item.name == "суп"


def test_m5a_exact_dish_has_its_buttons_at_once(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=30)
    reply_to = _mapped(db, meal)
    meal_items.delete_meal(db, owner.id, meal.id)
    wire = send(text("суп", reply_to_message=reply_to))
    other = next(m for m in _live(db) if m.id != meal.id)
    item = db.scalars(select(MealItem).where(MealItem.meal_id == other.id)).one()
    assert _kb(wire.sent()[0]) == [[f"ml:i:{item.id}:r", f"ml:t:{other.id}:r"]]
    assert reader.text == []


def test_m5a_photo(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=30)
    reply_to = _mapped(db, meal)
    meal_items.delete_meal(db, owner.id, meal.id)
    reader.answer = dict(SOUP)
    wire = send(photo(reply_to_message=reply_to))
    assert wire.edits()[-1]["text"].split("\n")[0] == (
        "Записано: суп. Тот приём был удалён раньше, поэтому это отдельная запись."
    )
    other = next(m for m in _live(db) if m.id != meal.id)
    assert other.merged_into_id is None
    assert _item_names(db, other) == ["суп"]


def test_m5a_corrective_words_lose_the_word(db, owner, reader, clock):
    """The target is gone, what is left is the food."""
    meal = _meal_now(db, owner, minutes_ago=30)
    reply_to = _mapped(db, meal)
    meal_items.delete_meal(db, owner.id, meal.id)
    reader.answer = {"foods": ["сыр"], "calories": 90.0}
    wire = send(text("ещё сыр", reply_to_message=reply_to))
    assert reader.text == ["сыр"]
    assert wire.sent()[0]["text"].startswith("Записано: сыр. Тот приём")
    assert _sources(db)[0].text == "ещё сыр"


@pytest.mark.parametrize("words", ["убери сыр", "добавка", "200"])
def test_a_change_to_a_deleted_meal_makes_no_meal_and_says_so(
    db, owner, reader, clock, words
):
    """Nothing to take away and nothing to count: a new meal would be untrue."""
    meal = _meal_now(db, owner, minutes_ago=30)
    reply_to = _mapped(db, meal)
    meal_items.delete_meal(db, owner.id, meal.id)
    wire = send(text(words, reply_to_message=reply_to))
    assert wire.seen() == [capture.TARGET_GONE]
    assert len(_live(db)) == 0
    assert reader.text == []
    assert _sources(db)[0].text == words


def test_a_mapping_through_a_merge_into_a_deleted_meal_is_deleted(
    db, owner, reader, clock
):
    reader.answer = dict(SOUP)
    meal = _meal_now(db, owner, minutes_ago=30)
    born = _meal_now(db, owner, minutes_ago=20, names=("сыр",))
    meal_items.join(db, owner.id, meal.id, born.id)
    reply_to = _mapped(db, born)
    meal_items.delete_meal(db, owner.id, meal.id)
    wire = send(text("суп", reply_to_message=reply_to))
    assert wire.sent()[0]["text"].split("\n")[0].endswith(capture.M5A_DELETED)


def test_a_replayed_correction_is_one(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=30)
    message = text("ещё сыр")
    send(message)
    send(message)
    assert _item_names(db, meal) == ["суп", "сыр"]
    assert len(_sources(db)) == 1
    assert (
        db.scalars(select(Meal).where(Meal.merged_into_id == meal.id)).one() is not None
    )


def test_the_chooser_is_mapped_so_a_reply_to_it_finds_the_meal(
    db, owner, reader, clock
):
    meal = _meal_now(db, owner, minutes_ago=30, names=("рис", "курица"))
    wire = send(text("это была индейка"))
    assert wire.sent()[0]["text"].startswith("<b>Какое блюдо")
    row = db.scalars(select(MealMessage)).one()
    assert row.meal_id == meal.id
    assert row.view == "other"
