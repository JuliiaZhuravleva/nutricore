"""The failure lines M7, M9 and the readers' upkeep, through the real handlers.

Every test sends a real ``Update`` into ``create_bot_application()``. The model and the
download are the fakes: what is asserted is what would reach Telegram. No network, no
paid call.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from telegram import Update
from telegram.error import BadRequest

from app.core.config import settings
from app.crud.crud_meal_item import crud_meal_item
from app.models import InboundMessage, Meal, MealMessage, MealReading, User
from app.services import capture, food_groups_retag, food_lists, stats_screen
from app.services import telegram as tg
from tests.test_capture_router import (  # noqa: F401 (fixtures)
    CHAT,
    NOW,
    NOW_TS,
    NUTRITION,
    OWNER,
    Session,
    Wire,
    _document,
    _kb,
    _meals,
    _message,
    _sources,
    _voice,
    _with_app,
    db,
    owner,
    photo,
    reader,
    send,
    text,
)

M7 = "Фото сохранено, распознать еду сейчас не вышло.\nВ дневнике запись с этим фото."
M7A = (
    "Текст сохранён, разобрать еду сейчас не вышло.\nВ дневнике запись с этим текстом."
)
M4D = (
    "Голосовое сохранено, разобрать сейчас не вышло.\n"
    "В дневнике запись с этим голосовым."
)
M7B = "<b>✏️ Что было?</b>\nНапиши одним сообщением."
M7D = "Фото сохранено, еды на нём не видно.\nВ дневнике запись с этим фото."
M7E = (
    "Фото пн 26 октября, 13:20 больше недоступно в Telegram. "
    "Запись в дневнике осталась, можно написать, что было."
)
M7F = "Записать сейчас не вышло, сервер не отвечает. Пришли ещё раз."
M9 = (
    "Такое пока не читается, в дневник не записано. "
    "Подойдёт фото, текст или голосовое."
)
M9B = "Это кнопка старого меню, в дневник не записано. Новое меню внизу."
AGAIN_TOAST = "Читаю"
FAILED_TOAST = "Пока не вышло, запись сохранена"


@pytest.fixture(autouse=True)
def _a_clean_run(monkeypatch):
    """Whatever the machine's .env says, and no screen left from another test."""
    monkeypatch.setattr(settings, "CUSTOM_EMOJI", None)
    numbers = itertools.count(5000, 500)
    start = Wire.__init__

    def go_on(self):
        start(self)
        self.next_id = next(numbers)

    monkeypatch.setattr(Wire, "__init__", go_on)
    monkeypatch.setattr(tg._card_delivery, "_screens", {})


_taps = itertools.count(1)


