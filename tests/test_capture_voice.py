"""M4: a voice note, through the real handlers.

Every test sends a real ``Update`` into ``create_bot_application()``. The local service
is a real HTTP server on the loopback, the paid door the SDK's own ``create`` replaced by
a recording fake: no call leaves the machine, and nothing here is paid for.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from openai.resources.audio.transcriptions import AsyncTranscriptions
from sqlalchemy import select

from app.core.config import settings
from app.models import AiCallLog, Meal, MealReading, MealSource, RecordEvent
from app.services import capture, meal_card
from app.services import telegram as tg
from app.services import usage_meter, voice
from app.services.openai_service import OpenAIService
from tests.test_capture_failures import (  # noqa: F401 (the autouse fixture)
    _a_clean_run,
    _reprocess,
    _tap_update,
    photo,
)
from tests.test_capture_failures import tap as _tap
from tests.test_capture_router import (  # noqa: F401 (fixtures)
    NUTRITION,
    OWNER,
    Session,
    Wire,
    _item_names,
    _kb,
    _message,
    _voice,
    _with_app,
    clock,
    db,
    owner,
    reader,
    send,
)

REPO = Path(__file__).resolve().parent.parent


def tap(data: str, message_id: int = 4900) -> Wire:
    """Each tap has its own run of the bot, and the delivery would hold on to the message
    of the last one, and through it that run's wire."""
    tg._card_delivery._screens.clear()
    return _tap(data, message_id)


OGG = b"OggS\x00\x02" + b"\x00" * 40
MP4 = b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 40
HEARD = "Два яйца, один… нет, хлеб и кофе"
M4A = "Записано: рис, курица и овощи."
M4C = (
    "Голосовые пока в пробном режиме, но запись уже в дневнике. Что услышано, видно "
    "по кнопке «🎙 Текст». Если сервис проекта не отвечает, голосовое уходит во "
    "внешний сервис."
)
M4D = "Голосовое сохранено, разобрать сейчас не вышло.\nВ дневнике запись с этим голосовым."
M4E = "В голосовом не слышно речи, в дневник не записано."
M7B = capture.M7B_ASK


class FakeWhisper:
    """The shared local service, as far as this bot knows it."""

    def __init__(self):
        self.status = 200
        self.body = json.dumps({"text": HEARD, "language": "ru"}).encode()
        self.delay = 0.0
        self.calls: list = []
        self.on_call = None
        owner_ = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length", 0))
                raw = self.rfile.read(length)
                owner_.calls.append({"path": self.path, "raw": raw})
                if owner_.on_call:
                    owner_.on_call()
                if owner_.delay:
                    time.sleep(owner_.delay)
                self.send_response(owner_.status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(owner_.body)))
                self.end_headers()
                try:
                    self.wfile.write(owner_.body)
                except OSError:
                    pass  # the client gave up: that is the point of that test

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def answers(self, text: str) -> None:
        self.body = json.dumps({"text": text, "language": "ru"}).encode()

    def refuses(self, status: int, body: bytes = b'{"detail": "no"}') -> None:
        self.status, self.body = status, body

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def whisper(monkeypatch):
    fake = FakeWhisper()
    monkeypatch.setattr(settings, "WHISPER_URL", fake.url)
    yield fake
    fake.close()


@pytest.fixture
def paid(monkeypatch):
    """The SDK's own door: what ``OpenAIService.transcribe`` really asked for."""
    state = SimpleNamespace(calls=[], text="Суп с паид", error=None)

    async def create(self, **kwargs):
        state.calls.append({**kwargs, "max_retries": self._client.max_retries})
        if state.error is not None:
            raise state.error
        usage = SimpleNamespace(input_tokens=1000, output_tokens=100)
        return SimpleNamespace(text=state.text, usage=usage)

    monkeypatch.setattr(AsyncTranscriptions, "create", create)
    return state


@pytest.fixture(autouse=True)
def _ogg(monkeypatch):
    monkeypatch.setattr(Wire, "file_bytes", OGG)


@pytest.fixture
def seen(db, owner):
    """A person past both hints, so the lines are the plain ones."""
    owner.settings = {
        **owner.settings,
        "voice_hint_seen": True,
        "capture_hint_seen": True,
    }
    db.commit()
    return owner


