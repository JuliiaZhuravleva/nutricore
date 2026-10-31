"""The read behind the line, section 5.2: in the background, swept, replayed, retried.

Through the real application at the wire, on real rows. The model and the download are
the fakes. No network, no paid call.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import openai
import pytest
from sqlalchemy import select
from telegram import Update
from telegram.error import BadRequest, TimedOut

from app.core.config import settings
from app.models import Meal, MealMessage, MealReading, MealSource
from app.services import capture
from app.services import telegram as tg
from app.services import usage_meter
from app.services.openai_service import ModelUnavailableError
from app.services.product_lookup_service import ResolutionResult
from tests.test_ai_errors import QUOTA_BODY, RATE_BODY, sdk_error
from tests.test_capture_router import (  # noqa: F401 (fixtures)
    CHAT,
    NOW,
    NUTRITION,
    PHOTO,
    Session,
    Wire,
    _item_names,
    _meal_now,
    _message,
    _voice,
    _with_app,
    db,
    owner,
    photo,
    reader,
    send,
    text,
)


@pytest.fixture(autouse=True)
def no_named_emoji(monkeypatch):
    """Whatever the machine's .env says: ``post_init`` runs here and reads no sets."""
    monkeypatch.setattr(settings, "CUSTOM_EMOJI", None)


@pytest.fixture
def at(monkeypatch):
    """The capture's clock; ``at(minutes=6)`` moves it on."""
    now = {"at": NOW}
    monkeypatch.setattr(capture, "_now", lambda: now["at"])

    def move(**delta):
        now["at"] += timedelta(**delta)

    return move


def _update(application, message) -> Update:
    return Update.de_json(
        {"update_id": message["message_id"], "message": message}, application.bot
    )


async def _drain():
    await asyncio.gather(*list(tg._meal_reads))


async def _until(happened) -> None:
    """Until the background got there; the reads run on this loop."""
    for _ in range(400):
        if happened():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("the background never got there")


def _one_meal(db) -> Meal:
    db.expire_all()
    return db.query(Meal).one()


def _readings(db) -> list:
    db.expire_all()
    return list(db.scalars(select(MealReading).order_by(MealReading.id)))


def _sent_one_photo(reader, *, error=None) -> None:
    """A photo read once, with this error from the model."""
    reader.error = error
    send(photo())


def _pending_row(db, owner, *, kind="photo", attempts=0, minutes_ago=0) -> Meal:
    """What a process that died mid-read leaves behind: a pending meal and its line."""
    meal = Meal(
        user_id=owner.id,
        meal_time=NOW,
        sent_at=NOW,
        photos=["big"] if kind == "photo" else [],
        description="овсянка на воде" if kind == "text" else None,
        reading_status="pending",
        read_attempts=attempts,
        numbers_basis="none",
        created_at=NOW - timedelta(minutes=minutes_ago),
    )
    db.add(meal)
    db.flush()
    db.add(
        MealSource(
            user_id=owner.id,
            meal_id=meal.id,
            origin_meal_id=meal.id,
            kind=kind,
            chat_id=CHAT,
            message_id=meal.id * 10,
            sent_at=NOW,
            text="овсянка на воде" if kind == "text" else None,
            file_id="big" if kind == "photo" else ("v" if kind == "voice" else None),
        )
    )
    db.add(
        MealMessage(
            chat_id=CHAT, message_id=6999 + meal.id, meal_id=meal.id, view="pending"
        )
    )
    db.commit()
    return meal


def _sweep_at(*moves, at) -> Wire:
    """One sweep after each move of the clock, each sweep's reads waited for."""
    box = {}

    async def steps(application, wire):
        box["wire"] = wire
        for move in moves:
            at(**move)
            await tg._sweep(application)
            await _drain()

    asyncio.run(_with_app(steps))
    return box["wire"]


# --- the handler returns before the read ends -----------------------------------------


def test_a_slow_read_does_not_hold_the_next_update(db, owner, reader):
    """Through the queue, where updates run in turn: the read is in flight, and the
    second update still gets its line."""
    seen = {}

    async def steps(application, wire):
        reader.gate = asyncio.Event()
        await application.update_queue.put(_update(application, photo()))
        await application.update_queue.put(_update(application, text("суп")))
        for _ in range(200):
            await asyncio.sleep(0.005)
            if len(wire.sent()) == 2:
                break
        seen["lines"] = [p["text"] for p in wire.sent()]
        seen["image"] = len(reader.image)  # the known positive: the read did begin
        seen["status"] = _one_status(db)
        seen["ended"] = bool(wire.edits())
        reader.gate.set()
        await _drain()

    asyncio.run(_with_app(steps))
    assert seen["image"] == 1
    assert seen["status"] == "pending"
    assert seen["ended"] is False
    assert seen["lines"][0] == capture.M1_PENDING
    assert seen["lines"][1].startswith("Записано: суп")