def _tap_update(application, data: str, message_id: int) -> Update:
    message = _message()
    return Update.de_json(
        {
            "update_id": message["message_id"],
            "callback_query": {
                "id": str(next(_taps)),
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


def tap(data: str, message_id: int = 4900) -> Wire:
    box = {}

    async def steps(application, wire):
        box["wire"] = wire
        await application.process_update(_tap_update(application, data, message_id))
        await asyncio.gather(*list(tg._meal_reads))

    asyncio.run(_with_app(steps))
    return box["wire"]


def _toasts(wire) -> list:
    return [p.get("text") for n, p in wire.calls if n == "answerCallbackQuery"]


def _one(db) -> Meal:
    db.expire_all()
    return db.scalars(select(Meal).order_by(Meal.id)).first()


def _failed_photo(db, reader) -> Meal:
    reader.error = ValueError("boom")
    send(photo())
    meal = _one(db)
    assert meal.reading_status == "failed"
    reader.error = None
    reader.image.clear()
    reader.text.clear()
    return meal


def _line_id(db, meal) -> int:
    return db.scalar(
        select(MealMessage.message_id).where(
            MealMessage.meal_id == meal.id, MealMessage.view != "source"
        )
    )


def _dead_file(monkeypatch):
    async def gone(bot, file_id):
        raise BadRequest("Wrong file_id or the file is temporarily unavailable")

    monkeypatch.setattr(tg, "_download_image", gone)


# --- M7, M7a, M4d, M7d: what the line says, and where ml:r comes back to ------------


def test_m7_says_it_exactly_with_both_buttons(db, reader):
    reader.error = ValueError("boom")
    wire = send(photo())
    meal = _one(db)
    assert wire.edits()[-1]["text"] == M7
    assert _kb(wire.edits()[-1]) == [[f"ml:rr:{meal.id}:r", f"ml:w:{meal.id}:r"]]


def test_m7a_says_it_exactly_and_offers_to_write_otherwise(db, reader):
    reader.error = ValueError("boom")
    wire = send(text("что-то непонятное"))
    meal = _one(db)
    assert wire.edits()[-1]["text"] == M7A
    keyboard = wire.edits()[-1]["reply_markup"]
    assert "✏️ Написать иначе" in str(keyboard)
    assert _kb(wire.edits()[-1]) == [[f"ml:rr:{meal.id}:r", f"ml:w:{meal.id}:r"]]


@pytest.mark.parametrize(
    "make, copy",
    [
        (lambda: photo(), M7),
        (lambda: text("что-то непонятное"), M7A),
        (lambda: _voice(), M4D),
    ],
)
def test_back_to_the_answer_is_the_failure_not_an_empty_record(db, reader, make, copy):
    reader.error = ValueError("boom")
    send(make())
    meal = _one(db)
    wire = tap(f"ml:r:{meal.id}")
    assert wire.edits()[-1]["text"] == copy
    assert _kb(wire.edits()[-1]) == [[f"ml:rr:{meal.id}:r", f"ml:w:{meal.id}:r"]]


def test_back_to_the_answer_after_no_food_is_m7d(db, reader):
    reader.answer = {"foods": [], "calories": 0}
    send(photo())
    meal = _one(db)
    assert meal.reading_status == "no_food"
    wire = tap(f"ml:r:{meal.id}")
    assert wire.edits()[-1]["text"] == M7D
    assert _kb(wire.edits()[-1]) == [[f"ml:w:{meal.id}:r", f"ml:d:{meal.id}:r"]]


def test_back_to_the_answer_after_a_read_is_the_record(db, reader):
    # Control: the record's line did not change.
    send(photo())
    meal = _one(db)
    wire = tap(f"ml:r:{meal.id}")
    assert wire.edits()[-1]["text"].startswith("Записано: рис")


def test_back_to_the_answer_while_reading_leaves_the_line_the_reads(db, owner, reader):
    meal = Meal(
        user_id=owner.id,
        meal_time=NOW,
        sent_at=NOW,
        photos=["big"],
        reading_status="pending",
        numbers_basis="none",
    )
    db.add(meal)
    db.commit()
    wire = tap(f"ml:r:{meal.id}", message_id=4901)
    assert wire.edits()[-1]["text"] == "Фото в дневнике, читаю…"
    view = db.scalar(select(MealMessage.view).where(MealMessage.message_id == 4901))
    assert view == "pending"


# --- 🔁 Ещё раз ---------------------------------------------------------------------


def test_again_reads_toasts_and_edits_the_tapped_line(db, reader):
    meal = _failed_photo(db, reader)
    line = _line_id(db, meal)
    wire = tap(f"ml:rr:{meal.id}:r", message_id=line)
    assert _toasts(wire) == [AGAIN_TOAST]
    assert len(reader.image) == 1
    shown = wire.edits()
    assert shown[0]["text"] == "Фото в дневнике, читаю…"
    assert shown[-1]["text"].startswith("Записано: рис")
    assert all(p["message_id"] == line for p in shown)
    meal = _one(db)
    assert meal.reading_status == "read"
    assert meal.read_attempts == 1
    assert [r.status for r in db.scalars(select(MealReading))] == ["failed", "done"]


def test_again_that_fails_brings_m7_back_and_says_so(db, reader):
    meal = _failed_photo(db, reader)
    reader.error = ValueError("boom")
    wire = tap(f"ml:rr:{meal.id}:r", message_id=_line_id(db, meal))
    assert _toasts(wire) == [AGAIN_TOAST, FAILED_TOAST]
    assert [p["text"] for p in wire.edits()] == ["Фото в дневнике, читаю…", M7]
    assert _kb(wire.edits()[-1]) == [[f"ml:rr:{meal.id}:r", f"ml:w:{meal.id}:r"]]
    db.expire_all()
    assert _one(db).reading_status == "failed"


def test_a_refused_second_toast_breaks_nothing(db, reader, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    # ASSUMED that Telegram may answer one query once: the line is the guarantee.
    meal = _failed_photo(db, reader)
    reader.error = ValueError("boom")
    line = _line_id(db, meal)
    real = Wire.do_request
    seen = []

    async def refusing(self, url, method, request_data=None, **kwargs):
        if url.endswith("answerCallbackQuery"):
            seen.append(url)
            if len(seen) > 1:
                body = {"ok": False, "error_code": 400, "description": "query too old"}
                return 400, json.dumps(body).encode()
        return await real(self, url, method, request_data, **kwargs)

    monkeypatch.setattr(Wire, "do_request", refusing)
    wire = tap(f"ml:rr:{meal.id}:r", message_id=line)
    assert len(seen) == 2  # the breakage did apply
    assert wire.edits()[-1]["text"] == M7
    assert _one(db).reading_status == "failed"
    assert "read crashed" not in caplog.text
    assert "Second toast refused" in caplog.text


def test_a_double_tap_pays_once(db, reader):
    meal = _failed_photo(db, reader)
    line = _line_id(db, meal)
    reader.error = ValueError("boom")
    box = {}

    async def steps(application, wire):
        box["wire"] = wire
        reader.gate = asyncio.Event()
        for _ in range(2):
            await application.process_update(
                _tap_update(application, f"ml:rr:{meal.id}:r", line)
            )
        await asyncio.sleep(0)
        reader.gate.set()
        await asyncio.gather(*list(tg._meal_reads))

    asyncio.run(_with_app(steps))
    assert len(reader.image) == 1  # the guard
    assert _toasts(box["wire"])[:2] == [AGAIN_TOAST, AGAIN_TOAST]
    assert _one(db).read_attempts == 1
    # Known positive: with that read over, a later tap does read again.
    assert _one(db).reading_status == "failed"
    tap(f"ml:rr:{meal.id}:r", message_id=line)
    assert len(reader.image) == 2


def test_a_tap_holds_the_read_against_the_sweep(db, reader, monkeypatch):
    """The meal is hours old and pending again: before its task runs, it is not stuck."""
    meal = _failed_photo(db, reader)
    meal.created_at = NOW - timedelta(hours=3)
    db.commit()
    seen = {}
    deliver = tg._card_delivery.tap

    async def looking(query, screen):
        with tg.SessionLocal() as session:
            seen["stuck"] = capture.stuck_meals(session, tg._live_reads)
        return await deliver(query, screen)

    monkeypatch.setattr(tg._card_delivery, "tap", looking)
    monkeypatch.setattr(capture, "_now", lambda: NOW)
    tap(f"ml:rr:{meal.id}:r", message_id=_line_id(db, meal))
    assert seen["stuck"] == []
    # Known positive: pending and held by nothing is what the sweep picks up.
    meal = _one(db)
    meal.reading_status = "pending"
    db.commit()
    with tg.SessionLocal() as session:
        assert capture.stuck_meals(session, tg._live_reads) == [meal.id]


def test_again_on_a_meal_already_read_pays_nothing(db, reader):
    send(photo())
    meal = _one(db)
    reader.image.clear()
    wire = tap(f"ml:rr:{meal.id}:r")
    assert reader.image == []
    assert wire.edits()[-1]["text"].startswith("Записано: рис")


def test_again_on_a_deleted_meal_is_rule_s(db, reader):
    meal = _failed_photo(db, reader)
    tap(f"ml:d:{meal.id}:r")
    assert _one(db).deleted_at is not None
    wire = tap(f"ml:rr:{meal.id}:r", message_id=4902)
    assert reader.image == []
    assert "Этого приёма уже нет" in wire.seen()[-1]


def test_again_on_someone_elses_meal_reads_nothing(db, reader):
    meal = _failed_photo(db, reader)
    other = User(telegram_id=777, settings={})
    db.add(other)
    db.commit()
    meal.user_id = other.id
    db.commit()
    tap(f"ml:rr:{meal.id}:r")
    assert reader.image == []
    assert _one(db).reading_status == "failed"


def test_again_on_a_voice_nobody_can_hear_asks_no_model(db, reader):
    send(_voice())
    meal = _one(db)
    wire = tap(f"ml:rr:{meal.id}:r", message_id=_line_id(db, meal))
    assert reader.text == [] and reader.image == []
    assert [p["text"] for p in wire.edits()] == ["Голосовое в дневнике, слушаю…", M4D]
    assert _toasts(wire) == [AGAIN_TOAST, FAILED_TOAST]


def test_again_on_a_text_keeps_the_words(db, reader):
    reader.error = ValueError("boom")
    send(text("овсянка на воде"))
    meal = _one(db)
    reader.error = None
    reader.text.clear()
    tap(f"ml:rr:{meal.id}:r", message_id=_line_id(db, meal))
    assert reader.text == ["овсянка на воде"]
    assert _one(db).reading_status == "read"


# --- M7e ---------------------------------------------------------------------------


def test_a_dead_file_on_a_retry_says_m7e_with_one_button(db, reader, monkeypatch):
    meal = _failed_photo(db, reader)
    _dead_file(monkeypatch)
    wire = tap(f"ml:rr:{meal.id}:r", message_id=_line_id(db, meal))
    assert wire.edits()[-1]["text"] == M7E
    assert _kb(wire.edits()[-1]) == [[f"ml:w:{meal.id}:r"]]
    assert reader.image == []  # no model was asked about a file that is not there
    db.expire_all()
    reading = db.scalars(select(MealReading).order_by(MealReading.id.desc())).first()
    assert (reading.status, reading.error) == (capture.FAILED, "DeadFile")
    # And it stays what the line says on the way back from ✏️.
    assert tap(f"ml:r:{meal.id}").edits()[-1]["text"] == M7E


def test_a_dead_file_is_not_retried_by_itself(db, reader, monkeypatch):
    meal = _failed_photo(db, reader)
    _dead_file(monkeypatch)
    tap(f"ml:rr:{meal.id}:r", message_id=_line_id(db, meal))
    monkeypatch.setattr(capture, "_now", lambda: NOW + timedelta(hours=7))
    with tg.SessionLocal() as session:
        assert capture.due_retries(session, 5) == []


def test_any_other_model_refusal_stays_m7(db, reader):
    # Control: a BadRequest from past the download is not a dead file.
    meal = _failed_photo(db, reader)
    reader.error = BadRequest("the model said no")
    wire = tap(f"ml:rr:{meal.id}:r", message_id=_line_id(db, meal))
    assert wire.edits()[-1]["text"] == M7


def test_the_first_read_of_a_dead_file_is_still_m7(db, reader, monkeypatch):
    # «Dead file id on retry»: the first answer is M7, one sentence for all of them.
    _dead_file(monkeypatch)
    wire = send(photo())
    assert wire.edits()[-1]["text"] == M7


# --- M7b, M7c: ✏️ Написать --------------------------------------------------------


def _writing(db, meal, words: str, *, message_id=None) -> Wire:
    """✏️ on the line, then the words: one application, as in the chat."""
    box = {}
    line = message_id or _line_id(db, meal)

    async def steps(application, wire):
        box["wire"] = wire
        await application.process_update(
            _tap_update(application, f"ml:w:{meal.id}:r", line)
        )
        message = text(words)
        await application.process_update(
            Update.de_json(
                {"update_id": message["message_id"], "message": message},
                application.bot,
            )
        )
        await asyncio.gather(*list(tg._meal_reads), *list(tg._typed_fills))

    asyncio.run(_with_app(steps))
    return box["wire"]


def test_write_asks_on_the_same_message(db, reader):
    meal = _failed_photo(db, reader)
    line = _line_id(db, meal)
    wire = tap(f"ml:w:{meal.id}:r", message_id=line)
    shown = wire.edits()[-1]
    assert shown["text"] == M7B and shown["message_id"] == line
    assert _kb(shown) == [[f"ml:r:{meal.id}"]]
    db.expire_all()
    ask = db.query(User).filter_by(telegram_id=OWNER).one().settings["ask"]
    assert ask["kind"] == "meal_text" and ask["meal_id"] == meal.id


def test_back_from_write_closes_the_ask_and_food_is_food_again(db, reader):
    meal = _failed_photo(db, reader)
    tap(f"ml:w:{meal.id}:r", message_id=_line_id(db, meal))
    wire = tap(f"ml:r:{meal.id}")
    assert wire.edits()[-1]["text"] == M7
    send(text("суп"))
    assert len(_meals(db)) == 2


def test_written_words_become_the_reading_and_the_photo_stays(db, reader):
    meal = _failed_photo(db, reader)
    wire = _writing(db, meal, "суп")
    answer = wire.sent()[-1]
    assert answer["text"] == "Записано: суп. Фото хранится при этой записи."
    db.expire_all()
    fresh = _one(db)
    item = crud_meal_item.live(db, fresh.id)[0]
    assert _kb(answer) == [[f"ml:i:{item.id}:r", f"ml:t:{fresh.id}:r"]]
    assert fresh.reading_status == "read" and fresh.photos == ["big"]
    assert item.name == "суп"
    assert len(_meals(db)) == 1  # the words are no new meal
    reading = db.scalars(select(MealReading).order_by(MealReading.id.desc())).first()
    assert (reading.kind, reading.status, reading.applied) == ("text", "done", True)
    kinds = [s.kind for s in _sources(db)]
    assert kinds == ["photo", "text"]
    assert reading.source_ids == [_sources(db)[1].id]


def test_written_words_retire_the_ask_and_map_the_answer(db, reader):
    meal = _failed_photo(db, reader)
    line = _line_id(db, meal)
    wire = _writing(db, meal, "суп")
    assert any(
        n == "editMessageReplyMarkup" and p["message_id"] == line for n, p in wire.calls
    )
    answer_id = wire.next_id
    db.expire_all()
    row = db.scalar(select(MealMessage).where(MealMessage.message_id == answer_id))
    assert (row.meal_id, row.view) == (meal.id, "line")


def test_written_words_are_not_paid_for_again_by_reprocess(db, reader):
    meal = _failed_photo(db, reader)
    _writing(db, meal, "суп")
    reader.text.clear()
    reader.image.clear()
    wire = send(_reprocess())
    assert reader.image == []
    assert wire.seen()[-1] == "Нет сообщений для повторной обработки. 👍"
    assert _one(db).reading_status == "read"


def test_written_words_get_their_numbers_in_the_background(db, reader):
    meal = _failed_photo(db, reader)
    reader.answer = {
        "foods": ["суп"],
        "calories": 120.0,
        "protein": 1,
        "fats": 1,
        "carbs": 1,
    }
    _writing(db, meal, "суп")
    assert reader.text == ["суп"]
    db.expire_all()
    assert _one(db).calories == 120.0


def test_written_words_replace_the_words_that_failed(db, reader):
    # ✏️ Написать иначе: the failed text is no dish beside the new one.
    reader.error = ValueError("boom")
    send(text("что-то непонятное"))
    meal = _one(db)
    reader.error = None
    wire = _writing(db, meal, "суп")
    assert wire.sent()[-1]["text"] == "Записано: суп."
    db.expire_all()
    assert [i.name for i in crud_meal_item.live(db, meal.id)] == ["суп"]
    assert _one(db).reading_status == "read"


def test_written_words_on_a_voice_keep_the_voice(db, reader):
    send(_voice())
    meal = _one(db)
    wire = _writing(db, meal, "суп")
    assert wire.sent()[-1]["text"] == "Записано: суп."
    assert [s.kind for s in _sources(db)] == ["voice", "text"]


def test_a_late_read_does_not_undo_written_words(db, reader):
    meal = _failed_photo(db, reader)
    job = None
    with tg.SessionLocal() as session:
        # As 🔁 Ещё раз leaves it: the line says «читаю», so a failure would be news.
        job = replace(capture.read_again(session, meal.id), shown="читаю")
    # ✏️ on the card, so the line is still the read's to claim: only the status guards.
    _writing(db, meal, "суп", message_id=4970)
    with tg.SessionLocal() as session:
        done = capture.read_failed(session, job, "ValueError")
    assert done.line is None
    db.expire_all()
    assert _one(db).reading_status == "read"
    # Known positive: the same failure on a meal still pending does say so.
    meal.reading_status = "pending"
    db.commit()
    with tg.SessionLocal() as session:
        capture.read_failed(session, job, "ValueError")
    db.expire_all()
    assert _one(db).reading_status == "failed"


def test_written_words_on_a_deleted_meal_are_rule_s(db, reader):
    meal = _failed_photo(db, reader)
    box = {}

    async def steps(application, wire):
        box["wire"] = wire
        await application.process_update(
            _tap_update(application, f"ml:w:{meal.id}:r", _line_id(db, meal))
        )
        fresh = _one(db)
        fresh.deleted_at = NOW
        db.commit()
        message = text("суп")
        await application.process_update(
            Update.de_json(
                {"update_id": message["message_id"], "message": message},
                application.bot,
            )
        )

    asyncio.run(_with_app(steps))
    assert "Этого приёма уже нет" in box["wire"].sent()[-1]["text"]


def test_a_photo_during_the_ask_is_a_new_meal(db, reader):
    meal = _failed_photo(db, reader)
    tap(f"ml:w:{meal.id}:r", message_id=_line_id(db, meal))
    send(photo())
    assert len(_meals(db)) == 2


def test_m7d_written_words_too(db, reader):
    reader.answer = {"foods": [], "calories": 0}
    send(photo())
    meal = _one(db)
    reader.answer = dict(NUTRITION)
    wire = _writing(db, meal, "суп")
    assert wire.sent()[-1]["text"] == "Записано: суп. Фото хранится при этой записи."
    assert _one(db).reading_status == "read"


# --- N1d ------------------------------------------------------------------------


def test_the_card_of_an_unread_photo_has_all_of_n1d(db, reader):
    meal = _failed_photo(db, reader)
    wire = tap(f"ml:o:{meal.id}:r")
    shown = wire.edits()[-1]
    assert shown["text"] == (
        "<b>🍽 Приём, пн 26 октября, 13:20</b>\n"
        "Фото сохранено, распознать еду пока не вышло.\n"
        "В дневнике запись с этим фото."
    )
    assert _kb(shown) == [
        [f"ml:rr:{meal.id}:r", f"ml:w:{meal.id}:r"],
        # ``c``: its time screen goes back to the card, not to the line.
        [f"ml:t:{meal.id}:c", f"ml:d:{meal.id}:r"],
        [f"ml:r:{meal.id}"],
    ]


def test_again_from_the_card_edits_that_message(db, reader):
    meal = _failed_photo(db, reader)
    wire = tap(f"ml:rr:{meal.id}:t", message_id=4950)
    assert all(p["message_id"] == 4950 for p in wire.edits())
    assert wire.edits()[-1]["text"].startswith("Записано: рис")
    assert _one(db).reading_status == "read"


def test_the_card_of_a_failed_text_offers_both_too(db, reader):
    reader.error = ValueError("boom")
    send(text("что-то непонятное"))
    meal = _one(db)
    wire = tap(f"ml:o:{meal.id}:r")
    assert [f"ml:rr:{meal.id}:r", f"ml:w:{meal.id}:r"] in _kb(wire.edits()[-1])


def test_the_card_of_a_read_meal_has_no_again(db, reader):
    # Control.
    send(photo())
    meal = _one(db)
    wire = tap(f"ml:o:{meal.id}:r")
    assert not any("ml:rr" in cell for row in _kb(wire.edits()[-1]) for cell in row)


# --- M3a, M3b: 🍽 Это еда ----------------------------------------------------------


def test_it_is_food_records_the_words_at_sent_at(db, reader):
    reader.answer = {"foods": [], "calories": 0}
    wire = send(text("кошка на окне сидит"))
    source = _sources(db)[0]
    assert source.meal_id is None
    edited = wire.edits()[-1]
    assert _kb(edited) == [[f"ml:nf:{source.id}"]]
    reader.answer = dict(NUTRITION)
    reader.text.clear()
    wire = tap(f"ml:nf:{source.id}", message_id=4960)
    db.expire_all()
    meal = db.scalars(
        select(Meal).where(Meal.deleted_at.is_(None)).order_by(Meal.id)
    ).one()
    assert wire.edits()[-1]["text"] == "Записано: кошка на окне сидит."
    assert [i.name for i in crud_meal_item.live(db, meal.id)] == ["кошка на окне сидит"]
    assert meal.reading_status == "read"
    assert meal.meal_time.replace(tzinfo=None) == NOW.replace(tzinfo=None)
    # The model that said no food is not asked whether it is food: only its numbers.
    assert reader.text == ["кошка на окне сидит"]
    # A second tap is the same meal.
    again = tap(f"ml:nf:{source.id}", message_id=4961)
    assert again.edits()[-1]["text"].startswith("Записано: кошка")
    db.expire_all()
    assert db.query(Meal).filter(Meal.deleted_at.is_(None)).count() == 1


def test_it_is_food_on_someone_elses_source_records_nothing(db, reader):
    reader.answer = {"foods": [], "calories": 0}
    send(text("кошка на окне сидит"))
    source = _sources(db)[0]
    other = User(telegram_id=777, settings={})
    db.add(other)
    db.commit()
    source.user_id = other.id
    db.commit()
    tap(f"ml:nf:{source.id}")
    db.expire_all()
    assert db.query(Meal).filter(Meal.deleted_at.is_(None)).count() == 0


# --- M7f --------------------------------------------------------------------------


def test_m7f_when_the_save_itself_fails(db, reader, monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("the database is down")

    monkeypatch.setattr(capture, "born", broken)
    wire = send(photo())
    assert [p["text"] for p in wire.sent()] == [M7F]
    assert (
        "reply_markup" not in wire.sent()[0] or wire.sent()[0]["reply_markup"] is None
    )
    assert _meals(db) == [] and db.scalars(select(InboundMessage)).all() == []
    assert reader.image == []


def test_m7f_when_the_router_fails(db, reader, monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("the database is down")

    monkeypatch.setattr(capture, "route_text", broken)
    wire = send(text("суп"))
    assert [p["text"] for p in wire.sent()] == [M7F]
    assert _meals(db) == []


def test_m7f_resent_is_one_meal(db, reader, monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("the database is down")

    with monkeypatch.context() as down:
        down.setattr(capture, "born", broken)
        send(text("суп"))
    assert _meals(db) == [] and _sources(db) == []
    send(text("суп"))
    assert len(_meals(db)) == 1 and len(_sources(db)) == 1


def test_born_first_model_and_download_raising_is_m7_with_the_row(
    db, reader, monkeypatch
):
    async def no_download(bot, file_id):
        raise OSError("no network")

    monkeypatch.setattr(tg, "_download_image", no_download)
    reader.error = RuntimeError("no model")
    wire = send(photo())
    assert wire.edits()[-1]["text"] == M7
    assert len(_meals(db)) == 1 and len(_sources(db)) == 1


# --- M9, M9b -----------------------------------------------------------------------

UNREADABLE = {
    "video": lambda: _message(
        video={
            "file_id": "x",
            "file_unique_id": "x",
            "width": 1,
            "height": 1,
            "duration": 1,
        }
    ),
    "video_note": lambda: _message(
        video_note={"file_id": "x", "file_unique_id": "x", "length": 1, "duration": 1}
    ),
    "audio": lambda: _message(
        audio={"file_id": "x", "file_unique_id": "x", "duration": 1}
    ),
    "file": lambda: _document("application/pdf"),
    "sticker": lambda: _message(
        sticker={
            "file_id": "x",
            "file_unique_id": "x",
            "type": "regular",
            "width": 1,
            "height": 1,
            "is_animated": False,
            "is_video": False,
        }
    ),
    "location": lambda: _message(location={"latitude": 1.0, "longitude": 1.0}),
}


@pytest.mark.parametrize("kind", sorted(UNREADABLE))
def test_m9_answers_what_cannot_be_read_and_keeps_nothing(db, reader, kind):
    wire = send(UNREADABLE[kind]())
    assert [p["text"] for p in wire.sent()] == [M9]
    assert _meals(db) == [] and _sources(db) == []
    assert db.scalars(select(InboundMessage)).all() == []
    assert reader.image == [] and reader.text == []


def test_an_image_file_is_not_m9(db, reader):
    # Control.
    wire = send(_document("image/jpeg"))
    assert M9 not in wire.seen()
    assert len(_meals(db)) == 1


@pytest.mark.parametrize("label", sorted(capture.STALE_LABELS))
def test_m9b_answers_an_old_label_with_the_main_keyboard(db, reader, label):
    wire = send(text(label))
    assert [p["text"] for p in wire.sent()] == [M9B]
    assert "keyboard" in str(wire.sent()[0]["reply_markup"])
    assert _meals(db) == [] and reader.text == []


# --- /reprocess fills the meal (TD-010) --------------------------------------------


def _reprocess() -> dict:
    return text(
        "/reprocess", entities=[{"type": "bot_command", "offset": 0, "length": 10}]
    )


def test_reprocess_fills_a_failed_photo_and_edits_its_line(db, reader):
    meal = _failed_photo(db, reader)
    wire = send(_reprocess())
    db.expire_all()
    assert _one(db).reading_status == "read"
    assert crud_meal_item.live(db, meal.id)
    assert len(reader.image) == 1
    assert any(p["text"].startswith("Записано: рис") for p in wire.edits())
    assert db.scalars(select(InboundMessage)).one().status == "analyzed"
    assert "успешно 1" in wire.seen()[-1]


def test_reprocess_asks_nothing_for_a_deleted_meals_row(db, reader):
    meal = _failed_photo(db, reader)
    tap(f"ml:d:{meal.id}:r")
    wire = send(_reprocess())
    assert reader.image == []
    assert db.scalars(select(InboundMessage)).one().status == "analyzed"
    assert wire.seen()[-1] == "Нет сообщений для повторной обработки. 👍"


def test_reprocess_sends_no_blank_row_to_the_text_model(db, reader):
    db.add(InboundMessage(telegram_id=OWNER, kind="voice", content="", status="failed"))
    db.add(
        InboundMessage(telegram_id=OWNER, kind="text", content="  ", status="failed")
    )
    db.commit()
    wire = send(_reprocess())
    assert reader.text == []
    # And they leave the queue: a blank row must not fill the batch for ever.
    db.expire_all()
    assert {row.status for row in db.scalars(select(InboundMessage))} == {"analyzed"}
    assert wire.seen()[-1] == "Нет сообщений для повторной обработки. 👍"


def test_reprocess_still_replays_a_legacy_row_with_no_meal(db, reader):
    # Known positive of the two guards above: a row with words and no meal does go.
    db.add(
        InboundMessage(telegram_id=OWNER, kind="text", content="суп", status="failed")
    )
    db.commit()
    wire = send(_reprocess())
    assert reader.text == ["суп"]
    assert "успешно 1" in wire.seen()[-1]
    assert _meals(db) == []  # written back to its row, no meal out of an old message


def test_reprocess_leaves_the_row_of_a_meal_beyond_the_batch(db, reader, monkeypatch):
    monkeypatch.setattr(tg, "REPROCESS_BATCH_LIMIT", 1)
    reader.error = ValueError("boom")
    send(text("суп"), text("борщ"))
    reader.error = None
    reader.text.clear()
    send(_reprocess())
    assert len(reader.text) == 1
    db.expire_all()
    statuses = sorted(row.status for row in db.scalars(select(InboundMessage)))
    assert statuses == ["analyzed", "failed"]
    send(_reprocess())
    assert len(reader.text) == 2
    assert {m.reading_status for m in _meals(db)} == {"read"}


# --- the readers ------------------------------------------------------------------


def _today(db):
    """The statistics screen reads the wall clock: the meals move to it."""
    for meal in _meals(db):
        meal.meal_time = meal.sent_at = datetime.now(UTC)
    db.commit()


def test_the_list_of_the_day_says_it_of_a_failed_photo(db, reader):
    _failed_photo(db, reader)
    wire = tap("ml:dl:20261026:t")
    assert "13:20 · 📷 фото сохранено" in wire.edits()[-1]["text"]


def test_a_failed_only_day_never_reads_0(db, reader):
    _failed_photo(db, reader)
    _today(db)
    shown = tap("st:tn").seen()[-1]
    assert "Не посчитано приёмов: 1" in shown
    assert "ккал" not in shown and "Б 0" not in shown


def test_the_day_counts_the_read_one_beside_the_failed_one(db, reader):
    # Control: the same day with a read meal does say its calories.
    send(photo())
    reader.error = ValueError("boom")
    send(text("что-то непонятное"))
    _today(db)
    shown = tap("st:tn").seen()[-1]
    assert "620 ккал" in shown and "Не посчитано приёмов: 1" in shown


def _unread_row(db, owner, status: str, *, description="суп", photos=None) -> Meal:
    meal = Meal(
        user_id=owner.id,
        meal_time=NOW - timedelta(days=1),
        sent_at=NOW - timedelta(days=1),
        photos=photos or [],
        description=description,
        reading_status=status,
        numbers_basis="none",
    )
    db.add(meal)
    db.commit()
    return meal


@pytest.mark.parametrize("status", ["pending", "failed", "no_food"])
def test_the_list_seed_skips_what_was_never_read(db, owner, status):
    for _ in range(4):
        _unread_row(db, owner, status)
    food_lists.seed(db, owner, now=NOW)
    assert food_lists.counts(owner)[0] == 0


def test_the_list_seed_counts_what_was_read(db, owner):
    # Known positive: the same rows, read, do repeat.
    for _ in range(4):
        _unread_row(db, owner, "read")
    food_lists.seed(db, owner, now=NOW)
    assert food_lists.counts(owner)[0] == 1


def test_the_list_seed_counts_a_dish_the_person_wrote_on_an_unread_meal(db, owner):
    for _ in range(4):
        meal = _unread_row(db, owner, "failed", description=None)
        crud_meal_item.add(db, meal_id=meal.id, name="суп")
    db.commit()
    food_lists.seed(db, owner, now=NOW)
    assert food_lists.counts(owner)[0] == 1


@pytest.mark.parametrize("status", ["pending", "failed", "no_food"])
def test_the_retag_pays_for_no_unread_meal(db, owner, status):
    _unread_row(db, owner, status)
    assert food_groups_retag.pending(db) == []


def test_the_retag_takes_a_read_meal(db, owner):
    # Known positive.
    meal = _unread_row(db, owner, "read")
    meal.food_groups = None
    db.commit()
    assert [row.id for row in food_groups_retag.pending(db)] == [meal.id]


# --- the picture, and the logs ----------------------------------------------------


def _picture_today(db) -> str:
    screen = stats_screen.open_view(db, OWNER, stats_screen.TODAY, datetime.now(UTC))
    return json.dumps(screen.picture, ensure_ascii=False)


def test_the_picture_of_a_failed_only_day_has_no_0(db, reader):
    _failed_photo(db, reader)
    _today(db)
    picture = _picture_today(db)
    assert "ккал" not in picture and "kcal" not in picture
    assert '"foods": null' in picture


def test_the_picture_of_a_read_day_has_its_number(db, reader):
    # Known positive: a read day does fill what the failed day leaves empty.
    send(photo())
    _today(db)
    assert '"foods": "Рис, курица, овощи"' in _picture_today(db)


def test_no_message_body_reaches_a_log(db, reader, caplog):
    # INFO, as in production; the library's own DEBUG dumps every call, the app's must not.
    caplog.set_level(logging.INFO)
    caplog.set_level(logging.DEBUG, logger="app")
    secret = "словосекретное"
    reader.error = ValueError("boom")
    send(text(secret))
    meal = _one(db)
    reader.error = None
    tap(f"ml:rr:{meal.id}:r", message_id=_line_id(db, meal))
    reader.error = ValueError("boom")
    second = text(f"{secret} два")
    send(second)
    other = _meals(db)[-1]
    reader.error = None
    _writing(db, other, f"{secret} три")
    send(_reprocess())
    assert secret not in caplog.text
    # Known positive: the capture did log, and the body did travel through the fake.
    assert "not read: ValueError" in caplog.text
    assert any(secret in asked for asked in reader.text)


def test_a_database_refusal_does_not_log_the_words(db, reader, monkeypatch, caplog):
    secret = "словосекретное"
    meal = _failed_photo(db, reader)
    refusal = IntegrityError("INSERT", {"name": secret}, Exception("no"))
    assert secret in str(refusal)  # known positive: this error does carry them

    def refused(*_args, **_kwargs):
        raise refusal

    monkeypatch.setattr(capture.meal_items, "add_item", refused)
    wire = _writing(db, meal, secret)
    assert wire.sent()[-1]["text"] == "Не получилось, попробуй ещё раз"
    assert "Meal slot failed: IntegrityError" in caplog.text
    assert secret not in caplog.text
