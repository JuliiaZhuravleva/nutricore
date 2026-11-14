# flake8: noqa: F811
"""D3 «questions back: buttons only»: a name that fits several dishes becomes a pick, the
model's ``ask`` a yes/no question; a tap writes under the plan's group, each answer has
its own ✅ ОК / ↩️, and a second or a foreign tap writes nothing. The planner is a fake
(``reader.plan``); everything after it is real.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session as OrmSession

from app.crud.crud_message_plan import crud_message_plan
from app.models import MessagePlan, PersonalFood, RecordEvent, User
from app.services import plan_grammar as grammar
from app.services import plan_screen, plan_service
from tests.test_capture_router import (  # noqa: F401 (fixtures)
    OWNER,
    Session,
    Wire,
    clock,
    db,
    owner,
    reader,
    text,
)
from tests.test_message_plans import (  # noqa: F401 (fixtures, helpers)
    _a_clean_run,
    _buttons,
    _dish,
    _meals,
    _plan,
    _stars,
    run,
    said,
    tapped,
)
from tests.test_plan_units import _book

ROOT = Path(__file__).resolve().parents[1]
BUCKWHEATS = ("гречка с маслом", "гречка отварная")


def _fresh(db):
    """A second session on the same database: what a restarted bot would read."""
    return OrmSession(bind=db.get_bind())


def _asks(db) -> list:
    return _plan(db).commands["asks"]


def _questions(wire: Wire) -> list:
    """The question messages sent, in order: (message id, text, buttons)."""
    out = []
    for params, mid in zip(wire.sent(), wire.sent_ids):
        rows = _buttons(params)
        if any(data and data.startswith("pl:c:") for row in rows for _t, data in row):
            out.append((mid, params["text"], rows))
    return out


def _edits(wire: Wire) -> list:
    return [p for n, p in wire.calls if n == "editMessageText"]


def _toasts(wire: Wire) -> list:
    return [p.get("text") for n, p in wire.calls if n == "answerCallbackQuery"]


def _check(answer: str, text: str, *dishes):
    return plan_service.check(grammar.parse(answer), text, _book(*dishes))


# --- the pick -----------------------------------------------------------------------


def test_a_name_fitting_several_dishes_is_a_pick_not_a_refusal():
    checked = _check(
        'star "гречка"', "добавь гречку в избранное", [BUCKWHEATS[0]], [BUCKWHEATS[1]]
    )
    assert checked.refused == [] and checked.acts == []
    (pick,) = checked.asks
    assert pick.kind == "pick" and pick.verb == grammar.STAR
    assert pick.cands == (101, 102) and pick.label == "гречка"
    ask = {"kind": "pick", "verb": "star", "label": "гречка", "q": 0}
    assert plan_screen.question_text(ask, []) == "⭐ Какую гречку добавить в избранное?"


def test_a_pick_offers_at_most_four_dishes():
    dishes = [[f"суп номер {n}"] for n in range(6)]
    (pick,) = _check('hide "суп"', "убери суп из блюд", *dishes).asks
    assert len(pick.cands) == plan_service.PICK_MAX == 4


@pytest.mark.parametrize(
    "verb, phrase",
    [
        ("star", "⭐ Какую гречку добавить в избранное?"),
        ("unstar", "⭐ Какую гречку убрать из избранного?"),
        ("hide", "🍲 Какую гречку убрать из блюд?"),
        ("merge", "🍲 Какую гречку объединить?"),
        ("rename", "🍲 Какую гречку переименовать?"),
    ],
)
def test_the_question_names_the_verb_in_words(verb, phrase):
    ask = {"kind": "pick", "verb": verb, "label": "гречку", "q": 0}
    assert plan_screen.question_text(ask, []) == phrase


def test_all_is_offered_for_star_unstar_hide_merge_never_rename():
    for verb in ("star", "unstar", "hide", "merge", "rename"):
        ask = {"kind": "pick", "verb": verb, "label": "x", "q": 0}
        markup = plan_screen.question_markup(7, ask, ["один", "два"])
        rows = [[b.text for b in row] for row in markup.inline_keyboard]
        assert rows[:2] == [["один"], ["два"]]  # one per row, the full name
        assert rows[-1] == ["✖ Не надо"]
        assert (["Все"] in rows) is (verb != "rename"), verb


def test_a_pick_does_not_ask_the_model_again():
    checked = _check(
        'star "гречка"', "добавь гречку в избранное", [BUCKWHEATS[0]], [BUCKWHEATS[1]]
    )
    parsed = grammar.parse('star "гречка"')
    assert plan_service._ambiguous(checked)
    assert plan_service._retry_reasons(checked, parsed, "добавь гречку", "dish") == ""


def test_two_unclear_names_in_one_merge_stay_a_refusal():
    checked = _check(
        'merge "гречка" "суп"',
        "гречка и суп — одно блюдо",
        [BUCKWHEATS[0]],
        [BUCKWHEATS[1]],
        ["суп овощной"],
        ["суп куриный"],
    )
    assert checked.asks == [] and len(checked.refused) == 1
    assert checked.refused[0].why.startswith(" — подходит несколько")


def test_a_merge_pick_asks_for_the_unclear_side_only():
    checked = _check(
        'merge "гречка" #3 "суп куриный"',
        "гречка и суп куриный — одно блюдо",
        [BUCKWHEATS[0]],
        [BUCKWHEATS[1]],
        ["суп куриный"],
    )
    (pick,) = checked.asks
    assert pick.verb == grammar.MERGE and pick.ids == (103,)
    assert pick.cands == (101, 102)


# --- the ask ------------------------------------------------------------------------


def test_ask_is_a_verb_with_doc_and_example_and_the_prompt_teaches_it():
    verb = next(v for v in grammar.VERBS if v.name == grammar.ASK)
    assert verb.syntax in plan_service.SYSTEM and verb.example in plan_service.SYSTEM
    (command,) = grammar.parse(verb.example).commands
    assert command.verb == grammar.ASK and command.inner.verb == grammar.MERGE
    assert grammar.parse('ask meal "x"').bad and grammar.parse("ask").bad


HEDGED = "наверное омлет стоит убрать"


def test_a_clause_the_question_took_gets_no_not_understood_line():
    # Calibration 2026-11-15: the question came with «Не записано» on its own clause.
    checked = _check('ask hide #1 "омлет"', HEDGED, ["омлет"])
    assert [q.kind for q in checked.asks] == ["ask"]
    assert checked.missed == []


def test_without_the_rule_the_clause_was_missed(monkeypatch):
    monkeypatch.setattr(plan_service, "_asked_words", lambda *_a: frozenset())
    checked = _check('ask hide #1 "омлет"', HEDGED, ["омлет"])
    assert checked.missed == [HEDGED]


def test_another_clause_beside_the_question_is_still_missed():
    checked = _check('ask hide #1 "омлет"', HEDGED + ", купить билеты", ["омлет"])
    assert checked.missed == ["купить билеты"]


def test_ask_merge_reads_exactly_as_approved():
    checked = _check(
        'ask merge #1 "котлета куриная" #2 "куриная котлета"',
        "котлета куриная и куриная котлета — одно блюдо?",
        ["котлета куриная"],
        ["куриная котлета"],
    )
    (ask,) = checked.asks
    assert ask.kind == "ask" and checked.acts == []
    stored = plan_service._asks_stored([ask])[0]
    words = plan_screen.question_text(stored, ["котлета куриная", "куриная котлета"])
    assert words == (
        "Объединить «котлета куриная» и «куриная котлета» в одно блюдо?\n"
        "Записи останутся, отменить можно."
    )
    rows = plan_screen.question_markup(3, stored, []).inline_keyboard
    assert [[b.text for b in row] for row in rows] == [["Да, объединить", "✖ Не надо"]]
    assert rows[0][0].callback_data == "pl:c:3:0:y"


def test_the_short_yes_of_the_other_dish_verbs():
    assert plan_screen.YES_LABEL == {
        "merge": "Да, объединить",
        "star": "Да, добавить",
        "unstar": "Да, убрать",
        "hide": "Да, убрать",
        "rename": "Да, переименовать",
    }


def test_a_refused_inner_command_is_a_refusal_line_not_a_question():
    checked = _check('ask star "пицца"', "добавь пиццу в избранное?", ["борщ"])
    assert checked.asks == []
    assert [r.why for r in checked.refused] == [plan_service.WHY_NONE]


@pytest.mark.parametrize(
    "answer",
    ['rename #1 "салат"', 'ask rename #1 "салат"', 'rename "гречка"'],
)
def test_a_rename_without_a_new_name_is_refused_with_a_hint_never_asked(answer):
    checked = _check(
        answer, "переименуй салат", ["салат"], [BUCKWHEATS[0]], [BUCKWHEATS[1]]
    )
    assert checked.asks == [] and checked.acts == []
    (refusal,) = checked.refused
    assert refusal.why == " — напиши новое имя: «переименуй X в Y»."
    assert refusal.final  # asking the model again cannot mend it


def test_an_ambiguous_rename_with_a_new_name_is_a_pick_without_all():
    checked = _check(
        'rename "гречка" to "каша"',
        "переименуй гречка в каша",
        [BUCKWHEATS[0]],
        [BUCKWHEATS[1]],
    )
    (pick,) = checked.asks
    assert pick.verb == grammar.RENAME and pick.new_name == "каша"


# --- through the bot ----------------------------------------------------------------


def _two_buckwheats(db, owner):
    return [_dish(db, owner, name) for name in BUCKWHEATS]


def test_a_pick_is_sent_after_the_answer_as_a_silent_reply(db, owner, reader, clock):
    _two_buckwheats(db, owner)
    reader.plan = 'star "гречка"'
    wire = run(said(text("добавь гречку в избранное")))
    assert len(reader.planned) == 1  # no second model call
    (question,) = _questions(wire)
    mid, words, rows = question
    assert words == "⭐ Какую гречку добавить в избранное?"
    plan = _plan(db)
    assert rows == [
        [(BUCKWHEATS[0], f"pl:c:{plan.id}:0:0")],
        [(BUCKWHEATS[1], f"pl:c:{plan.id}:0:1")],
        [("Все", f"pl:c:{plan.id}:0:a")],
        [("✖ Не надо", f"pl:c:{plan.id}:0:n")],
    ]
    sent = [p for p in wire.sent() if p["text"] == words]
    assert sent[0].get("disable_notification") is True
    assert _asks(db)[0]["message_id"] == mid
    assert _stars(db, owner) == [] and _meals(db) == []
    # the plan's own line went out before the question: it says questions follow
    assert plan_screen.ASKS_FOLLOW in [p.get("text") for n, p in wire.calls]
    assert wire.calls.index(
        next(c for c in wire.calls if c[1].get("text") == plan_screen.ASKS_FOLLOW)
    ) < wire.calls.index(next(c for c in wire.calls if c[1].get("text") == words))


def test_ask_merge_through_the_bot_then_yes_writes_under_the_plan_group(
    db, owner, reader, clock
):
    _dish(db, owner, "котлета куриная")
    _dish(db, owner, "куриная котлета")
    reader.plan = 'ask merge "котлета куриная" "куриная котлета"'
    wire = run(said(text("котлета куриная и куриная котлета — одно блюдо")))
    ((mid, words, rows),) = _questions(wire)
    assert words == (
        "Объединить «котлета куриная» и «куриная котлета» в одно блюдо?\n"
        "Записи останутся, отменить можно."
    )
    plan = _plan(db)
    assert rows == [
        [
            ("Да, объединить", f"pl:c:{plan.id}:0:y"),
            ("✖ Не надо", f"pl:c:{plan.id}:0:n"),
        ]
    ]
    tap = run(tapped(f"pl:c:{plan.id}:0:y", message_id=mid))
    (done,) = _edits(tap)
    assert done["text"].startswith("✓ Одно блюдо: «")
    rows = _buttons(done)
    assert rows[0][0][0] == "✅ ОК"  # ✅ ОК first, then ↩️
    assert rows[1] == [("↩️ Отменить", f"pl:q:{plan.id}:0")]
    db.expire_all()
    events = db.scalars(
        select(RecordEvent).where(RecordEvent.kind == "food_merge")
    ).all()
    assert [e.group_id for e in events] == [plan.group_id]
    (ask,) = _asks(db)
    assert ask["state"] == "done" and ask["events"] == [e.id for e in events]


def test_the_done_line_of_a_pick_is_the_plans_own_star_line(db, owner, reader, clock):
    _two_buckwheats(db, owner)
    reader.plan = 'star "гречка"'
    wire = run(said(text("добавь гречку в избранное")))
    plan, mid = _plan(db), _questions(wire)[0][0]
    tap = run(tapped(f"pl:c:{plan.id}:0:0", message_id=mid))
    (done,) = _edits(tap)
    assert done["text"] == "✓ ⭐ гречка с маслом"
    assert _buttons(done)[0][0][0] == "✅ ОК"
    assert _stars(db, owner) == ["гречка с маслом"]
    db.expire_all()
    star = db.scalars(select(RecordEvent).where(RecordEvent.kind == "food_star")).one()
    assert star.group_id == plan.group_id


def test_all_stars_every_candidate_and_one_tap_undoes_them_together(
    db, owner, reader, clock
):
    _two_buckwheats(db, owner)
    reader.plan = 'star "гречка"'
    wire = run(said(text("добавь гречку в избранное")))
    plan, mid = _plan(db), _questions(wire)[0][0]
    run(tapped(f"pl:c:{plan.id}:0:a", message_id=mid))
    assert sorted(_stars(db, owner)) == sorted(BUCKWHEATS)
    undone = run(tapped(f"pl:q:{plan.id}:0", message_id=mid))
    assert _stars(db, owner) == []
    assert _buttons(_edits(undone)[0]) == []


def test_not_needed_closes_the_question_without_buttons_or_writes(
    db, owner, reader, clock
):
    _two_buckwheats(db, owner)
    reader.plan = 'star "гречка"'
    wire = run(said(text("добавь гречку в избранное")))
    plan, mid = _plan(db), _questions(wire)[0][0]
    tap = run(tapped(f"pl:c:{plan.id}:0:n", message_id=mid))
    (done,) = _edits(tap)
    assert done["text"] == plan_screen.DECLINED_LINE and _buttons(done) == []
    assert _stars(db, owner) == [] and _asks(db)[0]["state"] == "declined"
    again = run(tapped(f"pl:c:{plan.id}:0:0", message_id=mid))
    assert _stars(db, owner) == [] and _edits(again) == []


def test_a_double_tap_does_nothing_the_second_time(db, owner, reader, clock):
    _two_buckwheats(db, owner)
    reader.plan = 'star "гречка"'
    wire = run(said(text("добавь гречку в избранное")))
    plan, mid = _plan(db), _questions(wire)[0][0]
    run(tapped(f"pl:c:{plan.id}:0:0", message_id=mid))
    events = len(db.scalars(select(RecordEvent)).all())
    again = run(
        tapped(f"pl:c:{plan.id}:0:1", message_id=mid),  # the other dish, too late
        tapped(f"pl:c:{plan.id}:0:0", message_id=mid),
    )
    assert _stars(db, owner) == ["гречка с маслом"]
    assert len(db.scalars(select(RecordEvent)).all()) == events
    assert _edits(again) == []
    assert _toasts(again) == [plan_screen.SAME_TOAST] * 2


def test_another_persons_tap_is_refused_and_writes_nothing(db, owner, reader, clock):
    _two_buckwheats(db, owner)
    reader.plan = 'star "гречка"'
    wire = run(said(text("добавь гречку в избранное")))
    plan, mid = _plan(db), _questions(wire)[0][0]
    other = User(telegram_id=OWNER + 1, settings={"timezone": "Asia/Tbilisi"})
    db.add(other)
    db.flush()
    plan.user_id = other.id  # the tapper is not the plan's person
    db.commit()
    events = len(db.scalars(select(RecordEvent)).all())
    tap = run(
        tapped(f"pl:c:{plan.id}:0:0", message_id=mid),
        tapped(f"pl:q:{plan.id}:0", message_id=mid),
    )
    assert _stars(db, owner) == [] and _edits(tap) == []
    assert _toasts(tap) == [plan_service.GONE_TOAST] * 2
    assert len(db.scalars(select(RecordEvent)).all()) == events
    assert _asks(db)[0]["state"] == "open"


def test_a_dish_hidden_between_question_and_tap_is_a_failure_line(
    db, owner, reader, clock
):
    first, _second = _two_buckwheats(db, owner)
    reader.plan = 'star "гречка"'
    wire = run(said(text("добавь гречку в избранное")))
    plan, mid = _plan(db), _questions(wire)[0][0]
    first.hidden_at = datetime.now(UTC)
    db.commit()
    events = len(db.scalars(select(RecordEvent)).all())
    tap = run(tapped(f"pl:c:{plan.id}:0:0", message_id=mid))
    (done,) = _edits(tap)
    assert done["text"].startswith("✖ Не вышло: «гречка»")
    assert _buttons(done) == []  # nothing written: nothing to take back
    assert _stars(db, owner) == []
    assert len(db.scalars(select(RecordEvent)).all()) == events


def _day_of_picks(db, owner):
    for pair in (BUCKWHEATS, ("суп овощной", "суп куриный")):
        for name in pair:
            _dish(db, owner, name)


def test_one_answer_is_undone_alone_and_a_second_tap_does_nothing(
    db, owner, reader, clock
):
    _day_of_picks(db, owner)
    reader.plan = 'star "гречка"\nstar "суп"'
    wire = run(said(text("добавь гречку и суп в избранное")))
    plan = _plan(db)
    (a, b) = _questions(wire)
    run(
        tapped(f"pl:c:{plan.id}:0:0", message_id=a[0]),
        tapped(f"pl:c:{plan.id}:1:0", message_id=b[0]),
    )
    assert sorted(_stars(db, owner)) == ["гречка с маслом", "суп овощной"]
    undone = run(tapped(f"pl:q:{plan.id}:1", message_id=b[0]))
    assert _stars(db, owner) == ["гречка с маслом"]  # only that answer
    (edited,) = _edits(undone)
    assert "↩️ Отменено: ⭐ суп овощной." in edited["text"]
    assert _buttons(edited) == []
    states = [x["state"] for x in _asks(db)]
    assert states == ["done", "undone"]
    again = run(tapped(f"pl:q:{plan.id}:1", message_id=b[0]))
    assert _edits(again) == [] and _toasts(again) == [plan_screen.SAME_TOAST]
    assert _stars(db, owner) == ["гречка с маслом"]


def test_the_plans_undo_takes_back_the_answered_questions_too(db, owner, reader, clock):
    _day_of_picks(db, owner)
    _dish(db, owner, "борщ")
    reader.plan = 'star "борщ"\nstar "гречка"\nstar "суп"'
    wire = run(said(text("добавь борщ, гречку и суп в избранное")))
    plan = _plan(db)
    (a, b) = _questions(wire)
    run(tapped(f"pl:c:{plan.id}:0:0", message_id=a[0]))  # the second stays open
    assert sorted(_stars(db, owner)) == ["борщ", "гречка с маслом"]
    run(tapped(f"pl:u:{plan.id}"))
    assert _stars(db, owner) == []
    assert [x["state"] for x in _asks(db)] == ["undone", "closed"]
    late = run(tapped(f"pl:q:{plan.id}:0", message_id=a[0]))
    assert _edits(late) == [] and _toasts(late) == [plan_screen.SAME_TOAST]
    assert _stars(db, owner) == []


def test_at_most_three_questions_go_out_each_its_own_message(db, owner, reader, clock):
    for pair in (
        BUCKWHEATS,
        ("суп овощной", "суп куриный"),
        ("каша рисовая", "каша овсяная"),
        ("борщ красный", "борщ зелёный"),
    ):
        for name in pair:
            _dish(db, owner, name)
    reader.plan = 'star "гречка"\nstar "суп"\nstar "каша"\nstar "борщ"'
    wire = run(said(text("добавь гречку, суп, кашу и борщ в избранное")))
    assert len(_questions(wire)) == 3
    assert [a["q"] for a in _asks(db)] == [0, 1, 2]


def test_the_largest_callback_data_fits_telegrams_64_bytes():
    plan_id = 2**31 - 1
    for kind in ("pick", "ask"):
        ask = {"kind": kind, "verb": "merge", "label": "x", "q": 2}
        markup = plan_screen.question_markup(plan_id, ask, ["a", "b", "c", "d"])
        data = [b.callback_data for row in markup.inline_keyboard for b in row]
        assert data and all(len(d.encode()) <= 64 for d in data), data
    undo = plan_screen.done_markup(plan_id, 2, True).inline_keyboard[1][0]
    assert len(undo.callback_data.encode()) <= 64


@pytest.mark.parametrize(
    "data", ["pl:c:1:0", "pl:c:1:0:z", "pl:q:1:0:y", "pl:c:x:0:0", "pl:c:1:99:0"]
)
def test_a_strange_question_tap_moves_nothing(db, owner, reader, clock, data):
    _two_buckwheats(db, owner)
    reader.plan = 'star "гречка"'
    run(said(text("добавь гречку в избранное")))
    events = len(db.scalars(select(RecordEvent)).all())
    run(tapped(data))
    assert _stars(db, owner) == []
    assert len(db.scalars(select(RecordEvent)).all()) == events
    assert _asks(db)[0]["state"] == "open"


def test_the_state_survives_a_fresh_session_read(db, owner, reader, clock):
    _two_buckwheats(db, owner)
    reader.plan = 'star "гречка"'
    wire = run(said(text("добавь гречку в избранное")))
    plan, mid = _plan(db), _questions(wire)[0][0]
    run(tapped(f"pl:c:{plan.id}:0:0", message_id=mid))
    with _fresh(db) as fresh:
        (row,) = fresh.scalars(select(MessagePlan)).all()
        (ask,) = row.commands["asks"]
        assert ask["state"] == "done" and ask["events"] and ask["message_id"] == mid
        assert row.commands["acts"] == []  # the plan's own fields kept beside


def test_the_crud_assigns_a_new_list_so_a_change_is_seen(db, owner, reader, clock):
    _two_buckwheats(db, owner)
    reader.plan = 'star "гречка"'
    run(said(text("добавь гречку в избранное")))
    plan = _plan(db)
    asks = crud_message_plan.asks_of(plan)
    asks[0]["state"] = "declined"
    assert plan.commands["asks"][0]["state"] == "open"  # a copy: nothing moved yet
    crud_message_plan.set_asks(plan, asks)
    db.commit()
    with _fresh(db) as fresh:
        assert fresh.scalars(select(MessagePlan)).one().commands["asks"][0][
            "state"
        ] == ("declined")


def test_the_crud_still_imports_no_telegram_capture_or_dish_ops():
    tree = ast.parse((ROOT / "app/crud/crud_message_plan.py").read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    assert not [
        n for n in names if "telegram" in n or "capture" in n or "dish_ops" in n
    ]
    assert PersonalFood  # imported for the dish rows these tests make
