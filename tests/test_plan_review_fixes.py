# flake8: noqa: F811
"""R2 review fixes: a merge pick whose dish went away answers instead of crashing, a tap
whose service fails still stops the spinner, a repeat tap on stale buttons shows the
answer again, one bad plan does not keep the 48 h close from the rest, a pick that
would hide dishes behind «Все» is refused, and a plan with questions is not «unreadable»
for one bad line. The planner is a fake; the rest is real.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from telegram.error import NetworkError

from app.crud.crud_message_plan import crud_message_plan
from app.models import MessagePlan, RecordEvent
from app.services import capture, dish_ops, plan_screen, plan_service
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
    _buttons,
    _dish,
    _plan,
    _stars,
    _swept,
    _voice,
    run,
    said,
    tapped,
)
from tests.test_plan_questions import (
    BUCKWHEATS,
    _asks,
    _check,
    _edits,
    _questions,
    _toasts,
)
from tests.test_plan_sweep import _age, _picked, _reply_markups


@pytest.fixture(autouse=True)
def _fresh_sweep_window(monkeypatch):
    monkeypatch.setattr(tg, "_asks_closed_upto", None)


# --- a merge pick whose dish went away --------------------------------------------


def _merge_pick(db, owner, reader):
    soups = [_dish(db, owner, n) for n in ("суп овощной", "суп куриный")]
    borscht = _dish(db, owner, "борщ")
    reader.plan = 'merge "суп" "борщ"'
    wire = run(said(text("объедини суп и борщ")))
    (question,) = _questions(wire)
    assert _asks(db)[0]["ids"] == [borscht.id]  # the clear side, the soups are asked
    return _plan(db), question[0], soups, borscht


def test_a_merge_pick_after_its_dish_was_hidden_answers_not_crashes(
    db, owner, reader, clock
):
    plan, mid, _soups, borscht = _merge_pick(db, owner, reader)
    borscht.hidden_at = datetime.now(UTC)
    db.commit()
    events = len(db.scalars(select(RecordEvent)).all())
    tap = run(tapped(f"pl:c:{plan.id}:0:0", message_id=mid))
    (done,) = _edits(tap)
    assert done["text"] == "✖ Не вышло: «суп» — блюдо уже изменилось."
    assert _toasts(tap) == [None]
    assert len(db.scalars(select(RecordEvent)).all()) == events
    assert _asks(db)[0]["state"] == "done"


def test_the_same_merge_pick_with_every_dish_in_place_merges(db, owner, reader, clock):
    """Control: the guard does not refuse a merge whose dishes are all there."""
    plan, mid, _soups, _borscht = _merge_pick(db, owner, reader)
    tap = run(tapped(f"pl:c:{plan.id}:0:0", message_id=mid))
    (done,) = _edits(tap)
    assert not done["text"].startswith("✖")
    assert _asks(db)[0]["events"]


# --- a tap whose service fails ------------------------------------------------------


@pytest.mark.parametrize(
    "name, data",
    [
        ("answer_question", "pl:c:{p}:0:0"),
        ("undo_question", "pl:q:{p}:0"),
        ("undo", "pl:u:{p}"),
        ("again", "pl:r:{p}"),
    ],
)
def test_a_tap_whose_service_fails_still_answers_the_tap(
    db, owner, reader, clock, monkeypatch, caplog, name, data
):
    plan, mid, _ = _picked(db, owner, reader)

    def broken(*args, **kwargs):
        raise RuntimeError("the database blinked")

    monkeypatch.setattr(plan_service, name, broken)
    with caplog.at_level(logging.ERROR):
        tap = run(tapped(data.format(p=plan.id), message_id=mid))
    assert _toasts(tap) == [plan_screen.TAP_FAILED]
    assert "the database blinked" not in caplog.text  # type and place only
    assert f"Plan {plan.id}" in caplog.text


# --- a repeat tap on stale buttons ------------------------------------------------


def test_a_repeat_tap_on_the_old_buttons_shows_the_answer_with_its_undo(
    db, owner, reader, clock, monkeypatch
):
    plan, mid, _ = _picked(db, owner, reader)
    first = run(tapped(f"pl:c:{plan.id}:0:0", message_id=mid))
    (shown,) = _edits(first)
    # the edit is lost (Telegram away): the person still sees the dish buttons
    again = run(tapped(f"pl:c:{plan.id}:0:1", message_id=mid))
    assert _toasts(again) == [plan_screen.SAME_TOAST]
    (re_shown,) = _edits(again)
    assert re_shown["text"] == shown["text"] == "✓ ⭐ гречка с маслом"
    assert _buttons(re_shown) == _buttons(shown)
    assert _buttons(re_shown)[-1][0][1] == f"pl:q:{plan.id}:0"
    assert _stars(db, owner) == ["гречка с маслом"]  # still one write


def test_a_repeat_tap_after_not_needed_shows_the_declined_line(
    db, owner, reader, clock
):
    plan, mid, _ = _picked(db, owner, reader)
    run(tapped(f"pl:c:{plan.id}:0:n", message_id=mid))
    again = run(tapped(f"pl:c:{plan.id}:0:0", message_id=mid))
    (re_shown,) = _edits(again)
    assert re_shown["text"] == plan_screen.DECLINED_LINE and _buttons(re_shown) == []
    assert _stars(db, owner) == []


def test_the_answers_undo_after_the_plans_undo_loses_its_buttons(
    db, owner, reader, clock
):
    plan, mid, _ = _picked(db, owner, reader)
    run(tapped(f"pl:c:{plan.id}:0:0", message_id=mid))
    run(tapped(f"pl:u:{plan.id}"))
    late = run(tapped(f"pl:q:{plan.id}:0", message_id=mid))
    assert _toasts(late) == [plan_screen.SAME_TOAST]
    (dropped,) = _reply_markups(late)
    assert _buttons(dropped) == []
    assert _edits(late) == []


def test_a_foreign_repeat_tap_touches_no_message(db, owner, reader, clock):
    plan, mid, _ = _picked(db, owner, reader)
    run(tapped(f"pl:c:{plan.id}:0:0", message_id=mid))
    assert plan_service.settled(db, owner.telegram_id + 1, plan.id, 0) is None
    assert plan_service.settled(db, owner.telegram_id, plan.id, 7) is None


# --- the 48 h close and one bad plan ------------------------------------------------


def test_one_plan_that_fails_to_close_does_not_keep_the_others_open(
    db, owner, reader, clock, monkeypatch, caplog
):
    _picked(db, owner, reader)
    run(said(text("добавь гречку в избранное")))  # a second plan, same pick
    first, second = db.scalars(select(MessagePlan).order_by(MessagePlan.id)).all()
    _age(db, first, hours=50)
    _age(db, second, hours=49)
    real = crud_message_plan.close_asks

    def broken_for_first(plan):
        if plan.id == first.id:
            raise RuntimeError("bad row")
        return real(plan)

    monkeypatch.setattr(crud_message_plan, "close_asks", broken_for_first)
    with caplog.at_level(logging.ERROR):
        _swept()
    db.expire_all()
    states = {
        p.id: [a["state"] for a in crud_message_plan.asks_of(p)]
        for p in (db.get(MessagePlan, first.id), db.get(MessagePlan, second.id))
    }
    assert states == {first.id: ["open"], second.id: ["closed"]}
    assert f"Plan {first.id} questions not closed: RuntimeError" in caplog.text
    assert tg._asks_closed_upto is not None  # the window moved


# --- the pick's size and «Все» ------------------------------------------------------


def test_a_name_fitting_more_dishes_than_a_pick_shows_is_refused():
    dishes = [[f"суп номер {n}"] for n in range(6)]
    checked = _check('hide "суп"', "убери суп из блюд", *dishes)
    assert checked.asks == []
    (refusal,) = checked.refused
    assert "подходит несколько" in refusal.why
    assert refusal.why.count("«суп номер") == plan_service.PICK_MAX


# --- a plan with questions and one bad line -----------------------------------------


def test_a_plan_with_a_question_and_a_bad_line_is_not_unreadable():
    checked = _check(
        'star "гречка"\nstar #',
        "добавь гречку в избранное",
        [BUCKWHEATS[0]],
        [BUCKWHEATS[1]],
    )
    assert checked.bad == 1 and checked.asks and not checked.acts
    assert not plan_service.unreadable(plan_service.DISH, checked)


def test_a_plan_with_only_a_bad_line_is_still_unreadable():
    """Control: the change keeps the failure for a message nothing came of."""
    checked = _check("star #", "добавь гречку в избранное", [BUCKWHEATS[0]])
    assert checked.bad == 1 and not checked.asks and not checked.acts
    assert plan_service.unreadable(plan_service.DISH, checked)


# --- the failed line's mark and the question's lock -----------------------------------


def test_a_failed_voice_line_whose_edit_failed_is_not_marked_as_shown(
    db, seen, reader, whisper, clock, monkeypatch
):
    _dish(db, seen, "гречка")
    reader.plan = None
    whisper.answers("добавь гречку в избранное")
    real = tg._edit_line
    tries = []

    async def away_for_the_plan(bot, chat_id, message_id, line):
        if line.text.startswith(capture.PLAN_FAILED):
            tries.append(message_id)
            raise NetworkError("telegram is away")
        return await real(bot, chat_id, message_id, line)

    with monkeypatch.context() as m:
        m.setattr(tg, "_edit_line", away_for_the_plan)
        run(said(_voice()))
    plan = _plan(db)
    assert tries and plan.status == crud_message_plan.FAILED
    assert plan.commands["fail_shown"] is False  # TD-274's sweep still owes it


def test_a_question_message_id_is_stored_under_the_persons_lock(
    db, owner, reader, clock, monkeypatch
):
    order = []
    lock, sent = dish_ops.lock_user, crud_message_plan.ask_sent

    def locking(db_, user):
        order.append(("lock", user.id))
        return lock(db_, user)

    def storing(db_, plan_id, q, message_id):
        order.append(("store", q))
        return sent(db_, plan_id, q, message_id)

    plan, mid, _ = _picked(db, owner, reader)
    monkeypatch.setattr(dish_ops, "lock_user", locking)
    monkeypatch.setattr(crud_message_plan, "ask_sent", storing)
    plan_service.question_sent(db, plan.id, 0, mid)
    assert order == [("lock", owner.id), ("store", 0)]
    assert _asks(db)[0]["message_id"] == mid
