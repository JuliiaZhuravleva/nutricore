"""Albums, M2, M2a, M2b, through the real handlers.

Every test sends a real ``Update`` into ``create_bot_application()``. The model and the
download are fakes, so what is asserted is what would reach Telegram and what the model
would be shown. No network, no paid call. Real Telegram albums were not run.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event, select
from telegram import Update
from telegram.error import BadRequest

import app.services.product_lookup_service as pls
from app.core.config import settings
from app.crud.crud_meal_item import crud_meal_item
from app.models import Meal, MealItem, MealMessage, MealReading, MealSource, RecordEvent
from app.services import close_row
from app.services import capture, meal_items
from app.services import telegram as tg
from app.services.openai_service import OpenAIService
from tests.test_capture_failures import M7, _tap_update, _toasts
from tests.test_capture_router import (  # noqa: F401 (fixtures)
    CHAT,
    NOW,
    NOW_TS,
    NUTRITION,
    OWNER,
    Session,
    Wire,
    _kb,
    _meals,
    _message,
    _sources,
    _with_app,
    db,
    owner,
    photo,
    reader,
    send,
)

SOUP = {**NUTRITION, "foods": ["суп", "хлеб", "йогурт"]}
M2A = "Записано по 3 фото: суп, хлеб и йогурт."
M2B = (
    "<b>➖ Разделить 3 фото на отдельные приёмы?</b>\n"
    "Каждое фото будет распознано заново, отдельно от других. "
    "Вернуть как было можно кнопкой «↩️ Вернуть»."
)
SLOW = 0.5


@pytest.fixture(autouse=True)
def _short_wait(monkeypatch):
    monkeypatch.setattr(settings, "CUSTOM_EMOJI", None)
    monkeypatch.setattr(capture, "ALBUM_WAIT_SECONDS", 0.15)
    numbers = itertools.count(5000, 500)
    start = Wire.__init__

    def go_on(self):
        start(self)
        self.next_id = next(numbers)

    monkeypatch.setattr(Wire, "__init__", go_on)
    monkeypatch.setattr(tg._card_delivery, "_screens", {})


@pytest.fixture(autouse=True)
def _hint_is_old(db, owner):
    """M1b comes once in a life, and is tested where the line is."""
    owner.settings = {**(owner.settings or {}), "capture_hint_seen": True}
    db.commit()


@pytest.fixture
def named(monkeypatch):
    """Each download gives its own file's name, so the order can be seen."""

    async def download(_bot, file_id):
        return f"data:{file_id}"

    monkeypatch.setattr(tg, "_download_image", download)


@pytest.fixture
def soup(reader, named):
    reader.answer = dict(SOUP)
    return reader


def shot(file_id: str, group: str = "a1", **fields) -> dict:
    size = {"file_id": file_id, "file_unique_id": file_id, "width": 9, "height": 9}
    return _message(photo=[size], media_group_id=group, **fields)


def _labels(screen: dict) -> list:
    markup = screen["reply_markup"]
    return (json.loads(markup) if isinstance(markup, str) else markup)[
        "inline_keyboard"
    ]


def _shown_to_model(call: dict) -> list:
    return [call["payload"], *call.get("more", ())]


def _meal(db) -> Meal:
    db.expire_all()
    return db.scalars(select(Meal).order_by(Meal.id)).first()


def _live(db, meal) -> list:
    db.expire_all()
    return [item.name for item in crud_meal_item.live(db, meal.id)]


async def _in(application, message) -> None:
    await application.process_update(
        Update.de_json(
            {"update_id": message["message_id"], "message": message}, application.bot
        )
    )


async def _drained() -> None:
    while tg._meal_reads:
        await asyncio.gather(*list(tg._meal_reads))


def _run(steps) -> Wire:
    box = {}

    async def wrapped(application, wire):
        box["wire"] = wire
        await steps(application, wire)
        await _drained()

    asyncio.run(_with_app(wrapped))
    return box["wire"]


def album(*messages) -> Wire:
    async def steps(application, _wire):
        for message in messages:
            await _in(application, message)

    return _run(steps)


def tap(data: str, message_id: int = 4900) -> Wire:
    # The screens delivered hold their bot, and each run here has a new one.
    tg._card_delivery._screens.clear()

    async def steps(application, _wire):
        await application.process_update(_tap_update(application, data, message_id))

    return _run(steps)


# --- read together ----------------------------------------------------------