def _meal(db) -> Meal:
    db.expire_all()
    return db.scalars(select(Meal).order_by(Meal.id)).first()


def _source(db) -> MealSource:
    db.expire_all()
    return db.scalars(select(MealSource).order_by(MealSource.id)).first()


def _readings(db, status=None) -> list:
    db.expire_all()
    stmt = select(MealReading).order_by(MealReading.id)
    if status:
        stmt = stmt.where(MealReading.status == status)
    return list(db.scalars(stmt))


def _labels(params) -> list:
    markup = params.get("reply_markup")
    if isinstance(markup, str):
        markup = json.loads(markup)
    if hasattr(markup, "to_dict"):
        markup = markup.to_dict()
    return [[b["text"] for b in row] for row in markup["inline_keyboard"]]


def _audio_dir() -> Path:
    return Path(settings.AUDIO_DIR)


def _in_day(db) -> list:
    db.expire_all()
    return list(db.scalars(select(Meal).where(Meal.deleted_at.is_(None))))


# --- M4, M4a: the local service answers ---------------------------------------------


def test_a_voice_is_read_by_the_local_service_and_nothing_is_paid(
    db, seen, reader, whisper, paid
):
    wire = send(_voice())
    meal = _meal(db)
    assert wire.sent()[0]["text"] == "Голосовое в дневнике, слушаю…"
    assert wire.edits()[-1]["text"] == M4A
    assert _kb(wire.edits()[-1]) == [
        [f"ml:vt:{meal.id}:1", f"ml:o:{meal.id}:r", f"ml:t:{meal.id}:r"]
    ]
    assert _labels(wire.edits()[-1])[0][0] == "🎙 Текст"
    assert reader.text == [HEARD]
    assert paid.calls == []
    assert meal.reading_status == "read"
    heard = _readings(db, "heard")[0]
    assert heard.kind == "transcript"
    assert heard.backend == "local"
    assert heard.output["transcript"] == HEARD
    assert heard.source_ids == [_source(db).id]


def test_the_local_service_is_asked_at_its_openai_door_in_russian(
    db, seen, reader, whisper
):
    send(_voice())
    call = whisper.calls[0]
    assert call["path"] == "/v1/audio/transcriptions"
    raw = call["raw"]
    assert b'name="language"\r\n\r\nru' in raw
    assert b'name="response_format"\r\n\r\njson' in raw
    assert b'name="prompt"' in raw
    assert OGG in raw


def test_a_trailing_slash_in_the_setting_makes_no_double_slash(
    db, seen, reader, whisper, monkeypatch
):
    monkeypatch.setattr(settings, "WHISPER_URL", whisper.url + "/")
    send(_voice())
    assert whisper.calls[0]["path"] == "/v1/audio/transcriptions"


def test_the_bytes_are_on_disk_before_anyone_listens(db, seen, reader, whisper):
    present = []
    whisper.on_call = lambda: present.extend(
        p.read_bytes() for p in _audio_dir().iterdir()
    )
    send(_voice())
    assert present == [OGG]
    source = _source(db)
    assert source.audio_path == f"{source.id}.ogg"
    assert (_audio_dir() / source.audio_path).read_bytes() == OGG
    # Only the person's process reads a person's voice.
    assert (_audio_dir() / source.audio_path).stat().st_mode & 0o077 == 0


def test_a_disk_that_refuses_costs_the_copy_not_the_read(
    db, seen, reader, whisper, monkeypatch, tmp_path, caplog
):
    blocked = tmp_path / "a-file"
    blocked.write_text("x")
    monkeypatch.setattr(settings, "AUDIO_DIR", str(blocked / "voice"))
    with caplog.at_level(logging.WARNING):
        wire = send(_voice())
    assert wire.edits()[-1]["text"] == M4A
    assert _source(db).audio_path is None
    assert "Voice not saved" in caplog.text
    assert str(tmp_path) not in caplog.text


@pytest.mark.parametrize(
    "data,ext", [(OGG, "ogg"), (MP4, "mp4"), (b"ID3\x04" + b"\x00" * 40, "mp3")]
)
def test_the_extension_follows_the_bytes(
    db, seen, reader, whisper, paid, monkeypatch, data, ext
):
    monkeypatch.setattr(Wire, "file_bytes", data)
    send(_voice())
    assert f'filename="voice.{ext}"'.encode() in whisper.calls[0]["raw"]
    assert _source(db).audio_path.endswith(f".{ext}")