def _one_status(db) -> str:
    db.expire_all()
    return db.scalars(select(Meal).order_by(Meal.id)).first().reading_status


def test_at_most_three_reads_at_once(db, owner, monkeypatch):
    running = {"now": 0, "peak": 0, "calls": 0}

    async def resolve(payload, *, telegram_id, input_ref, caption=None):
        running["now"] += 1
        running["calls"] += 1
        running["peak"] = max(running["peak"], running["now"])
        await asyncio.sleep(0.01)
        running["now"] -= 1
        return ResolutionResult(
            nutrition=dict(NUTRITION),
            source="vision",
            confidence_tier="low",
            description=None,
            signals={},
            portion_grams=None,
        )

    monkeypatch.setattr(tg, "resolve_meal_nutrition", resolve)
    send(*[photo() for _ in range(5)], drain=False)
    db.expire_all()
    assert running["calls"] == 5
    assert running["peak"] == tg.READS_AT_ONCE == 3  # reached, and not passed


def test_each_task_has_its_own_meter(db, owner, monkeypatch):
    """Two reads at once: each line says its own call, not both."""
    monkeypatch.setattr(tg.settings_screen, "shows_costs", lambda db, tid: True)
    box = {}

    async def steps(application, wire):
        both = asyncio.Event()
        started = []

        async def analyze(*, kind, payload, input_ref, telegram_id):
            usage_meter.record(
                "gpt-4o-mini",
                SimpleNamespace(prompt_tokens=1000, completion_tokens=100),
            )
            started.append(payload)
            if len(started) == 2:
                both.set()
            await both.wait()
            return dict(NUTRITION)

        monkeypatch.setattr(tg, "_analyze_and_parse", analyze)
        await application.process_update(_update(application, text("суп")))
        await application.process_update(_update(application, text("каша")))
        await _drain()
        box["edits"] = wire.edits()

    asyncio.run(_with_app(steps))
    assert len(box["edits"]) == 2
    for edit in box["edits"]:
        assert "1 вызов ИИ" in edit["text"]


# --- a late read ----------------------------------------------------------------------


def test_a_retry_the_person_overtook_is_kept_and_moves_nothing(db, owner, reader, at):
    _sent_one_photo(reader, error=TimedOut("slow"))
    box = {}

    async def steps(application, wire):
        reader.error = None
        reader.gate = asyncio.Event()
        at(minutes=6)
        reader.image.clear()
        await tg._sweep(application)
        await _until(lambda: reader.image)
        meal = _one_meal(db)
        meal.rev = (meal.rev or 0) + 1
        db.commit()
        reader.gate.set()
        await _drain()
        box["edits"] = wire.edits()

    asyncio.run(_with_app(steps))
    last = _readings(db)[-1]
    assert (last.status, last.applied) == ("done", False)
    assert last.output["foods"] == NUTRITION["foods"]
    assert _item_names(db, _one_meal(db)) == []
    assert box["edits"] == []


# --- the sweep ------------------------------------------------------------------------


def test_a_stuck_pending_becomes_failed_and_its_line_says_so(db, owner, reader, at):
    meal = _pending_row(db, owner, minutes_ago=6)
    wire = _sweep_at({}, at=at)
    db.expire_all()
    assert meal.reading_status == "failed"
    (edit,) = wire.edits()
    assert edit["text"] == capture.M7_FAILED
    assert edit["message_id"] == 7000
    assert wire.sent() == []  # an edit, no push
    (row,) = _readings(db)
    assert (row.status, row.error) == (capture.RETRY, capture.NO_LIVE_READ)
    assert reader.image == []  # giving up costs nothing


def test_a_young_pending_is_left_alone(db, owner, reader, at):
    """The other side of the 5 minutes."""
    meal = _pending_row(db, owner, minutes_ago=4)
    wire = _sweep_at({}, at=at)
    db.expire_all()
    assert meal.reading_status == "pending"
    assert wire.edits() == []


