"""D1 «за день»: a message that lists a day becomes several meals through the planner,
even when the parts say no times. The planner is a fake; everything after it is real.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services import plan_grammar as grammar
from app.services import plan_words
from app.services.meal_words import says_several
from app.services.plan_service import Book, check
from tests.test_capture_router import Session, _meals  # noqa: F401
from tests.test_message_plans import (  # noqa: F401 (fixtures)
    _a_clean_run,
    _local,
    clock,
    db,
    owner,
    reader,
    run,
    said,
    text,
)

CORPUS = Path(__file__).parent / "fixtures" / "day_plan_corpus.json"
SENT = datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)
ZONE = timezone(timedelta(hours=4))

# Strings of the recorded corpus that hold a new trigger phrase, and so may answer
# differently now. The corpus (recorded before the change) holds none.
HOLD_NEW_TRIGGER: set = set()


def test_the_old_answers_are_unchanged_except_the_new_trigger():
    corpus = json.loads(CORPUS.read_text())
    assert len(corpus) > 1000  # every string of the plan and capture tests
    assert any(wants == plan_words.MEALS for wants, _ in corpus.values())  # control
    changed = set()
    for message, (wants, several) in corpus.items():
        assert bool(says_several(message, SENT, ZONE)) == several, message
        if plan_words.wants_plan(message, SENT, ZONE) != wants:
            changed.add(message)
    assert changed == HOLD_NEW_TRIGGER
    assert {m for m in corpus if plan_words.says_day(m)} == HOLD_NEW_TRIGGER


def test_a_day_list_is_three_meals_without_times(db, owner, reader, clock):
    reader.plan = 'meal "овсянка с бананом"\nmeal "суп"\nmeal "сырники"'
    wire = run(said(text("за день: овсянка с бананом, суп, сырники")))

    meals = sorted(_meals(db), key=lambda m: m.id)
    assert [m.description for m in meals] == ["овсянка с бананом", "суп", "сырники"]
    assert {m.time_precision for m in meals} == {"day_only"}
    assert {_local(m).date() for m in meals} == {datetime(2026, 10, 26).date()}
    assert "суп" in wire.line(1)[0] and "сырники" in wire.line(2)[0]
    assert len(reader.planned) == 1


def test_the_days_word_of_the_message_reaches_parts_that_dropped_it(
    db, owner, reader, clock
):
    reader.plan = 'meal "суп"\nmeal "сырники"'
    run(said(text("вчера ела: суп, сырники")))

    meals = _meals(db)
    assert len(meals) == 2
    assert {m.time_precision for m in meals} == {"day_only"}
    assert {_local(m).date() for m in meals} == {datetime(2026, 10, 25).date()}


def test_a_part_with_its_own_time_keeps_it(db, owner, reader, clock):
    reader.plan = 'meal "овсянка"\nmeal "в 12:30 суп"'
    run(said(text("за день: овсянка, в 12:30 суп")))

    by = {m.description: m for m in _meals(db)}
    assert by["суп"].time_precision != "day_only"
    assert (_local(by["суп"]).hour, _local(by["суп"]).minute) == (12, 30)
    assert by["овсянка"].time_precision == "day_only"


@pytest.mark.parametrize(
    "message", ["за день съела много", "что я ела за день?", "сегодня ела суп"]
)
def test_the_trigger_does_not_fire_on_one_clause_or_a_question(message):
    assert plan_words.wants_plan(message, SENT, ZONE) is None


@pytest.mark.parametrize(
    "message",
    [
        "за день: овсянка, суп",
        "За весь день\nовсянка\nсуп",
        "вчера ела: суп, сырники",
        "сегодня ели суп и сырники",
        "за сегодня: каша; суп",
    ],
)
def test_the_trigger_fires_on_clauses_and_lines(message):
    assert plan_words.wants_plan(message, SENT, ZONE) == plan_words.MEALS


def test_a_forward_is_not_a_day_list(db, owner, reader, clock):
    reader.plan = 'meal "суп"\nmeal "сырники"'
    message = text("за день: суп, сырники")
    message["forward_origin"] = {
        "type": "hidden_user",
        "sender_user_name": "x",
        "date": message["date"] - 86400,
    }
    run(said(message))
    assert reader.planned == []  # never planned: a forward's time is its own


def test_a_word_of_the_trigger_is_not_named_as_missed():
    message = "за весь день: суп, сырники"
    checked = check(grammar.parse('meal "суп"\nmeal "сырники"'), message, Book())
    assert checked.missed == []
