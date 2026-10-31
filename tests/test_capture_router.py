"""The router of section 5.1, through the real application, at the wire.

Every test sends a real ``Update`` into ``create_bot_application()``; the only thing
replaced is the HTTP request underneath the bot, so what is asserted is what would
reach Telegram. The model and the download are fakes. No network, no paid call.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from telegram import Update
from telegram.request import BaseRequest

import app.services.telegram as tg
from app.core.config import settings
from app.crud.crud_meal_item import crud_meal_item
from app.db.base import Base
from app.models import (
    InboundMessage,
    Meal,
    MealItem,
    MealMessage,
    MealReading,
    MealSource,
    PersonalFood,
    User,
)
from app.schemas.meal import MealCreate
from app.services import meal_items
from app.services.product_lookup_service import ResolutionResult

OWNER = 42
CHAT = 42
NOW = datetime(2026, 10, 26, 9, 20, tzinfo=UTC)  # 13:20 in the owner's zone
NOW_TS = int(NOW.timestamp())

NUTRITION = {
    "foods": ["рис", "курица", "овощи"],
    "portion": "300г",
    "calories": 620.0,
    "protein": 30.0,
    "fats": 12.0,
    "carbs": 80.0,
}


class Wire(BaseRequest):
    """Telegram at the other end: answers every call and keeps it."""

    file_bytes = b"jpeg-bytes"  # what a download gets

    def __init__(self):
        super().__init__()
        self.calls: list = []
        self.next_id = 5000
        self.fail_download = False

    @property
    def read_timeout(self):
        return 1

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_request(self, url, method, request_data=None, **_kwargs):
        if method == "GET" or "/file/" in url:
            if self.fail_download:
                return 500, b"{}"
            return 200, self.file_bytes
        name = unquote(url.rsplit("/", 1)[-1])
        params = dict(request_data.parameters) if request_data else {}
        self.calls.append((name, params))
        return (
            200,
            json.dumps({"ok": True, "result": self._result(name, params)}).encode(),
        )

    def _result(self, name, params):
        if name == "getMe":
            return {"id": 1, "is_bot": True, "first_name": "bot", "username": "b"}
        if name == "getFile":
            return {
                "file_id": params.get("file_id", "f"),
                "file_unique_id": "u",
                "file_path": "photos/1.jpg",
            }
        if name in ("sendMessage", "editMessageText"):
            self.next_id += 1 if name == "sendMessage" else 0
            return {
                "message_id": params.get("message_id", self.next_id),
                "date": NOW_TS,
                "chat": {"id": CHAT, "type": "private"},
                "text": params.get("text", ""),
            }
        return True

    # What the person sees.
    def sent(self) -> list:
        return [p for n, p in self.calls if n == "sendMessage"]

    def edits(self) -> list:
        return [p for n, p in self.calls if n == "editMessageText"]

    def seen(self) -> list:
        return [
            p["text"] for n, p in self.calls if n in ("sendMessage", "editMessageText")
        ]


@pytest.fixture
def Session(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(tg, "SessionLocal", factory)
    # The services that keep their own session: the audit logs.
    import app.services.ai_call_log_service as log_service
    import app.services.inbound_message_service as inbound_service
    import app.services.model_selection as model_selection

    monkeypatch.setattr(inbound_service, "SessionLocal", factory)
    monkeypatch.setattr(log_service, "SessionLocal", factory)
    monkeypatch.setattr(model_selection, "SessionLocal", factory)
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", str(OWNER))
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:fake")
    # The learning loop reaches Celery: not here.
    monkeypatch.setattr(tg, "_schedule_personal_food_save", lambda **_: None)
    return factory


@pytest.fixture
def db(Session):
    session = Session()
    session.add(User(telegram_id=OWNER, settings={"timezone": "Asia/Tbilisi"}))
    session.commit()
    yield session
    session.close()


@pytest.fixture
def owner(db):
    return db.query(User).filter_by(telegram_id=OWNER).one()


@pytest.fixture
def reader(monkeypatch):
    """The fakes of the model: what was asked, and what they answer."""
    state = SimpleNamespace(
        text=[], image=[], answer=dict(NUTRITION), error=None, gate=None
    )

    async def analyze(*, kind, payload, input_ref, telegram_id):
        state.text.append(payload)
        if state.gate is not None:
            await state.gate.wait()
        if state.error is not None:
            raise state.error
        return dict(state.answer)

    async def resolve(payload, *, telegram_id, input_ref, caption=None, more_images=()):
        call = {"payload": payload, "caption": caption, "ref": input_ref}
        if more_images:
            call["more"] = more_images
        state.image.append(call)
        if state.gate is not None:
            await state.gate.wait()
        if state.error is not None:
            raise state.error
        return ResolutionResult(
            nutrition=dict(state.answer),
            source="vision",
            confidence_tier="low",
            description=None,
            signals={"portion_grams": 300.0},
            portion_grams=300.0,
        )

    monkeypatch.setattr(tg, "_analyze_and_parse", analyze)
    monkeypatch.setattr(tg, "resolve_meal_nutrition", resolve)
    return state


_next_message = iter(range(100, 100000))


def _message(**fields) -> dict:
    return {
        "message_id": next(_next_message),
        "date": NOW_TS,
        "chat": {"id": CHAT, "type": "private"},
        "from": {"id": OWNER, "is_bot": False, "first_name": "o", "username": "o"},
        **fields,
    }


PHOTO = [
    {"file_id": "small", "file_unique_id": "s", "width": 90, "height": 90},
    {"file_id": "big", "file_unique_id": "b", "width": 900, "height": 900},
]


def text(body: str, **fields) -> dict:
    return _message(text=body, **fields)


def photo(**fields) -> dict:
    return _message(photo=PHOTO, **fields)


def _document(mime: str, size: int = 1000) -> dict:
    return _message(
        document={
            "file_id": "doc",
            "file_unique_id": "d",
            "mime_type": mime,
            "file_size": size,
        }
    )


def _voice(size: int = 1000) -> dict:
    return _message(
        voice={"file_id": "v", "file_unique_id": "vu", "duration": 3, "file_size": size}
    )


async def _with_app(steps):
    application = tg.create_bot_application()
    wire = Wire()
    application.bot._request = (wire, wire)
    await application.initialize()
    await application.start()
    try:
        await steps(application, wire)
    finally:
        await asyncio.gather(*list(tg._meal_reads), return_exceptions=True)
        await application.stop()
        await application.shutdown()


def send(*messages, drain=True) -> Wire:
    """Each message as one update through the application; returns the wire."""
    box = {}

    async def steps(application, wire):
        box["wire"] = wire
        for message in messages:
            update = Update.de_json(
                {"update_id": message["message_id"], "message": message},
                application.bot,
            )
            await application.process_update(update)
            if drain:
                await asyncio.gather(*list(tg._meal_reads))

    asyncio.run(_with_app(steps))
    return box["wire"]


def _meals(db):
    db.expire_all()
    return list(db.scalars(select(Meal)).all())


def _sources(db):
    db.expire_all()
    return list(db.scalars(select(MealSource)).all())


def _item_names(db, meal):
    return [
        i.name
        for i in db.scalars(
            select(MealItem).where(
                MealItem.meal_id == meal.id, MealItem.removed_at.is_(None)
            )
        )
    ]


def _meal_now(db, owner, *, minutes_ago=30, names=("суп",), **columns) -> Meal:
    meal = meal_items.save_meal(
        db,
        MealCreate(
            description=", ".join(names),
            meal_time=NOW - timedelta(minutes=minutes_ago),
            sent_at=NOW - timedelta(minutes=minutes_ago),
            nutrients={"foods": list(names)},
            **columns,
        ),
        owner.id,
    )
    return meal


@pytest.fixture
def clock(monkeypatch):
    """The capture's clock, at the test's fixed moment."""
    from app.services import capture

    monkeypatch.setattr(capture, "_now", lambda: NOW)


def _rows(markup) -> list:
    return [[b["callback_data"] for b in row] for row in markup["inline_keyboard"]]


def _kb(params) -> list:
    markup = params.get("reply_markup")
    if isinstance(markup, str):
        markup = json.loads(markup)
    return _rows(markup)


# --- born first ---------------------------------------------------------------


def test_a_photo_is_in_the_diary_before_the_download(db, owner, reader, monkeypatch):
    """Known-positive first: the row exists when the first network call happens."""
    seen = {}
    original = Wire.do_request

    async def spy(self, url, method, request_data=None, **kw):
        if url.endswith("getFile"):
            db.expire_all()
            seen["meals"] = db.query(Meal).count()
            seen["sources"] = db.query(MealSource).count()
            seen["status"] = db.query(Meal).one().reading_status
        return await original(self, url, method, request_data, **kw)

    monkeypatch.setattr(Wire, "do_request", spy)
    send(photo())
    assert seen == {"meals": 1, "sources": 1, "status": "pending"}


def test_with_the_download_and_the_model_broken_the_meal_and_source_stand(
    db, owner, reader, monkeypatch
):
    reader.error = RuntimeError("model down")

    async def broken_file(self, *a, **k):
        raise OSError("no download")

    monkeypatch.setattr(tg, "_download_image", broken_file)
    wire = send(photo())

    meal = _meals(db)[0]
    assert meal.reading_status == "failed"
    assert meal.photos == ["big"]
    source = _sources(db)[0]
    assert (source.kind, source.meal_id, source.file_id) == ("photo", meal.id, "big")
    assert wire.edits()[-1]["text"] == (
        "Фото сохранено, распознать еду сейчас не вышло.\n"
        "В дневнике запись с этим фото."
    )
    assert _kb(wire.edits()[-1]) == [[f"ml:rr:{meal.id}:r", f"ml:w:{meal.id}:r"]]
    assert not reader.image  # the download failed before the model


def test_with_the_model_broken_a_text_stays_and_says_so(db, owner, reader):
    reader.error = ValueError("not json")
    wire = send(text("суп с хлебом"))
    meal = _meals(db)[0]
    assert meal.reading_status == "failed"
    assert meal.description == "суп с хлебом"
    assert wire.edits()[-1]["text"] == (
        "Текст сохранён, разобрать еду сейчас не вышло.\n"
        "В дневнике запись с этим текстом."
    )
    reading = db.scalars(select(MealReading)).one()
    assert (reading.status, reading.applied) == ("failed", False)
    # The error's class only: a body or a transcript never goes there.
    assert reading.error == "ValueError"


def test_a_database_that_refuses_the_birth_says_m7f(db, owner, reader, monkeypatch):
    from app.services import capture

    def broken(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(capture, "born", broken)
    wire = send(photo())
    assert wire.sent()[-1]["text"] == (
        "Записать сейчас не вышло, сервер не отвечает. Пришли ещё раз."
    )
    assert not reader.image


# --- M1: the photo line --------------------------------------------------------


def test_the_pending_line_is_a_silent_reply_with_no_buttons(db, owner, reader):
    message = photo()
    wire = send(message)
    first = wire.sent()[0]
    assert first["text"] == "Фото в дневнике, читаю…"
    assert first["disable_notification"] is True
    reply = first["reply_parameters"]
    reply = json.loads(reply) if isinstance(reply, str) else reply
    assert reply["message_id"] == message["message_id"]
    assert "reply_markup" not in first


def test_the_read_edits_the_same_line_and_the_hint_comes_once(db, owner, reader):
    wire = send(photo(), photo())
    meals = _meals(db)
    edits = wire.edits()
    assert edits[0]["text"] == (
        "Записано: рис, курица и овощи.\n"
        "На этом всё. Поправить можно кнопками или ответом на это сообщение."
    )
    assert edits[0]["message_id"] == 5001  # the line the first photo got
    assert _kb(edits[0]) == [[f"ml:o:{meals[0].id}:r", f"ml:t:{meals[0].id}:r"]]
    assert edits[1]["text"] == "Записано: рис, курица и овощи."
    assert [m.reading_status for m in meals] == ["read", "read"]
    assert _item_names(db, meals[0]) == ["рис", "курица", "овощи"]


def test_one_dish_has_its_fix_button_on_the_dish(db, owner, reader):
    reader.answer = {**NUTRITION, "foods": ["суп"]}
    wire = send(photo())
    meal = _meals(db)[0]
    item = db.scalars(select(MealItem)).one()
    assert _kb(wire.edits()[-1]) == [[f"ml:i:{item.id}:r", f"ml:t:{meal.id}:r"]]


def test_numbers_on_the_line_when_the_setting_is_on(db, owner, reader):
    owner.settings = {**owner.settings, "show_numbers": True}
    db.commit()
    wire = send(photo())
    assert wire.edits()[-1]["text"].startswith(
        "Записано: рис, курица и овощи · ≈ 620 ккал."
    )


def test_the_caption_reaches_the_read_and_the_file_is_the_largest(db, owner, reader):
    send(photo(caption="с сыром 150 г"))
    assert reader.image[0]["caption"] == "с сыром 150 г"
    assert reader.image[0]["ref"] == "big"
    assert reader.image[0]["payload"].startswith("data:image/jpeg;base64,")


def test_the_message_time_is_what_the_meal_is_born_with(db, owner, reader):
    send(photo())
    meal = _meals(db)[0]
    expected = NOW.replace(tzinfo=None)
    assert meal.sent_at.replace(tzinfo=None) == expected
    assert meal.meal_time.replace(tzinfo=None) == expected
    assert meal.time_source == "sent"
    assert _sources(db)[0].sent_at.replace(tzinfo=None) == expected


def test_a_forward_takes_the_original_date_as_approximate(db, owner, reader):
    before = NOW - timedelta(days=1)
    wire = send(
        photo(
            forward_origin={
                "type": "hidden_user",
                "sender_user_name": "x",
                "date": int(before.timestamp()),
            }
        )
    )
    meal = _meals(db)[0]
    assert meal.meal_time.replace(tzinfo=None) == before.replace(tzinfo=None)
    assert (meal.time_precision, meal.time_source) == ("approx", "forward")
    assert _sources(db)[0].forward_date is not None
    assert "(время пересланного сообщения)" in wire.edits()[-1]["text"]


def test_the_mapping_holds_the_line_and_the_persons_message(db, owner, reader):
    message = photo()
    wire = send(message)
    meal = _meals(db)[0]
    rows = {r.message_id: r for r in db.scalars(select(MealMessage))}
    assert rows[message["message_id"]].view == "source"
    line = wire.edits()[-1]["message_id"]
    assert rows[line].view == "line"
    assert {r.meal_id for r in rows.values()} == {meal.id}


def test_a_late_read_does_not_overwrite_what_the_person_changed(db, owner, reader):
    """Rev moved while the model was thinking: stored, not applied."""
    gate = {}

    async def steps(application, wire):
        reader.gate = asyncio.Event()
        update = Update.de_json({"update_id": 1, "message": photo()}, application.bot)
        await application.process_update(update)
        await asyncio.sleep(0)
        meal = db.query(Meal).one()
        meal.rev = (meal.rev or 0) + 1
        db.commit()
        reader.gate.set()
        await asyncio.gather(*list(tg._meal_reads))
        gate["wire"] = wire

    asyncio.run(_with_app(steps))
    reading = db.scalars(select(MealReading)).one()
    assert reading.applied is False
    assert reading.output["foods"] == NUTRITION["foods"]
    assert not _item_names(db, _meals(db)[0])
    assert gate["wire"].edits() == []


# --- M3: the text line ---------------------------------------------------------


def test_the_words_appear_at_once_and_the_names_only_edit_when_they_differ(
    db, owner, reader
):
    reader.answer = {**NUTRITION, "foods": ["кофе", "печенье"]}
    wire = send(text("кофе и печенье"))
    meal = _meals(db)[0]
    assert wire.sent()[0]["text"].startswith("Записано: кофе и печенье.")
    assert _kb(wire.sent()[0]) == [[f"ml:o:{meal.id}:r", f"ml:t:{meal.id}:r"]]
    assert wire.edits() == []  # the same names: nothing to say again
    assert _item_names(db, meal) == ["кофе", "печенье"]
    assert meal.reading_status == "read"


def test_when_the_model_splits_differently_the_line_is_edited(db, owner, reader):
    reader.answer = {**NUTRITION, "foods": ["кофе", "печенье"]}
    wire = send(text("кофе с печеньками"))
    assert wire.sent()[0]["text"].startswith("Записано: кофе с печеньками.")
    assert wire.edits()[-1]["text"].startswith("Записано: кофе и печенье.")


def test_an_exact_dish_needs_no_model_and_is_final_at_once(db, owner, reader):
    db.add(PersonalFood(user_id=owner.id, canonical_name="Овсянка"))
    db.commit()
    wire = send(text("овсянка"))
    assert reader.text == []  # known-negative of the paid call
    meal = _meals(db)[0]
    assert meal.reading_status == "read"
    assert wire.sent()[0]["text"].startswith("Записано: Овсянка.")
    assert wire.edits() == []
    reading = db.scalars(select(MealReading)).one()
    assert (reading.kind, reading.applied) == ("exact_name", True)
    assert _sources(db)[0].meal_id == meal.id


def test_a_text_the_model_finds_no_food_in_leaves_no_meal_and_keeps_the_source(
    db, owner, reader
):
    reader.answer = {**NUTRITION, "foods": []}
    wire = send(text("сегодня хорошая погода"))
    meal = _meals(db)[0]
    assert meal.deleted_at is not None
    assert meal.reading_status == "no_food"
    source = _sources(db)[0]
    assert source.meal_id is None
    assert source.text == "сегодня хорошая погода"
    assert wire.edits()[-1]["text"] == "Еды здесь не видно, в дневник не записано."
    assert _kb(wire.edits()[-1]) == [[f"ml:nf:{source.id}"]]


def test_a_final_question_mark_is_no_meal_and_costs_nothing(db, owner, reader):
    wire = send(text("сколько белка в твороге?"))
    assert _meals(db) == []
    assert reader.text == []
    source = _sources(db)[0]
    assert wire.sent()[0]["text"] == (
        "Вопросы здесь пока не разбираются, в дневник не записано."
    )
    assert _kb(wire.sent()[0]) == [[f"ml:nf:{source.id}"]]


def test_a_photo_with_no_food_stays_with_its_own_line(db, owner, reader):
    reader.answer = {**NUTRITION, "foods": []}
    wire = send(photo())
    meal = _meals(db)[0]
    assert meal.reading_status == "no_food"
    assert meal.deleted_at is None
    assert wire.edits()[-1]["text"] == (
        "Фото сохранено, еды на нём не видно.\nВ дневнике запись с этим фото."
    )
    assert _kb(wire.edits()[-1]) == [[f"ml:w:{meal.id}:r", f"ml:d:{meal.id}:r"]]


# --- the matrix: every row, and no type returns silence ------------------------


@pytest.mark.parametrize(
    "message, line",
    [
        (lambda: photo(), "Фото в дневнике, читаю…"),
        (lambda: _document("image/png"), "Фото в дневнике, читаю…"),
        (lambda: _voice(), "Голосовое в дневнике, слушаю…"),
    ],
)
def test_row_10_media_gets_a_line_and_a_meal(db, owner, reader, message, line):
    wire = send(message())
    assert wire.sent()[0]["text"] == line
    assert len(_meals(db)) == 1
    assert len(_sources(db)) == 1


@pytest.mark.parametrize(
    "extra",
    [
        {
            "video": {
                "file_id": "x",
                "file_unique_id": "y",
                "width": 1,
                "height": 1,
                "duration": 1,
            }
        },
        {
            "video_note": {
                "file_id": "x",
                "file_unique_id": "y",
                "length": 1,
                "duration": 1,
            }
        },
        {"audio": {"file_id": "x", "file_unique_id": "y", "duration": 1}},
        {
            "document": {
                "file_id": "x",
                "file_unique_id": "y",
                "mime_type": "application/pdf",
            }
        },
        {
            "sticker": {
                "file_id": "x",
                "file_unique_id": "y",
                "width": 1,
                "height": 1,
                "is_animated": False,
                "is_video": False,
                "type": "regular",
            }
        },
        {"location": {"latitude": 1.0, "longitude": 2.0}},
    ],
    ids=["video", "video_note", "audio", "pdf", "sticker", "location"],
)
def test_row_11_anything_else_gets_one_line_and_no_rows(db, owner, reader, extra):
    wire = send(_message(**extra))
    assert wire.seen() == [
        "Такое пока не читается, в дневник не записано. "
        "Подойдёт фото, текст или голосовое."
    ]
    assert _meals(db) == []
    assert _sources(db) == []


@pytest.mark.parametrize(
    "message", [lambda: _voice(21_000_000), lambda: _document("image/png", 21_000_000)]
)
def test_over_20_mb_says_so_and_writes_no_meal(db, owner, reader, message):
    wire = send(message())
    assert wire.seen() == [
        "Файл больше 20 МБ, Telegram такие боту не передаёт. В дневник не записано."
    ]
    assert _meals(db) == []


@pytest.mark.parametrize(
    "word", ["привет", "Спасибо", "ок", "окей", "да", "нет", "пока", "Ок!"]
)
def test_row_9_the_stop_list_answers_nothing_and_keeps_the_source(
    db, owner, reader, word
):
    wire = send(text(word))
    assert wire.sent() == []
    assert _meals(db) == []
    assert [s.text for s in _sources(db)] == [word]
    assert reader.text == []


def test_row_9_control_a_word_outside_the_stop_list_is_food(db, owner, reader):
    send(text("пока суп"))
    assert len(_meals(db)) == 1


@pytest.mark.parametrize(
    "label", ["Сейчас", "Завтрак", "Обед", "Ужин", "Назад", "Отмена"]
)
def test_row_3_a_stale_label_is_never_a_meal(db, owner, reader, label):
    wire = send(text(label))
    assert wire.seen() == [
        "Это кнопка старого меню, в дневник не записано. Новое меню внизу."
    ]
    assert _meals(db) == []
    assert reader.text == []
    markup = wire.sent()[0]["reply_markup"]
    markup = json.loads(markup) if isinstance(markup, str) else markup
    assert "keyboard" in markup


def test_row_3_control_the_same_word_in_a_sentence_is_food(db, owner, reader):
    send(text("обед в столовой"))
    assert len(_meals(db)) == 1


@pytest.mark.parametrize(
    "weight, kg", [("72,5", 72.5), ("72.5 кг", 72.5), ("150", 150.0)]
)
def test_row_5_a_bare_number_is_a_weigh_in(db, owner, reader, weight, kg):
    from app.models import BodyMetric

    send(text(weight))
    assert _meals(db) == []
    assert reader.text == []
    assert db.scalars(select(BodyMetric)).one().weight == kg


def test_row_5_13_20_outside_a_slot_stays_with_the_weight_and_moves_no_meal(
    db, owner, reader, clock
):
    meal = _meal_now(db, owner)
    before = meal.meal_time
    wire = send(text("13.20"))
    db.expire_all()
    assert meal.meal_time == before
    assert len(_meals(db)) == 1
    assert wire.seen()[0].startswith("Не похоже на вес.")


def test_row_6_a_routine_name_stays_a_tick(db, owner, reader, monkeypatch):
    monkeypatch.setattr(tg.stats_screen, "tick_by_name", lambda db, tid, t: "витамин")
    wire = send(text("витамин"))
    assert wire.seen() == ["✓ витамин"]
    assert _meals(db) == []
    assert reader.text == []


def test_row_6_control_with_no_such_routine_the_name_is_food(db, owner, reader):
    send(text("витамин"))
    assert len(_meals(db)) == 1


def test_row_7_a_clock_moves_the_last_meal(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=40)
    wire = send(text("12:10"))
    db.expire_all()
    assert meal.meal_time.replace(tzinfo=None) == datetime(2026, 10, 26, 8, 10)
    assert meal.time_source == "edited"
    assert len(_meals(db)) == 1
    assert wire.seen() == ["Время приёма теперь пн 26 октября, 12:10: суп."]


def test_row_7_yesterday_with_a_clock(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=40)
    send(text("вчера 23:10"))
    db.expire_all()
    assert meal.meal_time.replace(tzinfo=None) == datetime(2026, 10, 25, 19, 10)


def test_row_7_no_meal_within_a_day_changes_nothing(db, owner, reader, clock):
    _meal_now(db, owner, minutes_ago=25 * 60)
    wire = send(text("12:10"))
    assert wire.seen() == [
        "Похоже на время, но не ясно, к какому приёму. В дневнике ничего не "
        "поменялось. Время можно указать ответом на нужную запись."
    ]


def test_row_8_more_food_within_three_hours_joins_the_last_meal(
    db, owner, reader, clock
):
    meal = _meal_now(db, owner, minutes_ago=60)
    wire = send(text("ещё сыр"))
    # Born as its own row, merged: one meal to every reader.
    merged = [m for m in _meals(db) if m.id != meal.id]
    assert [m.merged_into_id for m in merged] == [meal.id]
    assert merged[0].deleted_at is not None
    assert _item_names(db, meal) == ["суп", "сыр"]
    assert wire.seen()[0].endswith(": сыр.")
    assert wire.seen()[0].startswith("Добавлено к приёму пн 26 октября, ")
    assert _sources(db)[0].meal_id == meal.id


def test_row_8_after_four_hours_the_same_words_are_a_new_meal(db, owner, reader, clock):
    old = _meal_now(db, owner, minutes_ago=240)
    send(text("ещё суп"))
    meals = _meals(db)
    assert len(meals) == 2
    assert _item_names(db, old) == ["суп"]
    assert reader.text == ["ещё суп"]


def test_row_8_only_at_the_start_of_the_text(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=30)
    send(text("суп и ещё хлеб"))
    assert len(_meals(db)) == 2
    assert _item_names(db, meal) == ["суп"]


def test_row_8_a_second_portion_is_the_same_dish_again(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=30)
    wire = send(text("добавка"))
    assert _item_names(db, meal) == ["суп", "суп"]
    assert wire.seen()[0].endswith(": суп.")


def test_row_8_remove_on_a_stem(db, owner, reader, clock):
    meal = _meal_now(db, owner, names=("суп", "сыр"), minutes_ago=30)
    wire = send(text("убери сыр"))
    assert _item_names(db, meal) == ["суп"]
    assert wire.seen()[0].startswith("Убрано из приёма пн 26 октября, ")
    assert wire.seen()[0].endswith(": сыр.")
    event_kb = _kb(wire.sent()[0])[0]
    assert event_kb[0].startswith("ml:u:")
    assert event_kb[1] == f"ml:o:{meal.id}:r"


def test_row_8_replace_one_dish_directly(db, owner, reader, clock):
    meal = _meal_now(db, owner, names=("курица",), minutes_ago=30)
    wire = send(text("это была индейка"))
    assert _item_names(db, meal) == ["индейка"]
    assert wire.seen()[0].endswith(": курица → индейка.")
    assert wire.seen()[0].startswith("Заменено в приёме пн 26 октября, ")


def test_row_8_replace_names_the_old_dish(db, owner, reader, clock):
    meal = _meal_now(db, owner, names=("рис", "курица"), minutes_ago=30)
    send(text("не курица, а индейка"))
    assert _item_names(db, meal) == ["рис", "индейка"]


def test_row_8_a_replace_it_cannot_place_asks_and_changes_nothing(
    db, owner, reader, clock
):
    meal = _meal_now(db, owner, names=("рис", "курица"), minutes_ago=30)
    wire = send(text("это была индейка"))
    assert _item_names(db, meal) == ["рис", "курица"]
    assert wire.seen()[0].startswith("<b>Какое блюдо это меняет?</b>\n")
    assert "«это была индейка», приём пн 26 октября" in wire.seen()[0]
    rows = _kb(wire.sent()[0])
    assert rows[-1][0].startswith("ml:kn:")


# --- row 4: a reply ------------------------------------------------------------


def _mapped(db, meal, message_id=777, view="line"):
    db.add(MealMessage(chat_id=CHAT, message_id=message_id, meal_id=meal.id, view=view))
    db.commit()
    return {
        "message_id": message_id,
        "date": NOW_TS,
        "chat": {"id": CHAT, "type": "private"},
    }


def test_row_4_a_bare_number_in_reply_is_grams_not_weight(db, owner, reader, clock):
    from app.models import BodyMetric

    meal = _meal_now(db, owner, names=("рис",), minutes_ago=300)
    wire = send(text("200", reply_to_message=_mapped(db, meal)))
    item = db.scalars(select(MealItem)).one()
    db.refresh(item)
    assert item.grams == 200
    assert db.scalars(select(BodyMetric)).first() is None
    assert wire.seen()[0].startswith("Рис в приёме пн 26 октября, ")
    assert wire.seen()[0].endswith(": 200 г.")


def test_row_4_a_number_with_several_dishes_asks_which(db, owner, reader, clock):
    meal = _meal_now(db, owner, names=("рис", "курица"), minutes_ago=300)
    wire = send(text("200", reply_to_message=_mapped(db, meal)))
    assert wire.seen()[0].startswith("<b>200 г: у какого блюда?</b>\n")
    items = list(db.scalars(select(MealItem).order_by(MealItem.id)))
    rows = _kb(wire.sent()[0])
    assert rows[0][0] == f"ml:ga:{items[0].id}:200:r"
    assert rows[-1][0] == f"ml:r:{meal.id}"


def test_row_4_a_bare_time_in_reply_moves_that_meal(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=300)
    send(text("12:10", reply_to_message=_mapped(db, meal)))
    db.expire_all()
    assert meal.meal_time.replace(tzinfo=None) == datetime(2026, 10, 26, 8, 10)


def test_row_4_words_in_reply_join_that_meal_whatever_its_age(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=2000)
    wire = send(text("оливковое масло", reply_to_message=_mapped(db, meal)))
    assert _item_names(db, meal) == ["суп", "оливковое масло"]
    merged = [m for m in _meals(db) if m.id != meal.id]
    assert [m.merged_into_id for m in merged] == [meal.id]
    assert wire.seen()[0].endswith(": оливковое масло.")


def test_row_4_a_reply_to_a_deleted_meal_makes_a_new_one(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=30)
    reply_to = _mapped(db, meal)
    meal_items.delete_meal(db, owner.id, meal.id)
    wire = send(text("суп", reply_to_message=reply_to))
    assert len(_meals(db)) == 2
    assert wire.seen()[0].startswith(
        "Записано: суп. Тот приём был удалён раньше, поэтому это отдельная запись."
    )


def test_row_4_control_a_reply_to_an_unknown_message_goes_on_down(db, owner, reader):
    from app.models import BodyMetric

    send(
        text(
            "72,5",
            reply_to_message={
                "message_id": 1,
                "date": NOW_TS,
                "chat": {"id": CHAT, "type": "private"},
            },
        )
    )
    assert db.scalars(select(BodyMetric)).one().weight == 72.5


# --- must not wrongly fire -----------------------------------------------------


def test_a_food_with_a_time_word_inside_moves_nothing(db, owner, reader, clock):
    meal = _meal_now(db, owner, minutes_ago=30)
    before = meal.meal_time
    send(text("вчерашний суп"))
    db.expire_all()
    assert meal.meal_time == before
    assert len(_meals(db)) == 2
    assert reader.text == ["вчерашний суп"]


def _tap(application, data: str) -> Update:
    message = _message(text="x", message_id=4900)
    return Update.de_json(
        {
            "update_id": message["message_id"],
            "callback_query": {
                "id": "1",
                "from": message["from"],
                "chat_instance": "c",
                "data": data,
                "message": message,
            },
        },
        application.bot,
    )


def test_a_grams_slot_takes_150_and_outside_it_150_is_a_weight(
    db, owner, reader, clock
):
    from app.models import BodyMetric
    from app.services import meal_card

    meal = _meal_now(db, owner, names=("рис",))
    item = db.scalars(select(MealItem)).one()
    meal_card.handle_callback(db, OWNER, f"ml:g:{item.id}:r")  # opens the slot
    send(text("150"))
    db.refresh(item)
    assert item.grams == 150
    assert db.scalars(select(BodyMetric)).first() is None
    send(text("150"))
    db.expire_all()
    assert db.scalars(select(BodyMetric)).one().weight == 150
    assert meal.deleted_at is None


def test_a_wait_never_eats_food_the_slot_closes_and_the_meal_is_born(
    db, owner, reader, clock
):
    _meal_now(db, owner, names=("рис",))
    item = db.scalars(select(MealItem)).one()
    from app.services import meal_card

    meal_card.handle_callback(db, OWNER, f"ml:g:{item.id}:r")
    db.expire_all()
    assert "ask" in db.get(User, 1).settings  # known-positive: the slot is open
    send(text("суп"))
    assert len(_meals(db)) == 2
    db.expire_all()
    assert "ask" not in db.get(User, 1).settings
    db.refresh(item)
    assert item.grams is None


def test_a_restart_between_the_tap_and_the_answer_keeps_the_slot(
    db, owner, reader, clock
):
    _meal_now(db, owner, names=("рис",))
    item = db.scalars(select(MealItem)).one()

    async def tapped(application, _wire):
        await application.process_update(
            _tap(application, f"ml:g:{item.id}:r"),
        )

    asyncio.run(_with_app(tapped))  # the process ends here, the slot only in the row
    db.expire_all()
    assert "ask" in db.get(User, 1).settings
    send(text("150"))  # a new application, as after a restart
    db.refresh(item)
    assert item.grams == 150
    db.expire_all()
    assert "ask" not in db.get(User, 1).settings


def test_a_photo_in_a_slot_is_a_meal_too(db, owner, reader, clock):
    _meal_now(db, owner, names=("рис",))
    item = db.scalars(select(MealItem)).one()
    from app.services import meal_card

    meal_card.handle_callback(db, OWNER, f"ml:g:{item.id}:r")
    send(photo())
    assert len(_meals(db)) == 2


def test_after_the_weight_screen_food_is_food(db, owner, reader):
    wire = send(text("⚖️ Мой вес"), text("суп"))
    assert len(_meals(db)) == 1
    assert not any("Не похоже на вес" in t for t in wire.seen())


def test_after_the_weight_screen_a_number_is_still_a_weight(db, owner, reader):
    from app.models import BodyMetric

    send(text("⚖️ Мой вес"), text("72,5"))
    assert _meals(db) == []
    assert db.scalars(select(BodyMetric)).one().weight == 72.5


def test_a_photo_while_a_routine_name_is_waited_for_is_a_meal_and_leaves_the_wait(
    db, owner, reader
):
    async def steps(application, wire):
        conv = next(
            h for h in application.handlers[0] if isinstance(h, tg.ConversationHandler)
        )
        conv._conversations[(CHAT, OWNER)] = tg.ENTERING_ROUTINE_NAME
        await application.process_update(
            Update.de_json({"update_id": 1, "message": photo()}, application.bot)
        )
        assert conv._conversations.get((CHAT, OWNER)) == tg.CHOOSING_ACTION

    asyncio.run(_with_app(steps))
    assert len(_meals(db)) == 1


# --- replay, concurrency, group chats, the lock --------------------------------


def test_a_replayed_update_makes_one_meal_and_no_second_line(db, owner, reader):
    message = photo()
    wire = send(message, dict(message))
    assert len(_meals(db)) == 1
    assert len(_sources(db)) == 1
    assert len(wire.sent()) == 1
    assert len(reader.image) == 1


def test_a_replayed_text_too(db, owner, reader):
    message = text("суп")
    send(message, dict(message))
    assert len(_meals(db)) == 1


def test_the_handler_returns_before_the_read_ends(db, owner, reader):
    """The slow fake holds its reading; a second update gets through meanwhile."""

    async def steps(application, wire):
        reader.gate = asyncio.Event()
        for message in (photo(), text("суп")):
            await asyncio.wait_for(
                application.process_update(
                    Update.de_json(
                        {"update_id": message["message_id"], "message": message},
                        application.bot,
                    )
                ),
                timeout=2,
            )
        assert [p["text"] for p in wire.sent()][:2] == [
            "Фото в дневнике, читаю…",
            "Записано: суп.\n"
            "На этом всё. Поправить можно кнопками или ответом на это сообщение.",
        ]
        assert wire.edits() == []  # known-positive: the reads are still held
        reader.gate.set()
        await asyncio.gather(*list(tg._meal_reads))
        assert wire.edits()

    asyncio.run(_with_app(steps))
    assert len(_meals(db)) == 2


def test_the_meter_records_inside_the_task(db, owner, reader, monkeypatch):
    """The cost line is what that read cost, on a line of its own."""
    from app.services import usage_meter

    owner.settings = {**owner.settings, "show_costs": True}
    db.commit()
    monkeypatch.setattr(tg.settings_screen, "shows_costs", lambda db, tid: True)
    answer = reader.answer

    async def resolve(payload, *, telegram_id, input_ref, caption=None):
        usage_meter.record(
            "gpt-4o-mini", SimpleNamespace(prompt_tokens=1000, completion_tokens=100)
        )
        return ResolutionResult(
            nutrition=dict(answer),
            source="vision",
            confidence_tier="low",
            description=None,
            portion_grams=None,
            signals={},
        )

    monkeypatch.setattr(tg, "resolve_meal_nutrition", resolve)
    wire = send(photo())
    lines = wire.edits()[-1]["text"].split("\n")
    assert lines[0] == "Записано: рис, курица и овощи."
    assert "$" in lines[-1]  # known-positive: a recorded call shows


def test_in_a_group_chat_nothing_is_captured(db, owner, reader):
    message = text("суп")
    message["chat"] = {"id": -100, "type": "group"}
    wire = send(message)
    assert _meals(db) == []
    assert wire.seen() == []


def test_a_non_subscriber_gets_the_lock_and_no_capture(db, owner, reader, monkeypatch):
    message = photo()
    message["from"]["id"] = 77
    message["chat"]["id"] = 77
    monkeypatch.setattr(settings, "BOT_ACCESS_MODE", "open")
    wire = send(message)
    assert _meals(db) == []
    assert _sources(db) == []
    assert wire.seen()[0].startswith("🔒 Эта функция доступна только")
    assert reader.image == []


def test_a_non_subscriber_can_ask_about_the_subscription_with_no_start_first(
    db, owner, reader, monkeypatch
):
    message = text("💎 Получить подписку")
    message["from"]["id"] = 77
    message["chat"]["id"] = 77
    monkeypatch.setattr(settings, "BOT_ACCESS_MODE", "open")
    wire = send(message)
    assert not wire.seen()[0].startswith("🔒")


def test_logs_carry_no_body(db, owner, reader, caplog):
    reader.error = ValueError("boom")
    with caplog.at_level("DEBUG"):
        wire = send(text("секретный суп"))
    # The body did pass through here, and the failure was logged: a control. The
    # library's own DEBUG echoes what it sends; the app runs at LOG_LEVEL, INFO by default.
    assert "секретный суп" in wire.sent()[0]["text"]
    ours = [r for r in caplog.records if r.name.startswith("app.")]
    assert any("not read: ValueError" in r.getMessage() for r in ours)
    assert not any("секретный суп" in r.getMessage() for r in ours)
    assert not any("секретный суп" in str(r.exc_text) for r in ours)


# --- what the audit trail keeps ------------------------------------------------


def test_the_diagnostic_log_still_gets_its_row(db, owner, reader):
    send(text("суп"))
    row = db.scalars(select(InboundMessage)).one()
    assert (row.kind, row.status) == ("text", "analyzed")


def test_a_failed_read_leaves_the_diagnostic_row_for_reprocess(db, owner, reader):
    reader.error = ValueError("boom")
    send(text("суп"))
    assert db.scalars(select(InboundMessage)).one().status == "failed"


def test_the_reading_row_keeps_what_was_read(db, owner, reader):
    send(photo())
    reading = db.scalars(select(MealReading)).one()
    assert reading.kind == "vision"
    assert reading.status == "done"
    assert reading.applied is True
    assert reading.output["foods"] == NUTRITION["foods"]
    assert reading.source_ids == [_sources(db)[0].id]


# --- the menu button, the card, the owner's alerts, /reprocess -----------------


def test_the_menu_button_is_a_hint_with_favorites_and_no_state(db, owner, reader):
    wire = send(text("🍽 Добавить приём пищи"), text("суп"))
    assert wire.seen()[0] == (
        "<b>🍽 Новый приём</b>\nПришли фото, голосовое или напиши, что было. "
        "Время и остальное можно поправить потом."
    )
    assert _kb(wire.sent()[0]) == [["fav:p:0"]]
    assert len(_meals(db)) == 1


def test_an_old_label_of_the_menu_button_still_works(db, owner, reader):
    wire = send(text("🍽 Добавить прием пищи"))
    assert _meals(db) == []
    assert "Новый приём" in wire.seen()[0]


def test_out_of_credit_says_m7_and_tells_the_owner_apart(
    db, owner, reader, monkeypatch
):
    monkeypatch.setattr(tg.ai_errors, "out_of_credit", lambda e: e)
    monkeypatch.setattr(tg.ai_errors, "billing", lambda e: ("OpenAI", "тут"))
    reader.error = RuntimeError("insufficient_quota")
    wire = send(photo())
    assert wire.edits()[-1]["text"].startswith("Фото сохранено, распознать")
    assert any("закончился баланс" in p["text"] for p in wire.sent())


def test_a_dead_model_offers_the_owner_the_picker_and_a_pick_reads_again(
    db, owner, reader, monkeypatch
):
    from app.services.openai_service import ModelUnavailableError

    reader.error = ModelUnavailableError("gpt-old", RuntimeError("gone"), task="vision")

    async def candidates(task, exclude=None):
        return ["gpt-new"]

    picked = []
    service = SimpleNamespace(
        list_candidates=candidates,
        runs_on_default=lambda task: False,
        model_for=lambda task: "m",
        set_model=lambda m, task=None: picked.append((m, task)),
    )
    monkeypatch.setattr(tg.telegram_service, "openai_service", service)
    monkeypatch.setattr(tg, "persist_task_model", lambda task, m: None)
    monkeypatch.setattr(tg, "persist_model", lambda m: None)

    async def steps(application, wire):
        for_each = [photo()]
        for message in for_each:
            await application.process_update(
                Update.de_json({"update_id": 1, "message": message}, application.bot)
            )
        await asyncio.gather(*list(tg._meal_reads))
        assert any("недоступна" in p["text"] for p in wire.sent())
        assert db.query(Meal).one().reading_status == "failed"
        reader.error = None
        await application.process_update(
            Update.de_json(
                {"update_id": 2, "message": text("gpt-new")}, application.bot
            )
        )
        await asyncio.gather(*list(tg._meal_reads))
        assert picked == [("gpt-new", "vision")]
        db.expire_all()
        assert db.query(Meal).count() == 1  # the pick is no meal
        assert db.query(Meal).one().reading_status == "read"

    asyncio.run(_with_app(steps))


def test_reprocess_fills_the_meal(db, owner, reader):
    reader.error = ValueError("boom")
    send(text("суп"))
    meal = _meals(db)[0]
    assert meal.reading_status == "failed"
    reader.error = None
    reader.text.clear()
    wire = send(
        text(
            "/reprocess", entities=[{"type": "bot_command", "offset": 0, "length": 10}]
        )
    )
    db.expire_all()
    assert meal.reading_status == "read"
    assert _item_names(db, meal) == NUTRITION["foods"]
    assert reader.text == ["суп"]
    assert db.scalars(select(InboundMessage)).one().status == "analyzed"
    assert "успешно 1" in wire.seen()[-1]


def test_reprocess_never_sends_a_voice_rows_blank_to_the_text_model(db, owner, reader):
    send(_voice())
    reader.text.clear()
    send(
        text(
            "/reprocess", entities=[{"type": "bot_command", "offset": 0, "length": 10}]
        )
    )
    assert reader.text == []


def test_a_voice_nobody_could_hear_does_not_hang_on_the_loader(db, owner, reader):
    wire = send(_voice())
    meal = _meals(db)[0]
    source = _sources(db)[0]
    assert (source.kind, source.file_id, source.duration_s) == ("voice", "v", 3)
    assert meal.reading_status == "failed"
    assert wire.edits()[-1]["text"] == (
        "Голосовое сохранено, разобрать сейчас не вышло.\n"
        "В дневнике запись с этим голосовым."
    )
    assert reader.text == []


# --- the readers ----------------------------------------------------------------


def test_a_draft_meal_is_in_no_reader(db, owner):
    from app.crud.crud_meal import crud_meal

    meal = _meal_now(db, owner)
    draft = _meal_now(db, owner)
    draft.is_draft = True
    db.commit()
    day = NOW - timedelta(days=1), NOW + timedelta(days=1)
    assert [m.id for m in crud_meal.get_user_meals(db, owner.id)] == [meal.id]
    assert [m.id for m in crud_meal.get_user_meals_between(db, owner.id, *day)] == [
        meal.id
    ]
    assert [m.id for m in crud_meal.get_multi(db)] == [meal.id]
    assert [m.id for m in crud_meal.get_for_export(db)] == [meal.id]


def test_retagging_skips_a_meal_not_yet_read_and_a_draft(db, owner):
    from app.services import food_groups_retag

    meal = _meal_now(db, owner)
    pending = _meal_now(db, owner)
    pending.reading_status = "pending"
    draft = _meal_now(db, owner)
    draft.is_draft = True
    db.commit()
    assert [row.id for row in food_groups_retag.pending(db)] == [meal.id]


def test_the_retired_steps_are_gone():
    for name in (
        "ADDING_MEAL",
        "ADDING_MEAL_TIME",
        "CONFIRMING_MEAL",
        "confirm_meal",
        "process_meal_time",
    ):
        assert not hasattr(tg, name), name
    from app.services import meal_draft

    for name in ("markup", "text", "set_size", "start"):
        assert not hasattr(meal_draft, name), name


# --- the line and the album -----------------------------------------------------


def test_a_tap_on_the_pending_line_keeps_the_late_read_off_it(db, owner, reader):
    """Known-positive: without the tap the same read edits the same line."""

    async def steps(application, wire):
        reader.gate = asyncio.Event()
        message = photo()
        await application.process_update(
            Update.de_json(
                {"update_id": message["message_id"], "message": message},
                application.bot,
            )
        )
        line_id = wire.sent()[0]
        meal = db.query(Meal).one()
        await application.process_update(
            Update.de_json(
                {
                    "update_id": 9,
                    "callback_query": {
                        "id": "1",
                        "from": message["from"],
                        "chat_instance": "c",
                        "data": f"ml:t:{meal.id}:r",
                        "message": {
                            "message_id": 5001,
                            "date": NOW_TS,
                            "chat": message["chat"],
                            "text": line_id["text"],
                        },
                    },
                },
                application.bot,
            )
        )
        tapped_edits = len(wire.edits())
        assert tapped_edits == 1  # the card replaced the line
        reader.gate.set()
        await asyncio.gather(*list(tg._meal_reads))
        assert len(wire.edits()) == tapped_edits

    asyncio.run(_with_app(steps))
    reading = db.scalars(select(MealReading)).one()
    assert reading.status == "done"


def test_album_photos_share_one_meal_one_line_and_one_read(
    db, owner, reader, monkeypatch
):
    from app.services import capture

    monkeypatch.setattr(capture, "ALBUM_WAIT_SECONDS", 0.05)
    first, second = photo(media_group_id="a1"), photo(media_group_id="a1")
    reader.gate = None

    async def steps(application, wire):
        reader.gate = asyncio.Event()
        for message in (first, second):
            await application.process_update(
                Update.de_json(
                    {"update_id": message["message_id"], "message": message},
                    application.bot,
                )
            )
        assert len(wire.sent()) == 1
        assert wire.edits()[0]["text"] == "2 фото в дневнике, читаю…"
        reader.gate.set()
        await asyncio.gather(*list(tg._meal_reads))
        assert wire.edits()[-1]["text"].startswith("Записано по 2 фото: рис")

    asyncio.run(_with_app(steps))
    meal = _meals(db)[0]
    assert len(_meals(db)) == 1
    assert meal.photos == ["big", "big"]
    assert [s.meal_id for s in _sources(db)] == [meal.id, meal.id]
    assert len(reader.image) == 1


def test_different_albums_are_different_meals(db, owner, reader):
    send(photo(media_group_id="a1"), photo(media_group_id="a2"))
    assert len(_meals(db)) == 2


def _hint_seen(db, owner) -> None:
    owner.settings = {**owner.settings, "capture_hint_seen": True}
    db.commit()


def test_a_line_that_reads_the_same_still_gets_the_fix_for_its_one_dish(
    db, owner, reader
):
    """Typed words leave the text as it was, but the items came only with the read: the
    M1 rule «with one dish, ✏️ Исправить is ml:i» has to reach the line all the same."""
    _hint_seen(db, owner)
    reader.answer = {**NUTRITION, "foods": ["суп"]}
    wire = send(text("суп"))
    db.expire_all()
    meal = _meals(db)[0]
    item = crud_meal_item.live(db, meal.id)[0]
    assert wire.sent()[0]["text"] == "Записано: суп."
    assert _kb(wire.sent()[0])[0][0] == f"ml:o:{meal.id}:r"  # no items yet
    assert [e["text"] for e in wire.edits()] == ["Записано: суп."]
    assert _kb(wire.edits()[0])[0] == [f"ml:i:{item.id}:r", f"ml:t:{meal.id}:r"]


def test_a_line_that_reads_the_same_with_many_dishes_is_left_alone(db, owner, reader):
    """Known-positive: its keyboard is right from the start, an edit would be refused."""
    _hint_seen(db, owner)
    reader.answer = {**NUTRITION, "foods": ["суп", "хлеб"]}
    wire = send(text("суп и хлеб"))
    assert wire.sent()[0]["text"] == "Записано: суп и хлеб."
    assert wire.edits() == []