def test_a_pending_with_a_live_task_is_left_alone(db, owner, reader, at):
    """Older than 5 minutes and still reading: a slow read is no stuck read."""
    box = {}

    async def steps(application, wire):
        reader.gate = asyncio.Event()
        await application.process_update(_update(application, photo()))
        await _until(lambda: reader.image)
        at(minutes=30)
        meal = _one_meal(db)
        meal.created_at = capture._now() - timedelta(minutes=10)
        db.commit()
        await tg._sweep(application)
        box["status"] = _one_status(db)
        box["image"] = len(reader.image)
        reader.gate.set()
        await _drain()
        box["after"] = wire.edits()[-1]["text"]

    asyncio.run(_with_app(steps))
    assert box["status"] == "pending"
    assert box["image"] == 1
    assert box["after"].startswith("Записано")


def test_a_pending_voice_is_given_up_with_its_own_sentence_and_no_read(
    db, owner, reader, at
):
    meal = _pending_row(db, owner, kind="voice", minutes_ago=6)
    wire = _sweep_at({}, at=at)
    db.expire_all()
    assert meal.reading_status == "failed"
    assert wire.edits()[0]["text"] == capture.M4D_FAILED
    assert reader.image == [] and reader.text == []
    # What it failed on would pass by itself; a voice still has nothing to read it.
    assert _readings(db)[-1].status == capture.RETRY
    wire = _sweep_at({"minutes": 6}, {"hours": 2}, at=at)
    db.expire_all()
    assert meal.read_attempts == 0
    assert wire.edits() == []


def test_a_restart_gives_up_a_pending_voice_with_no_read(db, owner, reader):
    meal = _pending_row(db, owner, kind="voice")
    wire = _start_up()
    db.expire_all()
    assert (meal.reading_status, meal.read_attempts) == ("failed", 0)
    assert wire.edits()[0]["text"] == capture.M4D_FAILED


def test_the_loop_really_sweeps_and_a_failed_pass_does_not_end_it(
    db, owner, reader, monkeypatch
):
    """Through ``post_init``: the loop runs the pass again and again, one raising."""
    monkeypatch.setattr(tg, "SWEEP_EVERY_SECONDS", 0.01)
    passes = []

    async def sweep(application):
        passes.append(1)
        if len(passes) == 1:
            raise RuntimeError("boom")

    monkeypatch.setattr(tg, "_sweep", sweep)

    async def run():
        application = tg.create_bot_application()
        wire = Wire()
        application.bot._request = (wire, wire)
        await application.initialize()
        await application.post_init(application)
        await application.start()
        await asyncio.sleep(0.1)
        await application.stop()
        await application.shutdown()
        await application.post_shutdown(application)
        return application

    application = asyncio.run(run())
    assert len(passes) >= 3
    assert application.bot_data[tg.SWEEP_TASK].done()


def test_stop_is_not_held_by_the_sweep_under_a_real_run_polling(db, owner, reader):
    """The real ``run_polling``: ``post_init`` ran, the replay read the meal, and the
    endless loop did not hold the stop."""
    meal = _pending_row(db, owner, kind="text")

    class Polled(Wire):
        def _result(self, name, params):
            return [] if name == "getUpdates" else super()._result(name, params)

    application = tg.create_bot_application()
    wire = Polled()
    application.bot._request = (wire, wire)

    async def stop_when_read(app):
        for _ in range(400):
            await asyncio.sleep(0.01)
            if wire.edits():
                break
        app.stop_running()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.create_task(stop_when_read(application))
        application.run_polling(stop_signals=None, close_loop=False)
        assert application.bot_data[tg.SWEEP_TASK].done()
    finally:
        asyncio.set_event_loop(None)
        loop.close()
    db.expire_all()
    assert reader.text == ["овсянка на воде"]
    assert meal.reading_status == "read"


# --- the replay at startup ------------------------------------------------------------


def _start_up() -> Wire:
    """The application's own start: what ``post_init`` does, until the reads are done."""
    box = {}

    async def run():
        application = tg.create_bot_application()
        wire = Wire()
        application.bot._request = (wire, wire)
        box["wire"] = wire
        await application.initialize()
        await application.post_init(application)
        await application.start()
        await application.bot_data[tg.REPLAY_DONE].wait()
        await _drain()
        await application.stop()
        await application.shutdown()
        await application.post_shutdown(application)

    asyncio.run(run())
    return box["wire"]