def test_the_wait_is_the_specs_and_so_is_the_limit(monkeypatch):
    monkeypatch.undo()
    assert capture.ALBUM_WAIT_SECONDS == 2
    assert capture.ALBUM_MAX == 10


def test_an_album_is_one_meal_one_line_and_one_read_in_order_of_arrival(db, soup):
    wire = album(shot("c"), shot("a"), shot("b"))
    meal = _meal(db)
    assert len(_meals(db)) == 1
    assert meal.photos == ["c", "a", "b"]
    assert len(soup.image) == 1
    assert _shown_to_model(soup.image[0]) == ["data:c", "data:a", "data:b"]
    assert len(wire.sent()) == 1
    assert wire.sent()[0]["text"] == "Фото в дневнике, читаю…"
    assert [e["text"] for e in wire.edits()] == [
        "2 фото в дневнике, читаю…",
        "3 фото в дневнике, читаю…",
        M2A,
    ]
    assert _live(db, meal) == ["суп", "хлеб", "йогурт"]
    reading = db.scalars(select(MealReading)).one()
    assert reading.source_ids == [s.id for s in _sources(db)]
    assert reading.applied is True


def test_the_record_line_offers_no_split(db, soup):
    wire = album(shot("a"), shot("b"), shot("c"))
    meal = _meal(db)
    assert _kb(wire.edits()[-1]) == [[f"ml:o:{meal.id}:r", f"ml:t:{meal.id}:r"]]


def test_order_of_arrival_outranks_the_message_numbers(db, soup):
    late, early = shot("late"), shot("early")
    assert late["message_id"] < early["message_id"]
    album(early, late)
    assert _shown_to_model(soup.image[0]) == ["data:early", "data:late"]


def test_the_read_waits_for_the_last_photo_not_the_first(db, soup, monkeypatch):
    # The margin is wall clock, and a busy machine ate 0.2 s of it.
    wait = SLOW * 3
    monkeypatch.setattr(capture, "ALBUM_WAIT_SECONDS", wait)

    async def steps(application, _wire):
        await _in(application, shot("a"))
        await asyncio.sleep(wait * 0.6)
        await _in(application, shot("b"))
        await asyncio.sleep(wait * 0.6)  # past the first photo's wait, not the second's
        assert soup.image == []
        await _drained()
        assert len(soup.image) == 1  # known-positive: the wait ends

    _run(steps)
    assert _shown_to_model(soup.image[0]) == ["data:a", "data:b"]


def test_a_photo_with_no_album_does_not_wait(db, soup, monkeypatch):
    monkeypatch.setattr(capture, "ALBUM_WAIT_SECONDS", 60)
    send(photo())
    assert len(soup.image) == 1
    assert "more" not in soup.image[0]


def test_any_caption_counts_in_order_of_arrival(db, soup):
    album(shot("a"), shot("b", caption="суп с чечевицей"), shot("c", caption="и хлеб"))
    assert soup.image[0]["caption"] == "суп с чечевицей\nи хлеб"
    assert [s.text for s in _sources(db)] == [None, "суп с чечевицей", "и хлеб"]


def test_a_time_in_a_later_caption_moves_the_meal(db, soup):
    wire = album(shot("a"), shot("b", caption="в 12:30"), shot("c"))
    meal = _meal(db)
    assert meal.time_source == "said"
    assert soup.image[0]["caption"] is None
    assert wire.edits()[-1]["text"].startswith("Записано на ")


def test_a_time_edited_by_hand_outranks_a_later_caption(db, soup, owner):
    async def steps(application, _wire):
        await _in(application, shot("a"))
        meal = _meal(db)
        meal.time_source = "edited"
        db.commit()
        await _in(application, shot("b", caption="в 12:30"))

    _run(steps)
    assert _meal(db).time_source == "edited"


def test_ten_photos_go_in_one_call_and_the_others_stay_in_the_record(
    db, soup, monkeypatch
):
    monkeypatch.setattr(capture, "ALBUM_WAIT_SECONDS", SLOW)
    names = [f"p{n}" for n in range(12)]
    album(*[shot(name) for name in names])
    assert len(soup.image) == 1
    assert _shown_to_model(soup.image[0]) == [f"data:{n}" for n in names[:10]]
    assert _meal(db).photos == names
    assert len(_sources(db)) == 12