def test_the_extension_reaches_the_paid_door_too(db, seen, reader, paid, monkeypatch):
    monkeypatch.setattr(Wire, "file_bytes", MP4)
    send(_voice())
    assert paid.calls[0]["file"] == ("voice.mp4", MP4)


# --- the fallback -------------------------------------------------------------------


def _closed_port() -> str:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{probe.getsockname()[1]}"


def test_an_unreachable_service_goes_to_the_paid_door(
    db, seen, reader, paid, monkeypatch
):
    monkeypatch.setattr(settings, "WHISPER_URL", _closed_port())
    wire = send(_voice())
    assert wire.edits()[-1]["text"] == M4A
    assert len(paid.calls) == 1
    assert reader.text == ["Суп с паид"]
    assert _readings(db, "heard")[0].backend == "openai"


@pytest.mark.parametrize(
    "status,body",
    [
        (500, b'{"detail": "failed"}'),
        (503, b'{"detail": "cancelled"}'),
        (200, b"not json"),
        (200, b'{"text": "   "}'),
        (200, b'{"text": null}'),
        (200, b"[1]"),
    ],
)
def test_a_service_that_refuses_goes_to_the_paid_door(
    db, seen, reader, whisper, paid, status, body
):
    whisper.refuses(status, body)
    wire = send(_voice())
    assert len(whisper.calls) == 1
    assert len(paid.calls) == 1
    assert wire.edits()[-1]["text"] == M4A
    assert _readings(db, "heard")[0].backend == "openai"


def test_a_service_out_of_time_goes_to_the_paid_door(
    db, seen, reader, whisper, paid, monkeypatch
):
    monkeypatch.setattr(voice, "LOCAL_TIMEOUT_S", 0.1)
    whisper.delay = 1.0
    wire = send(_voice())
    assert len(paid.calls) == 1
    assert wire.edits()[-1]["text"] == M4A


def test_the_local_wait_is_two_minutes():
    assert voice.LOCAL_TIMEOUT_S == 120.0


def test_empty_setting_goes_straight_to_the_paid_door(db, seen, reader, paid, caplog):
    assert settings.WHISPER_URL == ""
    with caplog.at_level(logging.INFO):
        wire = send(_voice())
    assert wire.edits()[-1]["text"] == M4A
    assert len(paid.calls) == 1
    assert _readings(db, "heard")[0].backend == "openai"
    # Straight: no address made of nothing was tried and failed on the way.
    assert "Local whisper" not in caplog.text
    assert "backend=openai" in caplog.text


def test_a_proxy_in_the_environment_does_not_stand_before_the_local_service(
    db, seen, reader, whisper, paid, monkeypatch
):
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, _closed_port())
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    send(_voice())
    assert len(whisper.calls) == 1
    assert paid.calls == []


def test_the_paid_door_is_asked_as_decided(db, seen, reader, paid):
    send(_voice())
    call = paid.calls[0]
    assert call["model"] == "gpt-4o-mini-transcribe"
    assert call["language"] == "ru"
    assert call["response_format"] == "json"
    assert call["timeout"] == 30.0
    assert call["max_retries"] == 0
    assert call["prompt"]
    assert _readings(db, "heard")[0].model == "gpt-4o-mini-transcribe"


def test_a_paid_note_is_metered_at_its_price(paid):
    with usage_meter.metering() as meter:
        asyncio.run(OpenAIService().transcribe(OGG, "voice.ogg"))
    assert meter.calls == 1
    # 1000 in at 1.25, 100 out at 5.00, per million.
    assert meter.usd == pytest.approx(0.00175)


def test_every_price_needs_its_model_or_the_meter_goes_blank():
    # The control of the one above: a model not in the table has no estimate.
    usage = SimpleNamespace(input_tokens=1000, output_tokens=100)
    assert usage_meter._estimate("gpt-4o-mini-transcribe", usage) is not None
    assert usage_meter._estimate("no-such-model", usage) is None


# --- M4e: no speech -----------------------------------------------------------------