def test_a_restart_replays_a_pending_photo_from_its_file_id(db, owner, reader):
    meal = _pending_row(db, owner)
    wire = _start_up()
    db.expire_all()
    assert meal.reading_status == "read"
    assert reader.image[0]["ref"] == "big"  # the stored id, not a new message
    assert ("getFile", {"file_id": "big"}) in wire.calls
    assert _item_names(db, meal) == NUTRITION["foods"]
    (edit,) = wire.edits()
    assert edit["message_id"] == 7000
    assert edit["text"].startswith("Записано")
    assert wire.sent() == []
    assert meal.read_attempts == 1


def test_a_restart_replays_a_pending_text_from_its_source(db, owner, reader):
    meal = _pending_row(db, owner, kind="text")
    _start_up()
    db.expire_all()
    assert reader.text == ["овсянка на воде"]
    assert meal.reading_status == "read"


def test_a_restart_leaves_a_read_meal_alone(db, owner, reader):
    """Known positive above: a meal that is not pending costs nothing."""
    meal = _pending_row(db, owner)
    meal.reading_status = "read"
    db.commit()
    wire = _start_up()
    assert reader.image == [] and wire.edits() == []


def test_a_crash_loop_cannot_buy_more_than_the_cap(db, owner, reader):
    """Each start counts: a process that dies on this meal stops paying for it."""
    meal = _pending_row(db, owner, attempts=len(capture.RETRY_AFTER))
    wire = _start_up()
    db.expire_all()
    assert reader.image == []
    assert meal.read_attempts == len(capture.RETRY_AFTER)
    assert meal.reading_status == "failed"
    assert wire.edits()[0]["text"] == capture.M7_FAILED


def test_a_restart_counts_each_replay_up_to_the_cap(db, owner, reader):
    """The meal is pending again after each start; the fourth start reads nothing."""
    meal = _pending_row(db, owner)

    reader_calls = []
    for _ in range(len(capture.RETRY_AFTER) + 1):
        db.expire_all()
        meal.reading_status = "pending"
        db.query(MealMessage).filter_by(message_id=7000).update({"view": "pending"})
        db.commit()
        reader.image.clear()
        reader.error = TimedOut("slow")
        _start_up()
        reader_calls.append(len(reader.image))
    assert reader_calls == [1, 1, 1, 0]
    db.expire_all()
    assert meal.read_attempts == 3


def test_a_restart_with_no_balance_reads_nothing_and_no_line_stays_reading(
    db, owner, reader
):
    meal = _pending_row(db, owner)
    db.add(
        MealReading(
            meal_id=meal.id, kind="vision", status=capture.NO_CREDIT, error="Refused"
        )
    )
    db.commit()
    wire = _start_up()
    db.expire_all()
    assert reader.image == []
    assert meal.reading_status == "failed"
    assert meal.read_attempts == 0  # no read happened, so none is counted
    assert wire.edits()[0]["text"] == capture.M7_FAILED


def test_a_burst_of_replays_stops_at_the_first_refusal(db, owner, monkeypatch):
    """Five pending, balance out: three go at once and are refused; the queued two see
    the refusal and never ask. One owner alert per refusal at most."""
    for _ in range(5):
        _pending_row(db, owner)
    calls = []
    gate = {}
    refusal = sdk_error(429, QUOTA_BODY)

    async def resolve(payload, *, telegram_id, input_ref, caption=None):
        calls.append(input_ref)
        gate.setdefault("event", asyncio.Event())
        if len(calls) == 3:
            gate["event"].set()
        await gate["event"].wait()
        raise refusal

    monkeypatch.setattr(tg, "resolve_meal_nutrition", resolve)
    _start_up()
    db.expire_all()
    assert len(calls) == 3
    assert {m.reading_status for m in db.query(Meal)} == {"failed"}
    assert {m.read_attempts for m in db.query(Meal)} == {0, 1}  # asked, or never asked


def test_replay_at_startup_has_a_limit_and_the_rest_are_told(
    db, owner, reader, monkeypatch
):
    monkeypatch.setattr(tg, "REPLAY_LIMIT", 2)
    for _ in range(4):
        _pending_row(db, owner)
    _start_up()
    db.expire_all()
    assert len(reader.image) == 2
    statuses = [m.reading_status for m in db.query(Meal).order_by(Meal.id)]
    assert statuses == ["read", "read", "failed", "failed"]