def test_a_replayed_update_is_one_source_and_no_second_read(db, soup):
    first, second = shot("a"), shot("b")
    wire = album(first, second, second, first)
    assert len(_meals(db)) == 1
    assert len(_sources(db)) == 2
    assert _meal(db).photos == ["a", "b"]
    assert len(soup.image) == 1
    assert len(wire.sent()) == 1


def test_a_reply_to_any_photo_of_the_album_means_this_meal(db, soup):
    first, second = shot("a"), shot("b")
    album(first, second)
    rows = db.scalars(select(MealMessage).where(MealMessage.view == "source")).all()
    assert {r.message_id for r in rows} == {first["message_id"], second["message_id"]}
    assert {r.meal_id for r in rows} == {_meal(db).id}


def test_two_albums_are_two_meals_and_read_apart(db, soup):
    album(shot("a", "x"), shot("b", "y"), shot("c", "x"))
    meals = _meals(db)
    assert sorted(m.photos for m in meals) == [["a", "c"], ["b"]]
    assert sorted(len(_shown_to_model(c)) for c in soup.image) == [1, 2]


def test_one_file_gone_does_not_lose_the_others(db, soup, monkeypatch):
    async def download(_bot, file_id):
        if file_id == "b":
            raise BadRequest("Wrong file_id")
        return f"data:{file_id}"

    monkeypatch.setattr(tg, "_download_image", download)
    album(shot("a"), shot("b"), shot("c"))
    assert _shown_to_model(soup.image[0]) == ["data:a", "data:c"]
    assert _meal(db).reading_status == "read"


def test_every_file_gone_is_a_dead_file_and_no_model_is_asked(db, soup, monkeypatch):
    async def download(_bot, _file_id):
        raise BadRequest("Wrong file_id")

    monkeypatch.setattr(tg, "_download_image", download)
    album(shot("a"), shot("b"))
    assert soup.image == []
    reading = db.scalars(select(MealReading)).one()
    assert reading.error == capture.DEAD_FILE


# --- the model is shown all of it, once --------------------------------------


def test_the_vision_call_shows_every_photo_and_says_they_are_one_meal():
    service = OpenAIService()
    answer = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))]
    )
    service.client.chat.completions.create = AsyncMock(return_value=answer)
    asyncio.run(service.analyze_food_image("one", more_images=("two", "three")))
    content = service.client.chat.completions.create.call_args.kwargs["messages"][1][
        "content"
    ]
    assert [p["image_url"]["url"] for p in content if p["type"] == "image_url"] == [
        "one",
        "two",
        "three",
    ]
    words = " ".join(p["text"] for p in content if p["type"] == "text")
    assert "one meal" in words
    # Known-positive: one photo stays as it was.
    asyncio.run(service.analyze_food_image("one"))
    content = service.client.chat.completions.create.call_args.kwargs["messages"][1][
        "content"
    ]
    words = " ".join(p["text"] for p in content if p["type"] == "text")
    assert "one meal" not in words


class _Model:
    """Counts what the pipeline asks. No embedding and no barcode call may pass."""

    def __init__(self):
        self.vision, self.barcode = [], []

    def model_for(self, _task):
        return "fake"

    async def analyze_food_image(self, image_url, *, caption=None, more_images=()):
        self.vision.append([image_url, *more_images])
        return json.dumps(SOUP)

    async def extract_barcode_from_image(self, image_url):
        self.barcode.append(image_url)
        return None

    def __getattr__(self, name):
        raise AssertionError(f"paid call not expected: {name}")


def test_an_album_is_one_vision_call_and_no_product_is_looked_for(
    db, owner, Session, monkeypatch
):
    model = _Model()
    monkeypatch.setattr(pls, "SessionLocal", Session)
    monkeypatch.setattr(pls, "get_openai_service", lambda: model)

    result = asyncio.run(
        pls.resolve_meal_nutrition("one", telegram_id=OWNER, more_images=("two",))
    )
    assert model.vision == [["one", "two"]]
    assert model.barcode == []
    assert result.source == "vision"
    assert result.signals["strategy_tried"] == ["vision"]


def test_one_photo_still_looks_for_a_barcode(db, owner, Session, monkeypatch):
    """Known-positive of the test above."""
    model = _Model()
    monkeypatch.setattr(pls, "SessionLocal", Session)
    monkeypatch.setattr(pls, "get_openai_service", lambda: model)
    monkeypatch.setattr(pls, "_build_pipeline", lambda: [pls.VisionFallbackStrategy()])
    asyncio.run(pls.resolve_meal_nutrition("one", telegram_id=OWNER))
    assert model.barcode == ["one"]


