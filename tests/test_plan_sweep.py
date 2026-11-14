# flake8: noqa: F811
"""The plan sweep and the questions' loose ends (design doc 6.3, «Closed»): a question
closes 48 h after its plan, a plan's ↩️ closes the questions still open, a failed line
that never went out is retried (TD-274), the owed sweep never overwrites a meal's result
(TD-275), and a voice plan sends its questions. The planner is a fake; the rest is real.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from telegram.error import BadRequest

from app.crud.crud_message_plan import crud_message_plan
from app.models import MealMessage, RecordEvent
from app.services import capture, plan_screen, plan_service
from app.services import telegram as tg
from tests.test_capture_router import (  # noqa: F401 (fixtures)
    Session,
    Wire,
    clock,
    db,
    owner,
    reader,
    text,
)
from tests.test_capture_voice import seen, whisper  # noqa: F401 (fixtures)
from tests.test_message_plans import (  # noqa: F401 (fixtures, helpers)
    DAY,
    _a_clean_run,
    _buttons,
    _dish,
    _meals,
    _plan,
    _stars,
    _stuck,
    _swept,
    _voice,
    run,
    said,
    tapped,
)
from tests.test_plan_questions import BUCKWHEATS, _asks, _edits, _questions, _toasts

ROOT = Path(__file__).resolve().parents[1]
CLOSED = "Вопрос закрыт: прошло 2 дня. Ничего не изменено."


@pytest.fixture(autouse=True)
def _fresh_sweep_window(monkeypatch):
    monkeypatch.setattr(tg, "_asks_closed_upto", None)


def _age(db, plan, **ago) -> None:
    plan.created_at = datetime.now(UTC) - timedelta(**ago)
    db.commit()


def _picked(db, owner, reader):
    """A plan holding one open pick; (plan, the question's message id, its wire)."""
    for name in BUCKWHEATS:
        _dish(db, owner, name)
    reader.plan = 'star "гречка"'
    wire = run(said(text("добавь гречку в избранное")))
    return _plan(db), _questions(wire)[0][0], wire


def _reply_markups(wire: Wire) -> list:
    return [p for n, p in wire.calls if n == "editMessageReplyMarkup"]


# --- 1. 48 h close --------------------------------------------------------------------


def test_a_question_open_after_48_hours_is_closed_with_the_exact_words(
    db, owner, reader, clock
):
    plan, mid, _ = _picked(db, owner, reader)
    _age(db, plan, hours=49)
    wire = _swept()
    (edit,) = [e for e in _edits(wire) if e["message_id"] == mid]
    assert edit["text"] == CLOSED == plan_screen.CLOSED_LINE
    assert _buttons(edit) == []
    assert _asks(db)[0]["state"] == "closed"
    assert _stars(db, owner) == [] and _meals(db) == []  # nothing written


def test_a_question_younger_than_48_hours_stays_open(db, owner, reader, clock):
    plan, mid, _ = _picked(db, owner, reader)
    _age(db, plan, hours=47)
    wire = _swept()
    assert [e for e in _edits(wire) if e["message_id"] == mid] == []
    assert _asks(db)[0]["state"] == "open"


def test_a_tap_on_a_closed_question_is_gone_writes_nothing_and_drops_buttons(
    db, owner, reader, clock
):
    plan, mid, _ = _picked(db, owner, reader)
    _age(db, plan, hours=49)
    _swept()
    events = len(db.scalars(select(RecordEvent)).all())
    tap = run(tapped(f"pl:c:{plan.id}:0:0", message_id=mid))
    assert _toasts(tap) == [plan_screen.GONE_TOAST]
    assert _stars(db, owner) == [] and _edits(tap) == []
    assert len(db.scalars(select(RecordEvent)).all()) == events
    (dropped,) = _reply_markups(tap)
    assert dropped["message_id"] == mid and not dropped.get("reply_markup")
    again = run(tapped(f"pl:q:{plan.id}:0", message_id=mid))
    assert _toasts(again) == [plan_screen.GONE_TOAST]


def test_answered_and_undone_questions_keep_the_same_toast(db, owner, reader, clock):
    plan, mid, _ = _picked(db, owner, reader)
    run(tapped(f"pl:c:{plan.id}:0:n", message_id=mid))  # declined
    again = run(tapped(f"pl:c:{plan.id}:0:0", message_id=mid))
    assert _toasts(again) == [plan_screen.SAME_TOAST]
    assert _reply_markups(again) == []  # the closed rule is not the declined one
    _age(db, plan, hours=49)
    _swept()
    assert _asks(db)[0]["state"] == "declined"  # only an open one closes


def test_a_question_with_no_message_is_closed_without_an_edit(db, owner, reader, clock):
    plan, mid, _ = _picked(db, owner, reader)
    asks = crud_message_plan.asks_of(plan)
    asks[0]["message_id"] = None
    crud_message_plan.set_asks(plan, asks)
    db.commit()
    _age(db, plan, hours=49)
    wire = _swept()
    assert _asks(db)[0]["state"] == "closed" and _edits(wire) == []


def test_a_failed_edit_is_logged_and_the_question_stays_closed_for_good(
    db, owner, reader, clock, monkeypatch, caplog
):
    plan, mid, _ = _picked(db, owner, reader)
    _age(db, plan, hours=49)
    tries = []

    async def broken(bot, chat_id, message_id, line):
        tries.append(message_id)
        raise BadRequest("message to edit not found")

    monkeypatch.setattr(tg, "_edit_line", broken)
    with caplog.at_level(logging.WARNING):
        _swept()
        _swept()
    assert tries == [mid]  # once: no retry loop
    assert "Plan question not edited" in caplog.text
    assert _asks(db)[0]["state"] == "closed"


def test_a_plan_looked_at_once_is_not_read_again(db, owner, reader, clock, monkeypatch):
    plan, _mid, _ = _picked(db, owner, reader)
    _age(db, plan, hours=49)
    now = datetime.now(UTC)
    after = now - plan_service.ASK_LIFE - plan_service.ASK_LOOKBACK
    closed, upto = plan_service.close_old(db, now, after)
    assert len(closed) == 1 and upto == now - plan_service.ASK_LIFE
    read = crud_message_plan.done_between
    got = []

    def spy(db_, after_, upto_):
        got.append(read(db_, after_, upto_))
        return got[-1]

    monkeypatch.setattr(crud_message_plan, "done_between", spy)
    again, _ = plan_service.close_old(db, now, upto)
    assert again == [] and got == [[]]  # the closed plan was not even read


def test_the_sweep_moves_its_window_forward(db, owner, reader, clock):
    plan, _mid, _ = _picked(db, owner, reader)
    _age(db, plan, hours=49)
    assert tg._asks_closed_upto is None
    _swept()
    assert tg._asks_closed_upto is not None
    assert datetime.now(UTC) - tg._asks_closed_upto >= plan_service.ASK_LIFE


def test_the_close_query_has_no_json_operators():
    source = (ROOT / "app/crud/crud_message_plan.py").read_text()
    assert "astext" not in source and "->>" not in source
    assert "json_extract" not in source and ".contains(" not in source
    assert "telegram" not in source and "capture" not in source
    assert "dish_ops" not in source


# --- 2. open asks after the plan's ↩️ -------------------------------------------------


def test_asks_open_when_the_plan_is_undone_close_and_the_late_tap_drops_buttons(
    db, owner, reader, clock
):
    for name in (*BUCKWHEATS, "борщ"):
        _dish(db, owner, name)
    reader.plan = 'star "борщ"\nstar "гречка"'
    wire = run(said(text("добавь борщ и гречку в избранное")))
    plan, (question,) = _plan(db), _questions(wire)
    assert _stars(db, owner) == ["борщ"]
    run(tapped(f"pl:u:{plan.id}"))
    assert _stars(db, owner) == []
    assert [a["state"] for a in _asks(db)] == ["closed"]
    events = len(db.scalars(select(RecordEvent)).all())
    late = run(tapped(f"pl:c:{plan.id}:0:0", message_id=question[0]))
    assert _toasts(late) == [plan_screen.GONE_TOAST]
    assert _stars(db, owner) == [] and _edits(late) == []
    assert len(db.scalars(select(RecordEvent)).all()) == events
    (dropped,) = _reply_markups(late)
    assert dropped["message_id"] == question[0]


# --- 3. TD-274: a failed line that never went out -------------------------------------


def _failed_unshown(db, owner, reader, monkeypatch):
    """A failed plan whose edit and send both failed."""
    _dish(db, owner, "гречка")
    reader.plan = None

    async def nothing_went(*args, **kwargs):
        return None

    with monkeypatch.context() as m:
        m.setattr(tg, "_put", nothing_went)
        run(said(text("добавь гречку в избранное")))
    plan = _plan(db)
    assert plan.status == crud_message_plan.FAILED
    assert plan.commands["fail_shown"] is False
    _age(db, plan, minutes=10)
    return plan


def test_a_failed_line_that_never_went_out_is_put_again_once(
    db, owner, reader, clock, monkeypatch
):
    plan = _failed_unshown(db, owner, reader, monkeypatch)
    wire = _swept()
    (shown,) = wire.edits()  # on the «читаю…» line
    assert shown["text"].startswith(capture.PLAN_FAILED)
    assert _buttons(shown)[0][0][1] == f"pl:r:{plan.id}"
    assert _plan(db).commands["fail_shown"] is True
    assert _swept().seen() == []  # stops once the line went out


def test_a_failed_line_that_fails_again_is_tried_on_the_next_sweep(
    db, owner, reader, clock, monkeypatch
):
    _failed_unshown(db, owner, reader, monkeypatch)
    tries = []

    async def still_down(*args, **kwargs):
        tries.append(1)
        return None

    with monkeypatch.context() as m:
        m.setattr(tg, "_put", still_down)
        _swept()
        _swept()
    assert len(tries) == 2 and _plan(db).commands["fail_shown"] is False
    assert len(_swept().edits()) == 1


def test_a_failed_line_is_not_retried_after_a_day(
    db, owner, reader, clock, monkeypatch
):
    plan = _failed_unshown(db, owner, reader, monkeypatch)
    _age(db, plan, hours=25)
    assert _swept().seen() == []
    assert _plan(db).commands["fail_shown"] is False


def test_a_failed_line_that_was_shown_is_marked_and_left_alone(
    db, owner, reader, clock
):
    _dish(db, owner, "гречка")
    reader.plan = None
    wire = run(said(text("добавь гречку в избранное")))
    assert wire.line(0)[-1].startswith(capture.PLAN_FAILED)
    plan = _plan(db)
    assert plan.commands["fail_shown"] is True
    _age(db, plan, minutes=10)
    assert _swept().seen() == []


def test_a_plan_failed_by_the_sweep_is_marked_when_its_line_goes_out(
    db, owner, reader, clock
):
    plan_id = _stuck(db, owner, "text")
    _swept()
    assert _plan(db).id == plan_id and _plan(db).commands["fail_shown"] is True


def test_a_plan_failed_again_after_the_retry_tap_is_unmarked(db, owner, reader, clock):
    _dish(db, owner, "гречка")
    reader.plan = None
    run(said(text("добавь гречку в избранное")))
    plan = _plan(db)
    assert plan.commands["fail_shown"] is True
    assert plan_service.again(db, owner.telegram_id, plan.id, plan.line_message_id)
    assert plan_service.fail(db, plan.id) is not None
    db.expire_all()
    assert _plan(db).commands["fail_shown"] is False


# --- 4. TD-275: the owed sweep and a line that holds a result -------------------------


def _owed_after_a_blink(db, owner, reader, monkeypatch):
    def blinked(*args, **kwargs):
        raise RuntimeError("the database blinked")

    reader.plan = 'meal "на завтрак овсянка"\nmeal "в обед суп"'
    with monkeypatch.context() as m:
        m.setattr(plan_service, "shown", blinked)
        run(said(text(DAY)))
    plan = _plan(db)
    assert plan.status == crud_message_plan.DONE and plan.line_ids is None
    _age(db, plan, minutes=10)
    return plan


def test_the_owed_sweep_never_covers_a_meals_result(
    db, owner, reader, clock, monkeypatch
):
    plan = _owed_after_a_blink(db, owner, reader, monkeypatch)
    row = db.scalars(
        select(MealMessage).where(MealMessage.message_id == plan.line_message_id)
    ).one()  # control: the line is mapped to a meal of the plan
    assert row.meal_id in plan.meal_ids
    wire = _swept()
    assert capture.PLAN_ANSWER_LOST not in wire.seen()
    assert wire.edits() == [] and wire.sent() == []
    assert _plan(db).line_ids == []  # only marked shown


def test_the_owed_sweep_still_stops_a_line_that_holds_no_result(
    db, owner, reader, clock, monkeypatch
):
    plan = _owed_after_a_blink(db, owner, reader, monkeypatch)
    for row in db.scalars(select(MealMessage)).all():
        db.delete(row)  # the line was never bound to a meal
    db.commit()
    wire = _swept()
    (shown,) = wire.edits()
    assert shown["text"] == capture.PLAN_ANSWER_LOST
    assert shown["message_id"] == plan.line_message_id
    assert _plan(db).line_ids == []


# --- 6. the voice route ---------------------------------------------------------------


def test_a_voice_plan_with_a_pick_sends_its_question_after_the_answer(
    db, seen, reader, whisper, clock, monkeypatch
):
    for name in BUCKWHEATS:
        _dish(db, seen, name)
    shown = []
    real = tg._plan_show

    async def spy(application, ask, executed, first_line):
        shown.append([q["q"] for q in executed.asks])
        return await real(application, ask, executed, first_line)

    monkeypatch.setattr(tg, "_plan_show", spy)
    reader.plan = 'star "гречка"'
    whisper.answers("добавь гречку в избранное")
    wire = run(said(_voice()))
    assert shown == [[0]]  # the voice route reached _plan_show with the question
    (question,) = _questions(wire)
    assert question[1] == "⭐ Какую гречку добавить в избранное?"
    texts = [
        p.get("text") for n, p in wire.calls if n in ("sendMessage", "editMessageText")
    ]
    assert texts.index(plan_screen.ASKS_FOLLOW) < texts.index(question[1])
    assert _asks(db)[0]["message_id"] == question[0]
    assert _stars(db, seen) == []  # nothing written until the tap
    tap = run(tapped(f"pl:c:{_plan(db).id}:0:0", message_id=question[0]))
    assert _stars(db, seen) == ["гречка с маслом"] and len(_edits(tap)) == 1