def test_no_speech_takes_no_paid_call_and_stays_out_of_the_day(
    db, seen, reader, whisper, paid
):
    whisper.refuses(422, b'{"detail": "no speech"}')
    wire = send(_voice())
    meal = _meal(db)
    assert paid.calls == []
    assert reader.text == []
    assert wire.edits()[-1]["text"] == M4E
    assert _kb(wire.edits()[-1]) == [[f"ml:w:{meal.id}:r"]]
    assert _labels(wire.edits()[-1]) == [["✏️ Написать"]]
    assert meal.reading_status == "no_speech"
    assert _in_day(db) == []
    source = _source(db)
    assert source.meal_id == meal.id
    assert (_audio_dir() / source.audio_path).read_bytes() == OGG
    assert _readings(db)[-1].status == "no_speech"


def test_no_speech_is_never_read_again_by_itself(db, seen, reader, whisper, paid):
    whisper.refuses(422)
    send(_voice())
    assert capture.due_retries(db, 10) == []
    assert capture.unread_meals(db, 10) == []
    assert capture.pending_meals(db) == []


def test_blank_words_from_the_paid_door_are_no_speech_too(db, seen, reader, paid):
    paid.text = "  "
    wire = send(_voice())
    assert wire.edits()[-1]["text"] == M4E
    assert reader.text == []


def test_written_words_bring_a_silent_voice_into_the_day(db, seen, reader, whisper):
    whisper.refuses(422)
    send(_voice())
    meal = _meal(db)
    wire = tap(f"ml:w:{meal.id}:r")
    assert wire.edits()[-1]["text"] == M7B
    assert _kb(wire.edits()[-1]) == [[f"ml:r:{meal.id}"]]
    # Asked again by the back button, the line still says what it said.
    back = tap(f"ml:r:{meal.id}")
    assert back.edits()[-1]["text"] == M4E
    tap(f"ml:w:{meal.id}:r")
    send(_message(text="суп"))
    meal = _meal(db)
    assert meal.deleted_at is None
    assert meal.reading_status == "read"
    assert _item_names(db, meal) == NUTRITION["foods"]
    assert _source(db).audio_path
    assert [m.id for m in _in_day(db)] == [meal.id]


def test_a_reply_to_the_silent_line_is_a_meal_of_its_own_with_no_word_of_deleting(
    db, seen, reader, whisper
):
    whisper.refuses(422)
    wire = send(_voice())
    silent = _meal(db)
    line = wire.edits()[-1]["message_id"]
    answer = send(
        _message(
            text="борщ",
            reply_to_message={
                "message_id": line,
                "date": 0,
                "chat": {"id": _message()["chat"]["id"], "type": "private"},
            },
        )
    )
    assert not any("удал" in text.casefold() for text in answer.seen())
    live = _in_day(db)
    assert len(live) == 1
    assert live[0].id != silent.id


def test_silence_after_the_person_wrote_keeps_what_they_wrote(
    db, seen, reader, whisper
):
    gate = threading.Event()
    whisper.refuses(422)
    whisper.on_call = gate.wait

    async def steps(application, wire):
        await application.process_update(_update(application, _voice()))
        meal = _meal(db)
        await application.process_update(
            _tap_update(application, f"ml:w:{meal.id}:r", 4900)
        )
        await application.process_update(_update(application, _message(text="суп")))
        gate.set()
        await asyncio.gather(*list(tg._meal_reads))

    asyncio.run(_with_app(steps))
    meal = _meal(db)
    assert meal.deleted_at is None
    assert meal.reading_status == "read"
    assert _item_names(db, meal) == NUTRITION["foods"]
    # What the service said is still on record.
    assert any(r.status == "no_speech" for r in _readings(db))


def _update(application, message):
    from telegram import Update

    return Update.de_json(
        {"update_id": message["message_id"], "message": message}, application.bot
    )


# --- a voice with words and no food -------------------------------------------------


def test_words_with_no_food_stay_out_of_the_day_and_can_be_written(
    db, seen, reader, whisper
):
    reader.answer = {"foods": [], "calories": 0, "protein": 0, "fats": 0, "carbs": 0}
    wire = send(_voice())
    meal = _meal(db)
    assert wire.edits()[-1]["text"] == "Еды здесь не видно, в дневник не записано."
    assert "Фото" not in wire.edits()[-1]["text"]
    assert _kb(wire.edits()[-1]) == [[f"ml:w:{meal.id}:r"]]
    assert _in_day(db) == []
    assert tap(f"ml:w:{meal.id}:r").edits()[-1]["text"] == M7B
    send(_message(text="суп"))
    assert _item_names(db, _meal(db)) == ["суп"]
    assert _in_day(db)