# --- the straggler -----------------------------------------------------------


def _read_album(soup, *messages) -> Wire:
    wire = album(*messages)
    soup.image.clear()
    return wire


def test_a_straggler_joins_by_id_and_re_reads_on_the_same_line(db, soup):
    _read_album(soup, shot("a"), shot("b"))
    soup.answer = {**SOUP, "foods": ["суп", "хлеб", "йогурт"], "calories": 900.0}
    meal = _meal(db)
    rev = meal.rev
    wire = album(shot("c"))
    assert wire.sent() == []  # the same line
    assert len(_meals(db)) == 1
    assert len(soup.image) == 1
    assert _shown_to_model(soup.image[0]) == ["data:a", "data:b", "data:c"]
    pending, final = wire.edits()
    assert pending["text"] == "3 фото в дневнике, читаю…"
    assert pending.get("reply_markup") is None
    assert final["text"] == M2A
    assert _kb(final) == [[f"ml:o:{meal.id}:r", f"ml:t:{meal.id}:r"]]
    assert {pending["message_id"], final["message_id"]} == {
        db.scalar(
            select(MealMessage.message_id).where(
                MealMessage.meal_id == meal.id, MealMessage.view == "line"
            )
        )
    }
    meal = _meal(db)
    assert meal.reading_status == "read"
    assert meal.calories == 900.0
    assert meal.rev == rev + 1


def test_a_re_read_replaces_what_the_old_read_found(db, soup):
    soup.answer = {**SOUP, "foods": ["суп"]}
    _read_album(soup, shot("a"), shot("b"))
    meal = _meal(db)
    assert _live(db, meal) == ["суп"]
    soup.answer = dict(SOUP)
    album(shot("c"))
    meal = _meal(db)
    assert _live(db, meal) == ["суп", "хлеб", "йогурт"]
    assert meal.numbers_basis == "plate"
    assert meal.plate_totals["calories"] == SOUP["calories"]
    assert db.scalars(select(MealItem).where(MealItem.removed_at.is_not(None))).all()
    event = db.scalars(select(RecordEvent).where(RecordEvent.meal_id == meal.id)).one()
    assert event.kind == "reread"
    readings = db.scalars(select(MealReading).order_by(MealReading.id)).all()
    assert [len(r.source_ids) for r in readings] == [2, 3]
    assert [r.applied for r in readings] == [True, True]


def test_a_plate_read_first_leaves_no_plate_behind(db, soup):
    _read_album(soup, shot("a"), shot("b"))
    meal = _meal(db)
    assert meal.numbers_basis == "plate"
    soup.answer = {**SOUP, "foods": ["суп"]}
    album(shot("c"))
    meal = _meal(db)
    assert meal.numbers_basis == "items"
    assert meal.plate_totals is None
    assert meal.calories == SOUP["calories"]


def test_a_straggler_during_the_read_is_read_after_it(db, soup):
    async def steps(application, wire):
        soup.gate = asyncio.Event()
        await _in(application, shot("a"))
        await _in(application, shot("b"))
        while not soup.image:
            await asyncio.sleep(0.01)
        await _in(application, shot("c"))
        assert len(wire.sent()) == 1
        soup.gate.set()
        await _drained()

    wire = _run(steps)
    assert [_shown_to_model(c) for c in soup.image] == [
        ["data:a", "data:b"],
        ["data:a", "data:b", "data:c"],
    ]
    assert wire.edits()[-1]["text"] == M2A
    assert len(_live(db, _meal(db))) == 3


def test_a_straggler_in_the_wait_costs_no_second_read(db, soup):
    album(shot("a"), shot("b"), shot("c"))
    assert len(soup.image) == 1


def test_what_the_person_did_outranks_a_straggler_and_nothing_is_paid(db, soup, owner):
    _read_album(soup, shot("a"), shot("b"))
    meal = _meal(db)
    assert meal_items.add_item(db, owner.id, meal.id, name="сыр").status == "done"
    wire = album(shot("c"))
    assert soup.image == []  # the control is the test above: a re-read does pay
    meal = _meal(db)
    assert meal.photos == ["a", "b", "c"]
    assert "сыр" in _live(db, meal)
    assert wire.edits()[-1]["text"].startswith("Записано по 3 фото: ")
    assert len(_sources(db)) == 3