# --- what a failure is ----------------------------------------------------------------


def _pipeline_error(cause: BaseException) -> RuntimeError:
    """The wrapper the photo pipeline raises around what the model did."""
    try:
        try:
            raise cause
        except BaseException as e:
            raise RuntimeError("Pipeline exhausted") from e
    except RuntimeError as wrapped:
        return wrapped


_REQUEST = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


@pytest.mark.parametrize(
    "make, status",
    [
        (lambda: sdk_error(429, RATE_BODY), capture.RETRY),
        (lambda: sdk_error(429, QUOTA_BODY), capture.NO_CREDIT),
        (lambda: sdk_error(500, {"error": {"message": "x"}}), capture.RETRY),
        (lambda: openai.APITimeoutError(_REQUEST), capture.RETRY),
        (lambda: openai.APIConnectionError(request=_REQUEST), capture.RETRY),
        (lambda: TimedOut("slow"), capture.RETRY),
        (lambda: asyncio.TimeoutError(), capture.RETRY),
        (lambda: ValueError("not json"), capture.FAILED),
        (lambda: json.JSONDecodeError("x", "y", 0), capture.FAILED),
        (lambda: BadRequest("file is too old"), capture.FAILED),
        (lambda: capture.NoVoiceReader(), capture.FAILED),
        (lambda: sdk_error(401, {"error": {"message": "x"}}), capture.FAILED),
        (lambda: RuntimeError("what is this"), capture.FAILED),
    ],
)
def test_only_what_passes_by_itself_is_retried(make, status):
    assert capture.failure_status(make()) == status
    # The photo pipeline wraps it: the same answer through its wrapper.
    assert capture.failure_status(_pipeline_error(make())) == status


def test_the_body_of_an_error_never_reaches_the_row_or_the_log(
    db, owner, reader, caplog, at
):
    secret = "гречка-из-сообщения"
    with caplog.at_level(logging.INFO):
        reader.error = TimedOut(secret)
        send(text(f"каша {secret}"))
        _sweep_at({"minutes": 6}, at=at)
    (first, second) = _readings(db)
    assert first.status == second.status == capture.RETRY
    assert secret not in (first.error or "") and secret not in (second.error or "")
    assert secret not in caplog.text
    assert "TimedOut" in caplog.text  # known positive: the failure was logged


# --- the retries ----------------------------------------------------------------------


def test_a_temporary_failure_is_read_again_and_the_line_edits_with_no_push(
    db, owner, reader, at
):
    _sent_one_photo(reader, error=TimedOut("slow"))
    meal = _one_meal(db)
    assert meal.reading_status == "failed"
    assert meal.read_attempts == 0
    line_id = db.scalar(
        select(MealMessage.message_id).where(MealMessage.view == "line")
    )
    reader.error = None
    reader.image.clear()
    wire = _sweep_at({"minutes": 6}, at=at)
    db.expire_all()
    assert meal.reading_status == "read"
    assert meal.read_attempts == 1
    assert len(reader.image) == 1
    (edit,) = wire.edits()
    assert edit["message_id"] == line_id
    assert edit["text"].startswith("Записано")
    assert wire.sent() == []
    assert _item_names(db, meal) == NUTRITION["foods"]


def test_a_retry_waits_for_its_time(db, owner, reader, at):
    """5 minutes after the first failure, 1 hour after the second, 6 after the third."""
    _sent_one_photo(reader, error=TimedOut("slow"))
    reader.image.clear()
    _sweep_at({"minutes": 4}, at=at)  # 4 min: not yet
    assert len(reader.image) == 0
    _sweep_at({"minutes": 2}, at=at)  # 6 min: the first
    assert len(reader.image) == 1
    _sweep_at({"minutes": 59}, at=at)  # 59 min on: not yet
    assert len(reader.image) == 1
    _sweep_at({"minutes": 2}, at=at)  # 61 min on: the second
    assert len(reader.image) == 2
    _sweep_at({"hours": 5}, at=at)  # 5 h on: not yet
    assert len(reader.image) == 2
    _sweep_at({"hours": 2}, at=at)  # 7 h on: the third
    assert len(reader.image) == 3