# --- M4d: nobody could listen -------------------------------------------------------


def test_both_failed_the_line_says_so_and_the_voice_stays(
    db, seen, reader, whisper, paid
):
    whisper.refuses(500)
    paid.error = ValueError("boom")
    wire = send(_voice())
    meal = _meal(db)
    assert wire.edits()[-1]["text"] == M4D
    assert _kb(wire.edits()[-1]) == [[f"ml:rr:{meal.id}:r", f"ml:w:{meal.id}:r"]]
    assert meal.reading_status == "failed"
    assert reader.text == []
    assert (_audio_dir() / _source(db).audio_path).read_bytes() == OGG
    assert _readings(db)[-1].error == "ValueError"


def test_again_by_hand_may_pay_and_downloads_nothing_twice(
    db, seen, reader, whisper, paid
):
    whisper.refuses(500)
    paid.error = ValueError("boom")
    send(_voice())
    meal = _meal(db)
    paid.error = None
    wire = tap(f"ml:rr:{meal.id}:r")
    assert [name for name, _ in wire.calls if name == "getFile"] == []
    assert len(paid.calls) == 2
    assert wire.edits()[-1]["text"] == M4A


def _read_by_itself(db) -> Wire:
    box = {}

    async def steps(application, wire):
        box["wire"] = wire
        job = capture.read_again(db, _meal(db).id, automatic=True)
        await tg._read_meal(application, job)

    asyncio.run(_with_app(steps))
    return box["wire"]


def test_a_retry_nobody_asked_for_stays_local(db, seen, reader, whisper, paid):
    whisper.refuses(500)
    paid.error = ValueError("boom")
    send(_voice())
    before = len(paid.calls)
    _read_by_itself(db)
    assert len(paid.calls) == before
    assert len(whisper.calls) == 2
    assert _readings(db)[-1].status == capture.RETRY
    assert _meal(db).read_attempts == 1
    # The service is back: it reads, and still nothing is paid.
    whisper.refuses(200, json.dumps({"text": HEARD}).encode())
    wire = _read_by_itself(db)
    assert len(paid.calls) == before
    assert reader.text == [HEARD]
    assert wire.edits()[-1]["text"] == M4A


def test_a_retry_nobody_asked_for_hears_no_speech_too(db, seen, reader, whisper, paid):
    whisper.refuses(500)
    paid.error = ValueError("boom")
    send(_voice())
    whisper.refuses(422)
    wire = _read_by_itself(db)
    assert wire.edits()[-1]["text"] == M4E
    assert _meal(db).reading_status == "no_speech"


def _replay(db, meals) -> Wire:
    box = {}

    async def steps(application, wire):
        box["wire"] = wire
        await tg._replay_pending(application, meals)
        await asyncio.gather(*list(tg._meal_reads))

    asyncio.run(_with_app(steps))
    return box["wire"]


def test_a_restart_reads_a_pending_voice_locally(
    db, seen, reader, whisper, paid, monkeypatch
):
    whisper.refuses(500)
    paid.error = ValueError("boom")
    send(_voice())
    meal = _meal(db)
    meal.reading_status = "pending"
    db.commit()
    whisper.refuses(200, json.dumps({"text": HEARD}).encode())
    before = len(paid.calls)
    _replay(db, [meal.id])
    assert reader.text == [HEARD]
    assert len(paid.calls) == before
    assert _meal(db).reading_status == "read"


def test_with_no_local_service_a_restart_gives_up_and_pays_nothing(
    db, seen, reader, paid
):
    paid.error = ValueError("boom")
    send(_voice())
    meal = _meal(db)
    meal.reading_status = "pending"
    # The line of a read the restart cut off is still on «слушаю».
    db.scalar(
        select(capture.MealMessage).where(capture.MealMessage.view == "line")
    ).view = "pending"
    db.commit()
    before = len(paid.calls)
    wire = _replay(db, [meal.id])
    assert len(paid.calls) == before
    assert wire.edits()[-1]["text"] == M4D
    assert _meal(db).reading_status == "failed"