def test_a_straggler_past_the_limit_is_kept_and_not_read(db, soup, monkeypatch):
    monkeypatch.setattr(capture, "ALBUM_WAIT_SECONDS", SLOW)
    _read_album(soup, *[shot(f"p{n}") for n in range(10)])
    wire = album(shot("p10"))
    assert soup.image == []
    assert len(_meal(db).photos) == 11
    assert wire.edits()[-1]["text"].startswith("Записано по 11 фото: ")


def test_the_tenth_photo_as_a_straggler_is_read(db, soup, monkeypatch):
    """Known-positive of the test above."""
    monkeypatch.setattr(capture, "ALBUM_WAIT_SECONDS", SLOW)
    _read_album(soup, *[shot(f"p{n}") for n in range(9)])
    album(shot("p9"))
    assert len(_shown_to_model(soup.image[0])) == 10


def test_a_re_read_that_fails_leaves_the_old_reading_on_the_line(db, soup):
    _read_album(soup, shot("a"), shot("b"))
    soup.error = ValueError("boom")
    wire = album(shot("c"))
    meal = _meal(db)
    assert meal.reading_status == "read"
    assert _live(db, meal) == ["суп", "хлеб", "йогурт"]
    assert wire.edits()[-1]["text"] == M2A
    assert M7 not in [e["text"] for e in wire.edits()]
    assert meal.photos == ["a", "b", "c"]
    last = db.scalars(select(MealReading).order_by(MealReading.id.desc())).first()
    assert last.status == "failed"
    assert last.applied is False


def test_a_re_read_that_sees_no_food_leaves_the_old_reading_on_the_line(db, soup):
    _read_album(soup, shot("a"), shot("b"))
    soup.answer = {**SOUP, "foods": []}
    wire = album(shot("c"))
    meal = _meal(db)
    assert meal.reading_status == "read"
    assert _live(db, meal) == ["суп", "хлеб", "йогурт"]
    assert wire.edits()[-1]["text"] == M2A


def test_a_line_on_a_re_read_nobody_holds_comes_back_by_the_sweep(db, soup):
    _read_album(soup, shot("a"), shot("b"))
    meal = _meal(db)
    db.add(
        MealSource(
            user_id=meal.user_id,
            meal_id=meal.id,
            kind="photo",
            chat_id=CHAT,
            message_id=999,
            media_group_id="a1",
            file_id="c",
        )
    )
    db.commit()
    meal.photos = ["a", "b", "c"]
    db.commit()
    job = capture.read_again(db, meal.id)
    assert _meal(db).reading_status == "pending"
    job, done = capture.give_up(db, meal.id)
    assert done.line.text == M2A
    assert _meal(db).reading_status == "read"
    assert job.meal_id == meal.id


def test_a_read_that_fails_first_says_so_and_a_tap_reads_the_whole_album(db, soup):
    soup.error = ValueError("boom")
    wire = album(shot("a"), shot("b"), shot("c"))
    assert wire.edits()[-1]["text"] == M7
    meal = _meal(db)
    assert meal.reading_status == "failed"
    soup.error = None
    soup.image.clear()
    tap(f"ml:rr:{meal.id}:r", wire.sent()[0].get("message_id", 4900))
    assert _shown_to_model(soup.image[0]) == ["data:a", "data:b", "data:c"]


def test_a_restart_reads_the_whole_album_from_the_rows(db, soup, owner):
    meal = Meal(
        user_id=owner.id,
        meal_time=NOW,
        sent_at=NOW,
        photos=["a", "b"],
        reading_status="pending",
        numbers_basis="none",
    )
    db.add(meal)
    db.flush()
    for number, name in enumerate(("a", "b")):
        db.add(
            MealSource(
                user_id=owner.id,
                meal_id=meal.id,
                kind="photo",
                chat_id=CHAT,
                message_id=700 + number,
                media_group_id="a1",
                file_id=name,
                text="суп" if number else None,
                sent_at=NOW,
                created_at=NOW - timedelta(minutes=5),
            )
        )
    db.commit()
    meal_id = meal.id

    async def steps(application, _wire):
        await tg._replay_pending(application, [meal_id])

    _run(steps)
    assert _shown_to_model(soup.image[0]) == ["data:a", "data:b"]
    assert soup.image[0]["caption"] == "суп"
    assert _meal(db).reading_status == "read"