def test_the_retry_cap_holds_with_a_counter(db, owner, reader, at):
    _sent_one_photo(reader, error=TimedOut("slow"))
    reader.image.clear()
    meal = _one_meal(db)
    _sweep_at({"minutes": 6}, {"minutes": 61}, {"hours": 7}, at=at)  # each is due
    db.expire_all()
    assert len(reader.image) == 3  # known positive: the cap was reached by real reads
    assert meal.read_attempts == 3
    _sweep_at({"hours": 7}, {"hours": 7}, at=at)  # still inside the 24 hours
    db.expire_all()
    assert len(reader.image) == 3
    assert meal.read_attempts == 3


def test_a_repeat_failure_makes_no_edit(db, owner, reader, at):
    """The line already says it: Telegram would refuse an edit that changes nothing."""
    _sent_one_photo(reader, error=TimedOut("slow"))
    wire = _sweep_at({"minutes": 6}, at=at)
    assert len(reader.image) == 2  # the retry did run
    assert wire.edits() == []


def test_no_retry_after_24_hours(db, owner, reader, at):
    _sent_one_photo(reader, error=TimedOut("slow"))
    reader.image.clear()
    _sweep_at({"hours": 24}, at=at)
    assert reader.image == []


def test_a_retry_inside_24_hours_is_the_control(db, owner, reader, at):
    _sent_one_photo(reader, error=TimedOut("slow"))
    reader.image.clear()
    _sweep_at({"hours": 23}, at=at)
    assert len(reader.image) == 1


def test_at_most_five_per_sweep(db, owner, reader, at):
    reader.error = TimedOut("slow")
    send(*[photo() for _ in range(7)])
    reader.image.clear()
    _sweep_at({"minutes": 6}, at=at)
    assert len(reader.image) == capture.RETRIES_PER_SWEEP == 5
    db.expire_all()
    _sweep_at({"minutes": 6}, at=at)
    assert len(reader.image) == 7  # the last two came with the next sweep


def test_a_lasting_failure_is_not_retried(db, owner, reader, at):
    _sent_one_photo(reader, error=ValueError("not json"))
    reader.image.clear()
    _sweep_at({"hours": 7}, at=at)
    assert reader.image == []


def test_no_retry_while_the_balance_is_out_and_again_when_it_is_back(
    db, owner, reader, at
):
    _sent_one_photo(reader, error=TimedOut("slow"))
    meal = _one_meal(db)
    other = _pending_row(db, owner)
    other.reading_status = "failed"
    db.add(
        MealReading(
            meal_id=other.id,
            kind="vision",
            status=capture.NO_CREDIT,
            error="RateLimitError",
            created_at=capture._now(),
        )
    )
    db.commit()
    reader.image.clear()
    reader.error = None
    rows = len(_readings(db))
    _sweep_at({"minutes": 6}, at=at)
    db.expire_all()
    assert reader.image == []
    assert meal.read_attempts == 0
    assert len(_readings(db)) == rows  # not even begun: no row, and no line moved

    # A failure that says nothing about money does not bring the balance back.
    db.add(
        MealReading(meal_id=other.id, kind="vision", status=capture.RETRY, error="X")
    )
    db.commit()
    _sweep_at({"minutes": 6}, at=at)
    assert reader.image == []

    # A model that answered does.
    db.add(MealReading(meal_id=other.id, kind="text", status="done", output={}))
    db.commit()
    _sweep_at({"minutes": 6}, at=at)
    db.expire_all()
    assert len(reader.image) == 1
    assert meal.read_attempts == 1


def test_an_exact_name_says_nothing_about_the_balance(db, owner, reader):
    """It asked no model."""
    meal = _pending_row(db, owner)
    db.add(MealReading(meal_id=meal.id, kind="vision", status=capture.NO_CREDIT))
    db.flush()
    db.add(MealReading(meal_id=meal.id, kind="exact_name", status="done"))
    db.commit()
    assert capture.balance_out(db) is True


def test_a_real_refusal_stops_the_retries_of_others(db, owner, reader, at):
    """The balance runs out in a live read, through the real classification."""
    _sent_one_photo(reader, error=TimedOut("slow"))
    reader.error = sdk_error(429, QUOTA_BODY)
    send(photo())
    db.expire_all()
    assert _readings(db)[-1].status == capture.NO_CREDIT
    reader.image.clear()
    _sweep_at({"hours": 1}, at=at)
    assert reader.image == []


