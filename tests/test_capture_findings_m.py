"""What the review of slice M found, each through the real handlers.

A read that stands over a time or a join, a line that is owed and not lost, words that
are no correction, and what leaves the bot's hands: the image, the logs, the numbers.
No network and no paid call: the model and Telegram are the fakes.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import struct
import zlib
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from telegram import Update
from telegram.error import BadRequest, NetworkError, RetryAfter, TimedOut

from app.crud.crud_meal_item import crud_meal_item
from app.models import Meal, MealMessage, MealReading, MealSource, User
from app.services import (
    capture,
    image_clean,
    meal_card,
    meal_items,
    next_step,
    stats_screen,
    step_count,
)
from app.services import telegram as tg
from app.services.product_lookup_service import ResolutionResult
from tests.test_capture_album import album, shot
from tests.test_capture_background import _pending_row, at  # noqa: F401
from tests.test_capture_join import (  # noqa: F401
    M11,
    _at,
    _clean,
    _hint_is_old,
    _meal_from,
    edited,
    tap,
)
from tests.test_capture_router import (  # noqa: F401
    CHAT,
    NOW,
    NOW_TS,
    NUTRITION,
    OWNER,
    Session,
    Wire,
    _document,
    _item_names,
    _meal_now,
    _message,
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

REAL_LEARNING = tg._schedule_personal_food_save
M7 = "Фото сохранено, распознать еду сейчас не вышло.\nВ дневнике запись с этим фото."
KEPT = "Сообщение изменено. В дневнике пока прежняя запись."
NO_RECORD = "Сообщение изменено. В дневнике по нему записи нет."
NOTHING = {"foods": [], "calories": None, "protein": None, "fats": None, "carbs": None}
PLATE = {"рис", "курица", "овощи"}


@pytest.fixture(autouse=True)
def _no_pause(monkeypatch):
    monkeypatch.setattr(tg, "SHOW_PAUSE_SECONDS", 0, raising=False)


@pytest.fixture
def refuse(monkeypatch):
    """Telegram refuses this method, this many times, with this error."""
    box = SimpleNamespace(method=None, left=0, error=TimedOut("slow"), tried=0)
    real = Wire.do_request

    async def do_request(self, url, method, request_data=None, **kw):
        if box.method and url.endswith("/" + box.method) and box.left:
            box.left -= 1
            box.tried += 1
            raise box.error
        return await real(self, url, method, request_data=request_data, **kw)

    monkeypatch.setattr(Wire, "do_request", do_request)
    return box


async def _in(application, message) -> None:
    await application.process_update(
        Update.de_json(
            {"update_id": message["message_id"], "message": message}, application.bot
        )
    )


async def _drained() -> None:
    while tg._meal_reads:
        await asyncio.gather(*list(tg._meal_reads), return_exceptions=True)


def _reply(message_id: int) -> dict:
    return {
        "message_id": message_id,
        "date": NOW_TS,
        "chat": {"id": CHAT, "type": "private"},
    }


def _during_read(db, reader, first, act) -> SimpleNamespace:
    """``first`` comes, its read is held, ``act`` happens, and the read comes back."""
    reader.gate = asyncio.Event()
    box = SimpleNamespace()

    async def steps(application, wire):
        box.wire = wire
        await _in(application, first)
        try:
            db.expire_all()
            row = db.scalars(
                select(MealMessage).where(MealMessage.view == "pending")
            ).one()
            box.line, box.meal_id = row.message_id, row.meal_id
            await act(application, row)
            box.edits_during = [
                e["text"] for e in wire.edits() if e["message_id"] == box.line
            ]
            box.sent_during = [s["text"] for s in wire.sent()]
        finally:
            reader.gate.set()
        await _drained()

    asyncio.run(_with_app(steps))
    db.expire_all()
    box.meal = db.get(Meal, box.meal_id)
    box.names = {i.name for i in crud_meal_item.live(db, box.meal_id)}
    box.line_says = [e["text"] for e in box.wire.edits() if e["message_id"] == box.line]
    box.applied = [(r.status, r.applied) for r in db.scalars(select(MealReading)).all()]
    return box


def _says(*words, reply=False):
    async def act(application, row):
        for body in words:
            fields = {"reply_to_message": _reply(row.message_id)} if reply else {}
            await _in(application, text(body, **fields))

    return act


# --- a read stands over a join and over a time ----------------------------------------


@pytest.mark.parametrize("reply", [False, True])
def test_more_words_during_a_read_keep_the_plate(db, owner, reader, clock, reply):
    box = _during_read(db, reader, photo(), _says("ещё сыр", reply=reply))
    assert box.names == PLATE | {"сыр"}
    assert box.edits_during == []  # the line stays the read's, and the read lands in it
    assert any(body.startswith("Добавлено") for body in box.sent_during)
    assert box.meal.reading_status == "read"
    assert "рис" in box.line_says[-1] and "сыр" in box.line_says[-1]
    assert ("done", True) in box.applied


def test_more_words_to_typed_food_during_its_read_keep_both(db, owner, reader, clock):
    box = _during_read(db, reader, text("суп"), _says("ещё сыр"))
    assert PLATE <= box.names and "сыр" in box.names
    assert box.meal.reading_status == "read"


def test_the_persons_own_replacement_still_outranks_the_read(db, owner, reader, clock):
    """The control: only a time or a join lets a read land over a moved ``rev``."""
    box = _during_read(
        db, reader, photo(), _says("ещё сыр", "это была индейка", reply=False)
    )
    assert box.names == {"индейка"}
    assert ("done", False) in box.applied
    assert box.meal.reading_status == "read"


def test_a_read_that_fails_over_a_join_says_so_and_can_be_tried(
    db, owner, reader, clock
):
    reader.error = RuntimeError("down")
    box = _during_read(db, reader, photo(), _says("ещё сыр"))
    assert box.names == {"сыр"}
    assert box.meal.reading_status == "failed"
    assert box.line_says[-1] == M7
    reader.error, reader.gate = None, None
    reader.image.clear()
    tap(f"ml:rr:{box.meal_id}:r", box.line)
    assert len(reader.image) == 1
    db.expire_all()
    assert {i.name for i in crud_meal_item.live(db, box.meal_id)} == PLATE | {"сыр"}


def test_a_read_with_no_food_over_a_join_keeps_the_join(db, owner, reader, clock):
    reader.answer = dict(NOTHING)
    box = _during_read(db, reader, photo(), _says("ещё сыр"))
    assert box.names == {"сыр"}
    assert box.meal.deleted_at is None and box.meal.reading_status == "read"
    assert box.line_says[-1] == "Записано: сыр."


def test_silence_in_a_voice_over_a_join_leaves_no_line_on_reading(
    db, owner, reader, clock, monkeypatch
):
    from app.services import voice

    async def silent(_bot, _job):
        await reader.gate.wait()
        raise voice.NoSpeech()

    monkeypatch.setattr(tg, "_voice_words", silent)
    box = _during_read(db, reader, _voice(), _says("ещё сыр"))
    # A change typed while a voice is not heard yet waits (the voice may be a weight),
    # and the voice's line still ends: it is not left on «читаю».
    assert capture.STILL_READING in box.sent_during
    assert box.names == set()
    assert box.line_says[-1].startswith(meal_card.M4E_NO_SPEECH)


def test_a_further_change_after_the_join_is_not_left_on_reading(
    db, owner, reader, clock
):
    """Made on a screen, so nothing retouched the line: the late read still ends it."""

    async def act(application, row):
        await _in(application, text("ещё сыр"))
        with tg.SessionLocal() as session:
            item = crud_meal_item.live(session, row.meal_id)[0]
            meal_items.set_grams(session, owner.id, item.id, 50.0)

    box = _during_read(db, reader, photo(), act)
    assert box.names == {"сыр"}
    assert ("done", False) in box.applied
    assert box.line_says[-1].startswith("Записано: сыр")


@pytest.mark.parametrize("first", [photo, lambda: text("тарелка супа")])
def test_a_time_during_a_read_costs_it_nothing(db, owner, reader, clock, first):
    box = _during_read(db, reader, first(), _says("12:10", reply=True))
    assert box.names == PLATE
    assert box.meal.reading_status == "read"
    said = [body for body in box.sent_during if body.startswith("Время")]
    assert said and ": ." not in said[0]
    assert ("done", True) in box.applied
    assert "рис" in box.line_says[-1]
    assert "12:10" in capture._when(box.meal, capture.user_zone(owner))


def test_a_time_said_to_a_photo_still_read_has_no_empty_what(db, owner, reader, clock):
    box = _during_read(db, reader, photo(), _says("12:10", reply=True))
    assert "Время приёма теперь пн 26 октября, 12:10." in box.sent_during


def test_the_time_button_during_a_read_costs_it_nothing(db, owner, reader, clock):
    async def act(application, row):
        with tg.SessionLocal() as session:
            meal_items.set_time(
                session,
                owner.id,
                row.meal_id,
                NOW - timedelta(hours=1),
                meal_card.EXACT,
            )

    box = _during_read(db, reader, photo(), act)
    assert box.names == PLATE
    assert ("done", True) in box.applied


def test_a_straggler_read_does_not_take_a_join_away(db, owner, reader, clock):
    """A re-read replaces what the read before gave: the words said to it stay out of
    its way, so it does not land at all, and the words stand."""
    album(shot("a"), shot("b"))
    db.expire_all()
    meal = db.scalars(select(Meal)).one()
    source = capture._job_of(db, meal)
    job = capture.replace(
        source, replaces=True, last_event=capture._last_event(db, meal)
    )
    meal.reading_status = "pending"
    db.commit()
    meal_items.set_time(
        db, owner.id, meal.id, NOW - timedelta(hours=1), meal_card.EXACT
    )
    db.refresh(meal)
    assert capture._read_stands(db, meal, job)
    send(text("ещё сыр"))
    db.expire_all()
    meal = db.get(Meal, meal.id)
    meal.reading_status = "pending"
    db.commit()
    assert not capture._read_stands(db, meal, job)


@pytest.mark.parametrize("how", ["photo", "voice", "control"])
def test_a_reply_to_a_line_still_read_joins_and_the_read_lands(
    db, owner, reader, monkeypatch, how
):
    box = {}

    async def resolve(payload, **_kw):
        if payload == "data:A":
            box["started"] = True
            await box["gate"].wait()
            foods = ["суп"]
        else:
            foods = ["хлеб"]
        return ResolutionResult(
            nutrition={**NUTRITION, "foods": foods},
            source="vision",
            confidence_tier="low",
            description=None,
            signals={"portion_grams": 300.0},
            portion_grams=300.0,
        )

    async def analyze(**_kw):
        return {**NUTRITION, "foods": ["хлеб"]}

    async def heard(_bot, _job):
        return "хлеб"

    async def download(_bot, file_id):
        return f"data:{file_id}"

    monkeypatch.setattr(tg, "_download_image", download)
    monkeypatch.setattr(tg, "resolve_meal_nutrition", resolve)
    monkeypatch.setattr(tg, "_analyze_and_parse", analyze)
    monkeypatch.setattr(tg, "_voice_words", heard)

    def picture(file_id):
        size = {"file_id": file_id, "file_unique_id": file_id, "width": 9, "height": 9}
        return _message(photo=[size])

    async def steps(application, wire):
        box["gate"] = asyncio.Event()
        await _in(application, picture("A"))
        for _ in range(100):
            if box.get("started"):
                break
            await asyncio.sleep(0.01)
        db.expire_all()
        row = db.scalars(select(MealMessage).where(MealMessage.view == "pending")).one()
        reply = {"reply_to_message": _reply(row.message_id)}
        if how == "photo":
            await _in(application, picture("B") | reply)
        elif how == "voice":
            await _in(application, _voice() | reply)
        else:
            await _in(application, picture("B"))
        for _ in range(300):
            await asyncio.sleep(0.01)
            db.expire_all()
            meals = db.scalars(select(Meal).order_by(Meal.id)).all()
            if len(meals) == 2 and meals[1].reading_status != "pending":
                break
        box["gate"].set()
        await _drained()

    asyncio.run(_with_app(steps))
    db.expire_all()
    first, second = db.scalars(select(Meal).order_by(Meal.id)).all()
    names = {i.name for i in crud_meal_item.live(db, first.id)}
    if how == "control":
        assert names == {"суп"}
        assert [i.name for i in crud_meal_item.live(db, second.id)] == ["хлеб"]
    else:
        assert names == {"суп", "хлеб"}
        assert second.deleted_at is not None
    assert first.reading_status == "read"


# --- words that are no correction -----------------------------------------------------


@pytest.mark.parametrize(
    "words",
    [
        "нет, спасибо",
        "Нет, спасибо!",
        "нет, не ел",
        "это был отличный день",
        "нет",
        "спасибо",
    ],
)
@pytest.mark.parametrize("as_reply", [False, True])
def test_words_that_correct_nothing_leave_the_dish(
    db, owner, reader, clock, words, as_reply
):
    reader.answer = dict(NOTHING)
    meal = _meal_now(db, owner, minutes_ago=30, names=("курица",))
    fields = {}
    if as_reply:
        capture.map_message(db, CHAT, 777, meal.id, "line")
        fields = {"reply_to_message": _reply(777)}
    send(text(words, **fields))
    db.expire_all()
    assert _item_names(db, meal) == ["курица"]


@pytest.mark.parametrize("words", ["нет, индейка", "это была индейка"])
def test_a_named_dish_still_replaces_the_only_one(db, owner, reader, clock, words):
    meal = _meal_now(db, owner, minutes_ago=30, names=("курица",))
    send(text(words))
    db.expire_all()
    assert _item_names(db, meal) == ["индейка"]


def test_many_words_with_no_stem_ask_which_dish(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=30, names=("курица",))
    wire = send(text("это был отличный день"))
    assert wire.sent()[-1]["text"].startswith("<b>Какое блюдо это меняет?</b>")
    assert _item_names(db, meal) == ["курица"]


def test_no_thanks_is_no_new_meal(db, owner, reader, clock):
    _meal_now(db, owner, minutes_ago=30, names=("курица",))
    wire = send(text("нет, спасибо"))
    assert wire.seen() == []
    assert reader.text == []


# --- 🔁 Прочитать заново promises only what it does ------------------------------------


def _anew_line(db, meal_id):
    db.expire_all()
    return db.scalars(
        select(MealMessage)
        .where(
            MealMessage.meal_id == meal_id, MealMessage.view == capture.READ_ANEW_VIEW
        )
        .order_by(MealMessage.id.desc())
    ).first()


def _buttons(params: dict) -> list:
    import json

    markup = params.get("reply_markup")
    markup = json.loads(markup) if isinstance(markup, str) else markup
    return [b["callback_data"] for row in markup["inline_keyboard"] for b in row]


def _join_two(db, reader):
    first = _meal_from(db, reader, "суп", ago=20)
    message = text("сыр", date=NOW_TS)
    reader.answer = _at("сыр")
    send(message)
    second = db.scalars(select(Meal).order_by(Meal.id.desc())).first()
    tap(f"ml:j:{first.id}:{second.id}:r")
    return first, message


def test_an_edit_of_a_joined_message_does_not_offer_a_read_of_another(
    db, owner, reader
):
    first, message = _join_two(db, reader)
    wire = edited({**message, "text": "брынза"})
    said = wire.sent()[-1]
    assert said["text"] == KEPT
    assert _buttons(said) == [f"ml:o:{first.id}:r"]


def test_an_edit_of_words_added_to_a_meal_does_not_offer_it(db, owner, reader, clock):
    reader.answer = dict(NUTRITION)
    send(text("суп"))
    db.expire_all()
    first = db.scalars(select(Meal)).one()
    message = text("ещё сыр")
    send(message)
    wire = edited({**message, "text": "ещё брынза"})
    assert wire.sent()[-1]["text"] == KEPT
    assert _buttons(wire.sent()[-1]) == [f"ml:o:{first.id}:r"]


def test_an_edit_of_a_meals_own_first_message_reads_the_new_words(db, owner, reader):
    message = text("рис", date=NOW_TS)
    reader.answer = _at("рис")
    send(message)
    meal = db.scalars(select(Meal)).one()
    wire = edited({**message, "text": "гречка"})
    assert wire.sent()[-1]["text"] == M11
    reader.text.clear()
    reader.answer = _at("гречка")
    tap(f"ml:rr:{meal.id}:r", _anew_line(db, meal.id).message_id)
    assert reader.text == ["гречка"]


def test_an_edit_of_an_albums_caption_still_offers_the_read(db, owner, reader):
    second = shot("b")
    album(shot("a"), second)
    wire = edited({**second, "caption": "суп"})
    assert wire.sent()[-1]["text"] == M11


# --- an edit of words that made no record ----------------------------------------------


def test_an_edit_of_a_message_with_no_food_can_become_food(db, owner, reader):
    reader.answer = dict(NOTHING)
    message = text("как дела")
    send(message)
    wire = edited({**message, "text": "творог"})
    said = wire.sent()[-1]
    assert said["text"] == NO_RECORD
    source = db.scalars(
        select(MealSource).where(MealSource.supersedes_id.is_(None))
    ).one()
    assert _buttons(said) == [f"ml:nf:{source.id}"]
    tap(f"ml:nf:{source.id}")
    db.expire_all()
    meal = db.scalars(select(Meal).where(Meal.deleted_at.is_(None))).one()
    assert [i.name for i in crud_meal_item.live(db, meal.id)] == ["творог"]


@pytest.mark.parametrize("words", ["а сколько это?", "спасибо"])
def test_an_edit_of_a_question_or_a_stop_word_can_become_food(
    db, owner, reader, clock, words
):
    message = text(words)
    send(message)
    wire = edited({**message, "text": "творог"})
    assert wire.sent()[-1]["text"] == NO_RECORD
    assert len(db.scalars(select(MealSource)).all()) == 2


def test_an_edit_into_a_stop_word_keeps_the_words_and_says_nothing(
    db, owner, reader, clock
):
    message = text("а сколько это?")
    send(message)
    wire = edited({**message, "text": "спасибо"})
    assert wire.sent() == []
    assert len(db.scalars(select(MealSource)).all()) == 2


def test_a_retry_reads_the_words_as_they_stand_now(db, owner, reader):
    reader.error = RuntimeError("down")
    message = text("рис")
    send(message)
    edited({**message, "text": "гречка"})
    db.expire_all()
    job = capture._job_of(db, db.scalars(select(Meal)).one())
    assert job.text == "гречка"


# --- a draft stays a draft ------------------------------------------------------------


def _drafting(db, owner):
    owner.settings = {**(owner.settings or {}), "draft_mode": True}
    db.commit()


def _day_text(db):
    from app.services import stats_screen

    db.expire_all()
    return stats_screen.handle_callback(db, OWNER, "st:t", now=NOW).text


def test_words_added_to_a_confirmed_meal_wait_for_their_own_tick(
    db, owner, reader, clock
):
    _drafting(db, owner)
    send(text("гречка"))
    db.expire_all()
    meal = db.scalars(select(Meal)).one()
    tap(f"ml:ok:{meal.id}")
    wire = send(text("ещё сыр"))
    db.expire_all()
    new = db.scalars(select(Meal).order_by(Meal.id.desc())).first()
    assert new.id != meal.id and new.is_draft and new.deleted_at is None
    assert [i.name for i in crud_meal_item.live(db, new.id)] == ["сыр"]
    assert "сыр" not in [i.name for i in crud_meal_item.live(db, meal.id)]
    assert f"ml:ok:{new.id}" in _buttons(wire.sent()[-1])


def test_a_photo_replying_to_a_confirmed_meal_waits_for_its_own_tick(
    db, owner, reader, clock
):
    _drafting(db, owner)
    send(text("гречка"))
    db.expire_all()
    meal = db.scalars(select(Meal)).one()
    tap(f"ml:ok:{meal.id}")
    line = db.scalars(
        select(MealMessage).where(
            MealMessage.meal_id == meal.id, MealMessage.view == "line"
        )
    ).first()
    wire = send(photo(reply_to_message=_reply(line.message_id)))
    db.expire_all()
    new = db.scalars(select(Meal).order_by(Meal.id.desc())).first()
    assert new.id != meal.id and new.is_draft and new.deleted_at is None
    assert f"ml:ok:{new.id}" in _buttons(wire.edits()[-1])


def test_with_drafts_off_words_still_join(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=5, names=("суп",))
    send(text("ещё сыр"))
    assert _item_names(db, meal) == ["суп", "сыр"]


# --- no food is no moment -------------------------------------------------------------


def _row(db, owner, day, hour, minute=0, status="read") -> Meal:
    meal = Meal(
        user_id=owner.id,
        meal_time=datetime(2026, 10, day, hour, minute) - timedelta(hours=4),
        sent_at=NOW,
        photos=[],
        numbers_basis="none",
        reading_status=status,
    )
    db.add(meal)
    db.commit()
    return meal


def test_a_photo_with_no_food_earns_no_early_day_and_moves_no_clock(db, owner):
    zone = ZoneInfo("Asia/Tbilisi")
    days = range(10, 15)
    read = [_row(db, owner, d, 13) for d in days] + [
        _row(db, owner, d, 21) for d in days
    ]
    empty = [_row(db, owner, d, 6, 30, status="no_food") for d in days]
    chosen = {"id": next_step.EARLY, "hour": 11, "turn": 3}
    span = date(2026, 10, 10), date(2026, 10, 14)
    assert step_count.count_days(read, chosen, zone, *span) == 0
    assert step_count.count_days(read + empty, chosen, zone, *span) == 0
    assert step_count.early_clock(read + empty, zone) == step_count.early_clock(
        read, zone
    )
    # The control: a morning that was food does count.
    food = [_row(db, owner, d, 6, 30) for d in days]
    assert step_count.count_days(read + food, chosen, zone, *span) == 5


# --- the logs -------------------------------------------------------------------------


MARK = "секретноеблюдо"


@pytest.mark.parametrize(
    "answer, fired",
    [
        ({"portion": "1 порция"}, "no per-100g basis"),
        ({"portion": "100г", "calories": None}, "no usable calories"),
        ({"portion": "100г"}, "enqueued"),
    ],
)
def test_the_learning_loop_logs_no_dish_and_no_portion(
    db, owner, reader, monkeypatch, caplog, answer, fired
):
    import celery_app.tasks.personal_food as personal_food

    monkeypatch.setattr(tg, "_schedule_personal_food_save", REAL_LEARNING)
    monkeypatch.setattr(
        personal_food.embed_and_save_personal_food, "delay", lambda **_k: None
    )
    reader.answer = {**NUTRITION, "foods": [MARK], **answer}
    with caplog.at_level(logging.DEBUG, logger=tg.logger.name):
        send(text(MARK))
    said = [r.getMessage() for r in caplog.records]
    assert any(fired in line for line in said)  # the path did run
    assert not [line for line in said if MARK in line or "порция" in line]


# --- the lines say the same on the way back -------------------------------------------


def _numbers_on(db, owner):
    owner.settings = {**(owner.settings or {}), "numbers_mode": "show"}
    db.commit()


def test_back_to_the_line_keeps_the_calories(db, owner, reader, monkeypatch):
    monkeypatch.setattr(meal_card, "shows_numbers", lambda _u: True)
    wire = send(text("рис"))
    meal = db.scalars(select(Meal)).one()
    said = wire.edits()[-1]["text"]
    assert "≈ 620 ккал" in said
    assert tap(f"ml:r:{meal.id}").edits()[-1]["text"] == said.split("\n")[0]


def test_back_to_a_draft_keeps_the_calories(db, owner, reader, monkeypatch):
    monkeypatch.setattr(meal_card, "shows_numbers", lambda _u: True)
    _drafting(db, owner)
    wire = send(text("рис"))
    meal = db.scalars(select(Meal)).one()
    said = wire.edits()[-1]["text"]
    assert "620" in said
    assert tap(f"ml:r:{meal.id}").edits()[-1]["text"] == said


def test_back_to_an_albums_line_keeps_its_count(db, owner, reader):
    album(shot("a"), shot("b"))
    meal = db.scalars(select(Meal)).one()
    back = tap(f"ml:r:{meal.id}").edits()[-1]["text"]
    assert back.startswith("Записано по 2 фото")


# --- only an estimate carries ≈ --------------------------------------------------------


def _exact(db, meal, source="label"):
    for item in crud_meal_item.live(db, meal.id):
        item.numbers_source = source
        item.calories = item.base_calories = 200.0
    meal.numbers_basis = "items"
    meal.calories = 200.0
    db.commit()
    db.expire_all()
    return db.get(Meal, meal.id)


@pytest.mark.parametrize(
    "source, shown",
    [
        ("barcode", "· 200 ккал"),
        ("label", "· 200 ккал"),
        ("user", "· 200 ккал"),
        ("vision", "· ≈ 200 ккал"),
    ],
)
def test_the_line_marks_only_an_estimate(db, owner, reader, monkeypatch, source, shown):
    monkeypatch.setattr(meal_card, "shows_numbers", lambda _u: True)
    reader.answer = _at("рис")
    send(text("рис"))
    meal = _exact(db, db.scalars(select(Meal)).one(), source)
    user = db.get(User, meal.user_id)
    assert shown in capture._record_text(db, meal, user)
    assert shown in tap(f"ml:r:{meal.id}").edits()[-1]["text"]


def test_one_estimate_among_exact_dishes_keeps_the_mark(db, owner, reader, monkeypatch):
    monkeypatch.setattr(meal_card, "shows_numbers", lambda _u: True)
    meal = _meal_now(db, owner, minutes_ago=5, names=("рис", "суп"))
    meal = _exact(db, meal)
    crud_meal_item.live(db, meal.id)[1].numbers_source = "vision"
    db.commit()
    db.expire_all()
    meal = db.get(Meal, meal.id)
    assert "≈ 200 ккал" in capture._record_text(db, meal, db.get(User, meal.user_id))


def test_the_day_and_week_totals_carry_the_mark_only_over_an_estimate(db, owner):
    exact = _exact(db, _meal_now(db, owner, minutes_ago=30, names=("рис",)))
    zone = capture.user_zone(owner)
    block = stats_screen._numbers_block([exact], zone)
    assert "≈" not in block and "200 ккал" in block
    assert "≈" not in stats_screen._week_numbers([(NOW.date(), [exact])])
    guess = _meal_now(db, owner, minutes_ago=20, names=("суп",))
    guess.calories = 100.0
    db.commit()
    both = stats_screen._numbers_block([exact, guess], zone)
    assert "За день: ≈" in both  # the total is as exact as its least exact part
    assert "≈" in stats_screen._week_numbers([(NOW.date(), [exact, guess])])


# --- what leaves for the model: no place in the picture --------------------------------


def _jpeg_with_gps() -> tuple:
    exif = b"Exif\x00\x00MM\x00*GPSLatitude=41.7"
    app1 = b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif
    app0_body = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    app0 = b"\xff\xe0" + struct.pack(">H", len(app0_body) + 2) + app0_body
    comment = b"\xff\xfe" + struct.pack(">H", 9) + b"secret!"
    scan = b"\xff\xda\x00\x02pixels\xff\xe1notamarker\xff\xd9"
    clean = b"\xff\xd8" + app0 + scan
    return b"\xff\xd8" + app0 + app1 + comment + scan, clean


def _chunk(kind: bytes, body: bytes) -> bytes:
    crc = struct.pack(">I", zlib.crc32(kind + body))
    return struct.pack(">I", len(body)) + kind + body + crc


def _png_with_gps() -> tuple:
    head = b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", b"\x00" * 13)
    tail = _chunk(b"IDAT", b"pixels") + _chunk(b"IEND", b"")
    meta = _chunk(b"eXIf", b"MM\x00*GPS") + _chunk(b"tEXt", b"GPS\x00here")
    return head + meta + tail, head + tail


def _webp_chunk(kind: bytes, body: bytes) -> bytes:
    pad = b"\x00" if len(body) % 2 else b""
    return kind + struct.pack("<I", len(body)) + body + pad


def _webp_with_gps() -> tuple:
    def riff(body):
        return b"RIFF" + struct.pack("<I", len(body) + 4) + b"WEBP" + body

    flagged = _webp_chunk(b"VP8X", bytes([0b00001100]) + b"\x00" * 9)
    plain = _webp_chunk(b"VP8X", bytes([0]) + b"\x00" * 9)
    picture = _webp_chunk(b"VP8 ", b"pixels")
    gps = _webp_chunk(b"EXIF", b"MM\x00*GPS") + _webp_chunk(b"XMP ", b"<gps/>")
    return riff(flagged + picture + gps), riff(plain + picture)


@pytest.mark.parametrize("make", [_jpeg_with_gps, _png_with_gps, _webp_with_gps])
def test_the_place_is_taken_out_and_the_picture_stays(make):
    dirty, clean = make()
    assert image_clean.clean(dirty) == clean
    assert image_clean.clean(clean) == clean


@pytest.mark.parametrize(
    "broken",
    [b"", b"\xff\xd8\xff", b"\xff\xd8\xff\xe1\xff\xff", b"GIF89a", b"not an image"],
)
def test_what_cannot_be_walked_is_never_sent_half_cut(broken):
    assert image_clean.clean(broken) == broken


@pytest.mark.parametrize(
    "mime, make",
    [("image/jpeg", _jpeg_with_gps), ("image/png", _png_with_gps)],
)
def test_a_file_reaches_the_model_without_its_place(
    db, owner, reader, monkeypatch, mime, make
):
    dirty, clean = make()
    monkeypatch.setattr(Wire, "file_bytes", dirty, raising=False)
    start = Wire.__init__

    def with_bytes(self):
        start(self)
        self.file_bytes = dirty

    monkeypatch.setattr(Wire, "__init__", with_bytes)
    send(_document(mime, 1000))
    head, body = reader.image[0]["payload"].split(",", 1)
    assert head == f"data:{mime};base64"
    assert base64.b64decode(body) == clean
    assert b"GPS" not in base64.b64decode(body)


# --- a file only what the model can read ------------------------------------------------


@pytest.mark.parametrize(
    "mime", ["image/heic", "image/tiff", "image/svg+xml", "image/bmp"]
)
def test_a_file_the_model_cannot_read_is_said_at_once(db, owner, reader, mime):
    wire = send(_document(mime, 1000))
    assert wire.seen() == [capture.M9_UNREADABLE]
    assert db.scalars(select(Meal)).all() == []
    assert reader.image == []


@pytest.mark.parametrize(
    "mime", ["image/jpeg", "image/png", "image/webp", "image/gif", "IMAGE/JPEG"]
)
def test_a_file_the_model_reads_is_a_photo(db, owner, reader, mime):
    send(_document(mime, 1000))
    assert len(reader.image) == 1
    assert len(db.scalars(select(Meal)).all()) == 1


# --- a line that did not reach the person ---------------------------------------------


def _line_row(db, meal_id):
    db.expire_all()
    return db.scalars(
        select(MealMessage).where(
            MealMessage.meal_id == meal_id, MealMessage.view.not_in(("source", "other"))
        )
    ).first()


def _sweep() -> Wire:
    box = {}

    async def steps(application, wire):
        box["wire"] = wire
        await tg._sweep(application)
        await _drained()

    asyncio.run(_with_app(steps))
    return box["wire"]


def _fresh_hints(db, owner):
    owner.settings = {
        k: v
        for k, v in (owner.settings or {}).items()
        if k not in ("capture_hint_seen", "voice_hint_seen")
    }
    db.commit()


def test_a_slow_telegram_does_not_lose_the_line(db, owner, reader, refuse):
    refuse.method, refuse.left = "editMessageText", 1
    wire = send(photo())
    assert refuse.tried == 1
    assert wire.edits()[-1]["text"].startswith("Записано")


@pytest.mark.parametrize(
    "error", [TimedOut("slow"), NetworkError("reset"), RetryAfter(0)]
)
def test_a_line_owed_is_shown_by_the_next_sweep(db, owner, reader, refuse, error):
    _fresh_hints(db, owner)
    refuse.method, refuse.left, refuse.error = "editMessageText", 99, error
    wire = send(photo())
    assert refuse.tried == tg.SHOW_TRIES
    assert wire.edits() == []
    meal = db.scalars(select(Meal)).one()
    assert meal.reading_status == "read"
    db.expire_all()
    # What the line would have said once is said later, not spent.
    assert not db.get(User, owner.id).settings.get("capture_hint_seen")
    refuse.left = 0
    later = _sweep()
    assert later.edits()[-1]["text"].startswith("Записано")
    assert _line_row(db, meal.id).view == "line"
    assert _sweep().edits() == []  # owed once


def test_a_failure_owed_is_said_too(db, owner, reader, refuse):
    reader.error = RuntimeError("down")
    refuse.method, refuse.left = "editMessageText", 99
    send(photo())
    refuse.left = 0
    assert _sweep().edits()[-1]["text"] == M7


def test_no_food_owed_on_words_is_said_with_its_button(db, owner, reader, refuse):
    reader.answer = dict(NOTHING)
    refuse.method, refuse.left = "editMessageText", 99
    send(text("как дела"))
    refuse.left = 0
    said = _sweep().edits()[-1]
    assert said["text"] == capture.M3A_NO_FOOD
    assert _buttons(said)[0].startswith("ml:nf:")


@pytest.mark.parametrize("words", ["Message to edit not found", "Forbidden-ish"])
def test_a_refusal_is_final_and_nothing_is_owed(db, owner, reader, refuse, words):
    _fresh_hints(db, owner)
    refuse.method, refuse.left, refuse.error = "editMessageText", 99, BadRequest(words)
    send(photo())
    assert refuse.tried == 1
    meal = db.scalars(select(Meal)).one()
    assert _line_row(db, meal.id).view == "line"
    db.expire_all()
    assert not db.get(User, owner.id).settings.get("capture_hint_seen")
    refuse.left = 0
    assert _sweep().edits() == []


def test_not_modified_is_no_failure(db, owner, reader, refuse):
    _fresh_hints(db, owner)
    refuse.method, refuse.left = "editMessageText", 1
    refuse.error = BadRequest("Message is not modified")
    send(photo())
    assert refuse.tried == 1
    db.expire_all()
    assert db.get(User, owner.id).settings.get("capture_hint_seen") is True


def test_a_hint_that_was_shown_is_spent(db, owner, reader):
    """The control of the hints: an edit that passed marks them for good."""
    _fresh_hints(db, owner)
    send(photo())
    db.expire_all()
    assert db.get(User, owner.id).settings.get("capture_hint_seen") is True


def test_a_first_line_refused_for_a_moment_still_goes_out(db, owner, reader, refuse):
    refuse.method, refuse.left = "sendMessage", 1
    wire = send(photo())
    assert wire.sent()[0]["text"] == capture.M1_PENDING
    assert wire.edits()[-1]["text"].startswith("Записано")


def test_no_first_line_at_all_and_the_read_still_answers(db, owner, reader, refuse):
    refuse.method, refuse.left = "sendMessage", tg.SHOW_TRIES
    wire = send(photo())
    assert len(wire.sent()) == 1 and wire.sent()[0]["text"].startswith("Записано")
    meal = db.scalars(select(Meal)).one()
    assert _line_row(db, meal.id).view == "line"


def test_no_first_line_and_a_failed_read_says_so(db, owner, reader, refuse):
    reader.error = RuntimeError("down")
    refuse.method, refuse.left = "sendMessage", tg.SHOW_TRIES
    wire = send(photo())
    assert [s["text"] for s in wire.sent()] == [M7]


def test_a_read_of_a_line_that_never_went_out_answers_after_a_restart(db, owner):
    meal = _pending_row(db, owner, minutes_ago=6)
    for row in db.scalars(
        select(MealMessage).where(
            MealMessage.meal_id == meal.id, MealMessage.view == "pending"
        )
    ):
        db.delete(row)
    db.commit()
    db.expire_all()
    job = capture._job_of(db, db.get(Meal, meal.id))
    assert job.unsent and job.line_id is None


def test_a_reply_line_refused_for_a_moment_still_goes_out(
    db, owner, reader, clock, refuse
):
    meal = _meal_now(db, owner, minutes_ago=5, names=("суп",))
    refuse.method, refuse.left = "sendMessage", 1
    wire = send(text("ещё сыр"))
    assert wire.sent()[-1]["text"].startswith("Добавлено")
    assert _item_names(db, meal) == ["суп", "сыр"]


def test_the_edit_answer_refused_for_a_moment_still_goes_out(db, owner, reader, refuse):
    message = text("рис")
    reader.answer = _at("рис")
    send(message)
    refuse.method, refuse.left = "sendMessage", 1
    assert edited({**message, "text": "гречка"}).sent()[-1]["text"] == M11


def test_a_split_with_no_line_still_answers_each_photo(db, owner, reader, refuse):
    album(shot("a"), shot("b"))
    db.expire_all()
    meal = db.scalars(select(Meal)).one()
    refuse.method, refuse.left = "sendMessage", tg.SHOW_TRIES
    wire = tap(f"ml:za:{meal.id}")
    assert refuse.left == 0
    said = [s["text"] for s in wire.sent() if s["text"].startswith("Записано")]
    edited_in = [e for e in wire.edits() if e["text"].startswith("Записано")]
    assert len(said) == 1  # the photo whose line never went out: a message of its own
    assert len(edited_in) >= 1  # the other, on its line


# --- the sweep: one meal's trouble stays its own --------------------------------------


def _flaky(monkeypatch, name, bad_id, times=1):
    real = getattr(capture, name)
    left = {"n": times}

    def flaky(session, meal_id, *args, **kw):
        if meal_id == bad_id and left["n"]:
            left["n"] -= 1
            raise OperationalError("SELECT", {}, Exception("db blip"))
        return real(session, meal_id, *args, **kw)

    monkeypatch.setattr(capture, name, flaky)


def _ids(db, *meals):
    return {_line_row(db, m.id).message_id for m in meals}


def test_a_meal_that_raises_in_the_sweep_does_not_take_the_others(
    db, owner, reader, at, monkeypatch
):
    a = _pending_row(db, owner, minutes_ago=6)
    b = _pending_row(db, owner, minutes_ago=6)
    c = _pending_row(db, owner, minutes_ago=6)
    _flaky(monkeypatch, "give_up", b.id)
    wire = _sweep()
    assert {e["message_id"] for e in wire.edits()} == _ids(db, a, c)
    at(seconds=60)
    assert {e["message_id"] for e in _sweep().edits()} == _ids(db, b)


def test_a_meal_that_raises_at_a_start_does_not_take_the_others(
    db, owner, reader, at, monkeypatch
):
    a = _pending_row(db, owner, minutes_ago=6)
    b = _pending_row(db, owner, minutes_ago=6)
    _flaky(monkeypatch, "automatic_read", a.id)

    async def steps(application, _wire):
        await tg._replay_pending(application, [a.id, b.id])
        await _drained()

    asyncio.run(_with_app(steps))
    assert _item_names(db, b) == ["рис", "курица", "овощи"]


def test_a_retry_that_fails_tells_a_line_that_never_said_it(
    db, owner, reader, at, refuse
):
    meal = _pending_row(db, owner, minutes_ago=6)
    refuse.method, refuse.left = "editMessageText", 99
    _sweep()
    refuse.left = 0
    db.expire_all()
    job = capture._job_of(db, db.get(Meal, meal.id))
    assert job.shown is None
    assert _line_row(db, meal.id).view == capture.OWED_VIEW
    row = _line_row(db, meal.id)
    row.view = "line"
    db.commit()
    db.expire_all()
    assert capture._job_of(db, db.get(Meal, meal.id)).shown == M7  # the control


# --- what was paid for and could not be written ---------------------------------------


def test_an_answer_that_cannot_be_written_is_not_paid_for_again(
    db, owner, reader, at, caplog
):
    reader.answer = {**NUTRITION, "calories": "около 300"}
    with caplog.at_level(logging.ERROR):
        wire = send(photo())
    assert wire.edits()[-1]["text"] == M7
    assert len(reader.image) == 1
    meal = db.scalars(select(Meal)).one()
    assert meal.reading_status == "failed"
    last = db.scalars(select(MealReading).order_by(MealReading.id.desc())).first()
    assert last.status == capture.FAILED
    assert last.output["calories"] == "около 300"  # what was paid for is kept
    assert not [r for r in caplog.records if "около" in r.getMessage() or r.exc_info]
    assert [r for r in caplog.records if "ValidationError" in r.getMessage()]
    meal.created_at = NOW
    db.commit()
    for move in ({"minutes": 6}, {"minutes": 61}, {"hours": 7}, {"hours": 7}):
        at(**move)
        _sweep()
    assert len(reader.image) == 1