def test_a_photo_for_a_deleted_album_is_its_own_meal(db, soup, owner):
    _read_album(soup, shot("a"), shot("b"))
    first = _meal(db)
    assert meal_items.delete_meal(db, owner.id, first.id).status == "deleted"
    album(shot("c"))
    meals = [m for m in _meals(db) if m.deleted_at is None]
    assert [m.photos for m in meals] == [["c"]]
    assert _shown_to_model(soup.image[0]) == ["data:c"]


# --- M2b, the split ----------------------------------------------------------


def _split(db, soup) -> tuple:
    """A read album of three, split. (old meal, the split's wire)."""
    photos = [shot("a"), shot("b", caption="хлеб"), shot("c")]
    _read_album(soup, *photos)
    meal = _meal(db)
    soup.answer = {**NUTRITION, "foods": ["кусок"]}
    return meal, tap(f"ml:za:{meal.id}"), photos


def test_the_question_says_it_exactly_and_costs_nothing(db, soup):
    _read_album(soup, shot("a"), shot("b"), shot("c"))
    meal = _meal(db)
    wire = tap(f"ml:zq:{meal.id}:t")
    screen = wire.edits()[-1]
    assert screen["text"] == M2B
    assert _kb(screen) == [[f"ml:za:{meal.id}"], [f"ml:o:{meal.id}:t"]]
    assert [row[0]["text"] for row in _labels(screen)] == [
        "➖ Разделить",
        "⬅️ К приёму",
    ]
    assert soup.image == []
    assert _meal(db).photos == ["a", "b", "c"]


def test_a_meal_that_is_no_album_is_not_asked(db, soup, reader):
    wire = send(photo())
    meal = _meal(db)
    assert wire.edits()
    soup.image.clear()
    for data in (f"ml:zq:{meal.id}:t", f"ml:za:{meal.id}"):
        screen = tap(data).edits()[-1]
        assert screen["text"] != M2B
    assert soup.image == []
    assert len(_meals(db)) == 1


def test_split_makes_a_meal_of_each_photo_each_read_alone(db, soup):
    meal, wire, photos = _split(db, soup)
    meals = sorted((m for m in _meals(db) if m.deleted_at is None), key=lambda m: m.id)
    assert [m.photos for m in meals] == [["a"], ["b"], ["c"]]
    assert meals[0].id == meal.id
    assert sorted(_shown_to_model(c) for c in soup.image) == [
        ["data:a"],
        ["data:b"],
        ["data:c"],
    ]
    captions = {c["payload"]: c["caption"] for c in soup.image}
    assert captions == {"data:a": None, "data:b": "хлеб", "data:c": None}
    for found in meals:
        assert _live(db, found) == ["кусок"]
        assert found.reading_status == "read"
        assert found.meal_time == meal.meal_time
    moved = {s.file_id: s.meal_id for s in _sources(db)}
    assert moved == {name: m.id for name, m in zip("abc", meals)}
    assert [s.origin_meal_id for s in _sources(db)] == [meal.id] * 3


def test_each_new_line_answers_its_own_photo_and_gets_its_result(db, soup):
    _meal_row, wire, photos = _split(db, soup)
    assert [
        (
            json.loads(p["reply_parameters"])["message_id"]
            if isinstance(p["reply_parameters"], str)
            else p["reply_parameters"]["message_id"]
        )
        for p in wire.sent()
    ] == [m["message_id"] for m in photos]
    assert {p["text"] for p in wire.sent()} == {"Фото в дневнике, читаю…"}
    lines = {p["text"] for p in wire.edits()}
    assert "Записано: кусок." in lines


def test_the_tapped_message_counts_the_photos_and_offers_the_way_back(db, soup):
    meal, wire, _photos = _split(db, soup)
    screen = wire.edits()[0]
    event = db.scalars(select(RecordEvent).where(RecordEvent.kind == "split")).one()
    assert screen["text"] == "3 фото в дневнике, читаю…"
    assert _kb(screen) == [[close_row.CALLBACK], [f"ml:u:{event.id}:r"]]
    assert _labels(screen)[1][0]["text"] == "↩️ Вернуть"
    assert event.meal_id == meal.id
    # And no line stays on «читаю»: once the reads end it says what is there, and the
    # way back is still on it.
    settled = wire.edits()[-1]
    assert settled["message_id"] == screen["message_id"]
    assert settled["text"] == "Записано: кусок."
    assert f"ml:u:{event.id}:r" in _kb(settled)[1]