def test_with_no_local_service_nothing_is_due_but_a_heard_voice_is(
    db, seen, reader, paid, monkeypatch
):
    """The retry rule, on both sides. A transcript kept needs no listening."""
    paid.error = ValueError("boom")
    send(_voice())
    meal = _meal(db)
    assert capture._may_read_by_itself(db, meal) is False
    monkeypatch.setattr(settings, "WHISPER_URL", "http://127.0.0.1:1")
    assert capture._may_read_by_itself(db, meal) is True
    monkeypatch.setattr(settings, "WHISPER_URL", "")
    db.add(
        MealReading(
            meal_id=meal.id,
            source_ids=[],
            kind="transcript",
            status="heard",
            output={"transcript": HEARD},
        )
    )
    db.commit()
    assert capture._may_read_by_itself(db, meal) is True


def test_a_dead_file_stays_on_the_voice_line_never_the_photo_one(
    db, seen, reader, paid, monkeypatch
):
    from telegram import Bot
    from telegram.error import BadRequest

    async def gone(self, *_args, **_kwargs):
        raise BadRequest("Wrong file_id or the file is temporarily unavailable")

    monkeypatch.setattr(Bot, "get_file", gone)
    wire = send(_voice())
    meal = _meal(db)
    assert wire.edits()[-1]["text"] == M4D
    assert paid.calls == []
    again = tap(f"ml:r:{meal.id}")
    assert again.edits()[-1]["text"] == M4D


# --- what was heard is kept ---------------------------------------------------------


def test_a_heard_voice_is_not_listened_to_twice(db, seen, reader, whisper, paid):
    reader.error = ValueError("the text model fell over")
    wire = send(_voice())
    meal = _meal(db)
    assert wire.edits()[-1]["text"] == M4D
    assert _readings(db, "heard")
    reader.error = None
    wire = tap(f"ml:rr:{meal.id}:r")
    assert len(whisper.calls) == 1
    assert paid.calls == []
    assert reader.text == [HEARD, HEARD]
    assert wire.edits()[-1]["text"] == M4A
    assert len(_readings(db, "heard")) == 1


def test_the_text_model_never_gets_a_blank_from_reprocess(
    db, seen, reader, whisper, paid
):
    whisper.refuses(500)
    paid.error = ValueError("boom")
    send(_voice())
    whisper.refuses(200, json.dumps({"text": HEARD}).encode())
    paid.error = None
    send(_reprocess())
    assert reader.text == [HEARD]
    assert _meal(db).reading_status == "read"


def test_a_voice_answering_a_meal_joins_it_at_the_end(db, seen, reader, whisper):
    send(_message(text="суп"))
    first = _meal(db)
    line = db.scalar(
        select(capture.MealMessage.message_id).where(
            capture.MealMessage.meal_id == first.id,
            capture.MealMessage.view != "source",
        )
    )
    reply = {
        "message_id": line,
        "date": 0,
        "chat": {"id": _message()["chat"]["id"], "type": "private"},
    }
    wire = send(_voice() | {"reply_to_message": reply})
    assert reader.text[-1] == HEARD
    assert any("Добавлено" in text for text in wire.seen())
    assert "рис" in _item_names(db, _meal(db))


def test_a_time_said_in_the_note_moves_the_meal_and_stays_out_of_the_dish(
    db, seen, reader, whisper, clock
):
    whisper.answers("вчера вечером суп")
    send(_voice())
    meal = _meal(db)
    assert reader.text == ["суп"]
    assert meal.time_source == "said"
    assert meal.time_phrase == "вчера вечером"
    assert meal.meal_time < meal.sent_at


def test_a_time_the_person_set_meanwhile_is_not_moved(db, seen, reader, whisper, clock):
    whisper.answers("вчера вечером суп")
    send(_voice())
    meal = _meal(db)
    assert meal.time_source == "said"
    job = capture.read_again(db, meal.id)
    meal.time_source = "person"
    moment = meal.meal_time
    db.commit()
    assert capture.spoken_words(db, job, "утром суп") == "суп"
    assert _meal(db).meal_time == moment


# --- M4b: the toggle ----------------------------------------------------------------