def test_a_person_who_wrote_the_dishes_is_not_overwritten_by_a_retry(
    db, owner, reader, at
):
    """Items from the person: a retry would replace them."""
    _sent_one_photo(reader, error=TimedOut("slow"))
    meal = _one_meal(db)
    from app.services import meal_items

    change = meal_items.add_item(db, owner.id, meal.id, name="суп")
    assert change.status == meal_items.DONE
    reader.image.clear()
    reader.error = None
    _sweep_at({"minutes": 6}, at=at)
    db.expire_all()
    assert reader.image == []
    assert _item_names(db, meal) == ["суп"]


def test_a_dead_model_on_a_read_nobody_asked_for_sends_no_picker(db, owner, reader, at):
    """The picker is a push; a retry never pushes. The next live meal asks."""
    _sent_one_photo(reader, error=TimedOut("slow"))
    reader.error = ModelUnavailableError("gpt-old", ValueError("gone"), task="vision")
    wire = _sweep_at({"minutes": 6}, at=at)
    assert len(reader.image) == 2  # the retry did run into it
    assert wire.sent() == []
    assert _readings(db)[-1].status == capture.FAILED


def test_a_voice_is_not_retried_with_no_reader(db, owner, reader, at):
    send(_voice())
    meal = _one_meal(db)
    assert meal.reading_status == "failed"
    wire = _sweep_at({"hours": 7}, at=at)
    db.expire_all()
    assert meal.read_attempts == 0
    assert wire.edits() == []


def test_a_retry_edits_the_capture_line_and_not_a_later_one(db, owner, reader, at):
    """A later reply's line belongs to the meal too; it is not what the read answers."""
    _sent_one_photo(reader, error=TimedOut("slow"))
    meal = _one_meal(db)
    capture_line = db.scalar(
        select(MealMessage.message_id).where(MealMessage.view == "line")
    )
    db.add(MealMessage(chat_id=CHAT, message_id=9999, meal_id=meal.id, view="line"))
    db.commit()
    reader.error = None
    wire = _sweep_at({"minutes": 6}, at=at)
    (edit,) = wire.edits()
    assert edit["message_id"] == capture_line


def test_a_screen_opened_on_the_line_is_left_alone(db, owner, reader, at):
    _sent_one_photo(reader, error=TimedOut("slow"))
    db.query(MealMessage).filter_by(view="line").update({"view": "card"})
    db.commit()
    reader.error = None
    wire = _sweep_at({"minutes": 6}, at=at)
    db.expire_all()
    assert _one_meal(db).reading_status == "read"
    assert wire.edits() == []


# --- /reprocess meets the same rows ---------------------------------------------------


def test_reprocess_does_not_buy_a_read_that_is_running(db, owner, reader):
    box = {}

    async def steps(application, wire):
        reader.gate = asyncio.Event()
        await application.process_update(_update(application, photo()))
        await _until(lambda: reader.image)
        try:
            # A second read of the same meal would wait at the gate: bounded here.
            await asyncio.wait_for(
                application.process_update(
                    _update(application, _command("/reprocess"))
                ),
                1,
            )
            box["image"] = len(reader.image)
            box["said"] = wire.sent()[-1]["text"]
        finally:
            reader.gate.set()
        await _drain()

    asyncio.run(_with_app(steps))
    assert box["image"] == 1
    assert box["said"] == "Нет сообщений для повторной обработки. 👍"


def _command(body: str) -> dict:
    return _message(
        text=body, entities=[{"type": "bot_command", "offset": 0, "length": len(body)}]
    )


def test_reprocess_is_the_way_back_after_a_top_up_and_clears_the_balance(
    db, owner, reader, at
):
    """Out of credit is not retried by itself; /reprocess reads it, and retries resume."""
    reader.error = sdk_error(429, QUOTA_BODY)
    send(photo())
    db.expire_all()
    assert capture.balance_out(db) is True
    reader.error = None
    send(_command("/reprocess"))
    db.expire_all()
    assert _one_meal(db).reading_status == "read"
    assert capture.balance_out(db) is False


def test_the_time_on_a_stored_reading_follows_the_clock(db, owner, reader, at):
    """The retry's wait is measured from the failure, read back naive on SQLite."""
    _sent_one_photo(reader, error=TimedOut("slow"))
    (row,) = _readings(db)
    assert row.created_at.replace(tzinfo=UTC) == NOW


def test_now_is_real_outside_a_test():
    assert isinstance(capture._now(), datetime)
    assert capture._now().tzinfo is not None