def test_a_second_tap_pays_nothing(db, soup):
    meal, _wire, _photos = _split(db, soup)
    soup.image.clear()
    wire = tap(f"ml:za:{meal.id}")
    assert soup.image == []
    assert _toasts(wire) == ["Уже так"]
    assert len([m for m in _meals(db) if m.deleted_at is None]) == 3
    # And the question no longer asks.
    assert tap(f"ml:zq:{meal.id}:t").edits()[0]["text"] != M2B


def test_a_meal_still_being_read_is_not_split_under_the_read(db, soup):
    async def steps(application, _wire):
        soup.gate = asyncio.Event()
        await _in(application, shot("a"))
        await _in(application, shot("b"))
        meal_id = _meal(db).id
        await application.process_update(
            _tap_update(application, f"ml:za:{meal_id}", 4900)
        )
        soup.gate.set()

    wire = _run(steps)
    assert _toasts(wire) == ["Читаю"]
    assert len(_meals(db)) == 1


def test_a_foreign_or_deleted_meal_is_not_split(db, soup, owner):
    _read_album(soup, shot("a"), shot("b"))
    meal = _meal(db)
    meal.user_id = owner.id + 1
    db.commit()
    assert tap(f"ml:za:{meal.id}").edits()[-1]["text"] != M2B
    assert soup.image == []
    assert len(_meals(db)) == 1
    meal.user_id = owner.id
    db.commit()
    meal_items.delete_meal(db, owner.id, meal.id)
    tap(f"ml:za:{meal.id}")
    assert soup.image == []
    assert len(_meals(db)) == 1


def test_a_late_photo_after_a_split_is_its_own_meal(db, soup):
    _split(db, soup)
    soup.image.clear()
    album(shot("d"))
    meals = [m for m in _meals(db) if m.deleted_at is None]
    assert sorted(m.photos for m in meals) == [["a"], ["b"], ["c"], ["d"]]
    assert _shown_to_model(soup.image[0]) == ["data:d"]


def _split_event(db) -> RecordEvent:
    db.expire_all()
    return db.scalars(
        select(RecordEvent)
        .where(RecordEvent.kind == "split", RecordEvent.undoes_event_id.is_(None))
        .order_by(RecordEvent.id)
    ).first()


def test_the_way_back_puts_all_of_it_back_and_removes_the_new_lines(db, soup):
    meal, split_wire, _photos = _split(db, soup)
    event = _split_event(db)
    new_lines = {p["message_id"] for p in split_wire.edits()} - {4900}
    wire = tap(f"ml:u:{event.id}:r")
    meal = _meal(db)
    assert meal.photos == ["a", "b", "c"]
    assert meal.reading_status == "read"
    assert _live(db, meal) == ["суп", "хлеб", "йогурт"]
    assert meal.description == "суп, хлеб, йогурт"
    assert meal.calories == SOUP["calories"]
    assert meal.numbers_basis == "plate"
    assert {s.meal_id for s in _sources(db)} == {meal.id}
    assert [m.id for m in _meals(db) if m.deleted_at is None] == [meal.id]
    gone = {p["message_id"] for n, p in wire.calls if n == "deleteMessage"}
    assert gone == new_lines and len(gone) == 3
    back = wire.edits()[-1]
    assert "↩️ Вернуть" not in str(back.get("reply_markup"))
    assert soup.image and len(soup.image) == 3  # the way back asks no model


def test_after_the_way_back_the_album_can_be_split_and_grow_again(db, soup):
    _split(db, soup)
    tap(f"ml:u:{_split_event(db).id}:r")
    soup.image.clear()
    album(shot("d"))
    assert len(_shown_to_model(soup.image[0])) == 4
    assert len([m for m in _meals(db) if m.deleted_at is None]) == 1


def test_the_way_back_is_refused_once_a_new_meal_was_changed(db, soup, owner):
    meal, _wire, _photos = _split(db, soup)
    event = _split_event(db)
    other = [m for m in _meals(db) if m.id != meal.id][0]
    assert meal_items.add_item(db, owner.id, other.id, name="сыр").status == "done"
    wire = tap(f"ml:u:{event.id}:r")
    assert meal_items.UNDO_REFUSED in wire.edits()[-1]["text"]
    assert len([m for m in _meals(db) if m.deleted_at is None]) == 3
    assert "сыр" in _live(db, other)
    assert not [n for n, _p in wire.calls if n == "deleteMessage"]