def test_the_text_opens_in_place_and_closes_again(db, seen, reader, whisper):
    send(_voice())
    meal = _meal(db)
    wire = tap(f"ml:vt:{meal.id}:1")
    opened = wire.edits()[-1]
    assert opened["text"] == f"{M4A}\n<blockquote expandable>{HEARD}</blockquote>"
    assert opened["parse_mode"] == "HTML"
    assert _labels(opened)[0] == ["✓ 🎙 Текст", "✏️ Исправить", "🕐 Время"]
    assert _kb(opened)[0][0] == f"ml:vt:{meal.id}:0"
    assert wire.sent() == []
    closed = tap(f"ml:vt:{meal.id}:0").edits()[-1]
    assert closed["text"] == M4A
    assert _kb(closed) == [
        [f"ml:vt:{meal.id}:1", f"ml:o:{meal.id}:r", f"ml:t:{meal.id}:r"]
    ]


def test_the_button_carries_the_target_state(db, seen, reader, whisper):
    send(_voice())
    meal = _meal(db)
    first = tap(f"ml:vt:{meal.id}:1").edits()[-1]
    second = tap(f"ml:vt:{meal.id}:1").edits()[-1]
    assert first["text"] == second["text"]


def test_what_was_heard_is_escaped_and_bounded(db, seen, reader, whisper):
    whisper.answers("<b>суп</b> & " + "слово " * 2000)
    send(_voice())
    meal = _meal(db)
    text = tap(f"ml:vt:{meal.id}:1").edits()[-1]["text"]
    assert "&lt;b&gt;суп&lt;/b&gt; &amp;" in text
    assert "<b>" not in text
    assert len(text) <= 4096


def test_a_foreign_or_deleted_meal_shows_no_text(db, seen, reader, whisper):
    send(_voice())
    meal = _meal(db)
    meal.user_id = meal.user_id + 1
    db.commit()
    wire = tap(f"ml:vt:{meal.id}:1")
    assert not any(HEARD in text for text in wire.seen())


def test_a_meal_with_nothing_heard_has_no_button(db, seen, reader):
    wire = send(photo())
    assert not any("🎙" in label for row in _labels(wire.edits()[-1]) for label in row)
    meal = _meal(db)
    answer = tap(f"ml:vt:{meal.id}:1").edits()[-1]
    assert "blockquote" not in answer["text"]


def test_the_button_stays_after_a_way_back_to_the_line(db, seen, reader, whisper):
    send(_voice())
    meal = _meal(db)
    back = tap(f"ml:r:{meal.id}").edits()[-1]
    assert _kb(back)[0][0] == f"ml:vt:{meal.id}:1"


# --- M4c: once ----------------------------------------------------------------------


def test_the_first_voice_explains_itself_once(db, owner, reader, whisper):
    first = send(_voice())
    assert first.edits()[-1]["text"] == f"{M4A}\n{M4C}"
    meal = _meal(db)
    assert _kb(first.edits()[-1]) == [
        [f"ml:vt:{meal.id}:1", f"ml:o:{meal.id}:r", f"ml:t:{meal.id}:r"]
    ]
    db.expire_all()
    assert owner.settings.get("voice_hint_seen") is True
    # The other hint is not spent on a line that carries this one.
    assert not owner.settings.get("capture_hint_seen")
    second = send(_voice())
    assert second.edits()[-1]["text"] == M4A


def test_the_hint_waits_for_a_voice_that_was_written(db, owner, reader, whisper):
    whisper.refuses(422)
    send(_voice())
    db.expire_all()
    assert not owner.settings.get("voice_hint_seen")


# --- the last correction wins -------------------------------------------------------


class _FakeChoice:
    def __init__(self, content):
        self.message = SimpleNamespace(content=content)


def _real_text_model(monkeypatch) -> list:
    asked = []
    answer = json.dumps(
        {
            "calories": 100,
            "protein": 1,
            "fats": 1,
            "carbs": 1,
            "portion": "1",
            "foods": ["хлеб"],
        }
    )

    async def create(self, *, task, messages, **_kwargs):
        asked.append(messages)
        return SimpleNamespace(choices=[_FakeChoice(answer)])

    monkeypatch.setattr(OpenAIService, "_create", create)
    return asked