def test_a_second_tap_on_the_way_back_changes_nothing(db, soup):
    _split(db, soup)
    event = _split_event(db)
    tap(f"ml:u:{event.id}:r")
    count = db.scalars(select(RecordEvent)).all()
    wire = tap(f"ml:u:{event.id}:r")
    assert len(db.scalars(select(RecordEvent)).all()) == len(count)
    assert not [n for n, _p in wire.calls if n == "deleteMessage"]


def test_after_a_split_the_way_on_lands_on_the_meals_own_line(db, soup):
    """What a sweep or a retry finds from the rows: not the album's old line."""
    meal, wire, _photos = _split(db, soup)
    old = db.scalars(
        select(MealMessage).where(MealMessage.view == "other").order_by(MealMessage.id)
    ).all()
    assert old
    sent = {p["message_id"] for p in wire.edits()} - {4900}
    job = capture.read_again(db, meal.id)
    assert job.line_id in sent
    assert job.line_id not in {row.message_id for row in old}


def test_a_read_that_is_over_holds_nothing_for_the_next_photo(db, soup):
    """In the same step as its look at the rows: the task's own end comes later, and a
    photo in between would find a read that will never take it in."""
    _read_album(soup, shot("a"), shot("b"))
    meal = _meal(db)
    job = capture.read_again(db, meal.id)
    job = capture.read_as_of_now(db, job)

    async def steps(application, _wire):
        tg._live_reads[meal.id] = None
        assert await tg._album_went_on(application.bot, job) is None
        assert meal.id not in tg._live_reads

    _run(steps)


def test_an_ended_read_does_not_let_go_of_the_next_ones_hold(monkeypatch):
    async def read(_application, job):
        await job.done.wait()

    monkeypatch.setattr(tg, "_read_meal", read)

    async def steps(application, _wire):
        one = SimpleNamespace(meal_id=7, inbound_id=None, done=asyncio.Event())
        two = SimpleNamespace(meal_id=7, inbound_id=None, done=asyncio.Event())
        ended = tg._start_read(application, one)
        tg._live_reads.pop(7)  # as a read that is over lets go
        held = tg._start_read(application, two)
        one.done.set()
        await ended
        await asyncio.sleep(0)
        seen["after the first"] = 7 in tg._live_reads
        two.done.set()
        await held
        await asyncio.sleep(0)
        seen["after the second"] = 7 in tg._live_reads

    seen = {}
    _run(steps)
    assert seen == {"after the first": True, "after the second": False}


def test_a_read_still_out_at_the_way_back_writes_nothing(db, soup, owner):
    _read_album(soup, shot("a"), shot("b"))
    meal = _meal(db)
    soup.answer = {**NUTRITION, "foods": ["кусок"]}

    async def steps(application, _wire):
        soup.gate = asyncio.Event()
        await application.process_update(
            _tap_update(application, f"ml:za:{meal.id}", 4900)
        )
        while len(soup.image) < 2:
            await asyncio.sleep(0.01)
        await application.process_update(
            _tap_update(application, f"ml:u:{_split_event(db).id}:r", 4900)
        )
        soup.gate.set()

    _run(steps)
    meal = _meal(db)
    assert _live(db, meal) == ["суп", "хлеб", "йогурт"]
    assert meal.reading_status == "read"
    kept = db.scalars(select(MealReading).order_by(MealReading.id)).all()[1:]
    assert kept and all(r.applied is False for r in kept)


def test_nothing_about_the_food_reaches_the_logs(db, soup, caplog):
    soup.error = ValueError("boom")
    album(shot("a", caption="секретный суп"), shot("b"))
    assert "секретный" not in caplog.text


def test_finding_the_album_never_asks_postgres_to_compare_json(db, soup, Session):
    """``SELECT DISTINCT meals.*`` passes on SQLite and is refused by Postgres, whose
    json has no equality: there every album answered «Записать сейчас не вышло»."""
    engine = Session.kw["bind"]
    seen = []

    def watch(conn, cursor, statement, parameters, context, executemany):
        seen.append(" ".join(statement.split()))

    event.listen(engine, "before_cursor_execute", watch)
    try:
        album(shot("a"), shot("b"))
    finally:
        event.remove(engine, "before_cursor_execute", watch)
    joined = [s for s in seen if "meal_sources.media_group_id" in s]
    assert joined  # the query under guard did run
    assert not [s for s in joined if "DISTINCT meals." in s]
    assert _meal(db).photos == ["a", "b"]