def test_the_rule_reaches_the_model_for_a_voice_and_only_for_a_voice(
    db, seen, whisper, monkeypatch
):
    asked = _real_text_model(monkeypatch)
    send(_voice())
    assert voice.LAST_CORRECTION_RULE in asked[0][0]["content"]
    assert HEARD in asked[0][1]["content"]
    send(_message(text="суп"))
    assert voice.LAST_CORRECTION_RULE not in asked[-1][0]["content"]
    assert "суп" in asked[-1][1]["content"]


def test_the_call_log_holds_no_transcript(db, seen, whisper, monkeypatch):
    asked = _real_text_model(monkeypatch)
    send(_voice())
    assert asked  # the model was asked, so a row is there to look at
    row = db.scalars(select(AiCallLog)).one()
    assert row.kind == "voice"
    assert HEARD not in (row.input_ref or "")
    assert row.input_ref == "v"


# --- nothing in the logs ------------------------------------------------------------


def test_the_logs_say_who_answered_and_never_what_was_said(
    db, seen, reader, whisper, caplog
):
    secret = "секретнаяфраза"
    whisper.answers(secret)
    with caplog.at_level(logging.DEBUG):
        send(_voice())
    assert reader.text == [secret]  # the words did pass by here
    assert "backend=local" in caplog.text
    assert "took_s=" in caplog.text
    assert "duration_s=3" in caplog.text
    assert secret not in caplog.text
    assert "voice/" not in caplog.text
    assert not any(secret in str(r.error) for r in _readings(db))


def test_a_refusal_body_is_not_logged_either(db, seen, reader, whisper, paid, caplog):
    secret = "секретнаяфраза"
    whisper.refuses(500, json.dumps({"detail": secret}).encode())
    with caplog.at_level(logging.DEBUG):
        send(_voice())
    assert "Local whisper refused: 500" in caplog.text
    assert secret not in caplog.text


# --- the pair of R35's measure ------------------------------------------------------


def test_what_was_heard_and_what_was_corrected_stand_side_by_side(
    db, seen, reader, whisper, clock
):
    send(_voice())
    send(_message(text="не курицу, а индейку"))
    meal = _meal(db)
    heard = _readings(db, "heard")[0]
    assert heard.meal_id == meal.id
    assert heard.output["transcript"] == HEARD
    event = db.scalars(
        select(RecordEvent).where(RecordEvent.meal_id == meal.id)
    ).first()
    assert event is not None


# --- where the bytes live -----------------------------------------------------------


def test_the_voice_folder_is_gitignored():
    check = ["git", "-C", str(REPO), "check-ignore", "-q"]
    assert subprocess.run([*check, "data/voice/1.ogg"]).returncode == 0
    assert subprocess.run([*check, "app/main.py"]).returncode == 1  # the control


def test_the_image_never_bakes_a_voice_in():
    assert "data/" in (REPO / ".dockerignore").read_text().splitlines()


def _service_lines(compose: str, name: str) -> list:
    """One service's lines of a compose file; no YAML parser in this project's deps."""
    lines, inside = [], False
    for line in compose.splitlines():
        if line.startswith("  ") and not line.startswith("   ") and line.strip():
            inside = line.strip() == f"{name}:"
        elif inside:
            lines.append(line.strip())
    return lines


def test_production_keeps_the_voice_on_the_host():
    compose = (REPO / "docker-compose.prod.yml").read_text()
    default = settings.model_fields["AUDIO_DIR"].default
    mounts = [
        line
        for line in _service_lines(compose, "telegram_bot")
        if line.endswith(f":/app/{default}")
    ]
    # A path, not a named volume: a named one dies with the VM. And no machine's path.
    assert mounts == [f"- ./{default}:/app/{default}"]
    # The control: the helper does read this service's volumes.
    assert "- render_jobs:/render" in _service_lines(compose, "telegram_bot")


def test_the_settings_are_documented():
    example = (REPO / ".env.example").read_text()
    assert "WHISPER_URL=" in example
    assert "OPENAI_TRANSCRIBE_MODEL=gpt-4o-mini-transcribe" in example
    assert settings.model_fields["WHISPER_URL"].default == ""
    assert "gpt-4o-mini-transcribe" in usage_meter.PRICES


def test_the_copy_is_in_one_place():
    assert meal_card.M4E_NO_SPEECH == M4E
    assert meal_card.M4C_VOICE_HINT == M4C
    assert meal_card.M4D_FAILED == M4D
