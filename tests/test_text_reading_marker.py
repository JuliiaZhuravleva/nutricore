"""A typed meal's line says «Считаю…» while its read is out, and only then.

Typed words show at once, so without the marker the line looks finished for the seconds
the model takes, and the meal looks recorded with no read at all (owner 2026-11-08).
Every way the read ends takes the marker away: the answer, a failure, the sweep. A tap
back to the line draws none (TD-260).
"""

from __future__ import annotations

from sqlalchemy import select

from app.models import Meal, MealMessage
from app.services import capture
from app.services.meal_card import M4T_READING
from tests.test_capture_background import (  # noqa: F401 (fixtures)
    _pending_row,
    _sweep_at,
    at,
    no_named_emoji,
)
from tests.test_capture_failures import tap
from tests.test_capture_router import (  # noqa: F401 (fixtures)
    NOW,
    Session,
    _meals,
    db,
    owner,
    reader,
    send,
    text,
)


def test_a_typed_meal_says_it_is_counting_until_the_answer(db, owner, reader):
    wire = send(text("суп с хлебом"))
    first = wire.sent()[0]["text"].splitlines()
    assert first[:2] == ["Записано: суп с хлебом.", M4T_READING]
    answer = wire.edits()[-1]["text"]
    assert answer.startswith("Записано: ")
    assert M4T_READING not in answer
    assert _meals(db)[0].reading_status == "read"


def test_a_failed_read_takes_the_marker_away(db, owner, reader):
    reader.error = ValueError("not json")
    wire = send(text("суп с хлебом"))
    assert M4T_READING in wire.sent()[0]["text"]
    assert M4T_READING not in wire.edits()[-1]["text"]
    assert _meals(db)[0].reading_status == "failed"


def test_the_sweep_giving_up_a_stuck_text_takes_the_marker_away(db, owner, reader, at):
    meal = _pending_row(db, owner, kind="text", minutes_ago=6)
    wire = _sweep_at({}, at=at)
    db.expire_all()
    assert meal.reading_status == "failed"
    assert wire.edits(), "the sweep left the line as it was"
    assert all(M4T_READING not in edit["text"] for edit in wire.edits())


def test_back_to_a_typed_line_while_reading_promises_no_count(db, owner, reader):
    """A tap back may land on a message the read will never edit: no marker there, or it
    would say «Считаю…» for good. The row still waits for the read (TD-260)."""
    meal = Meal(
        user_id=owner.id,
        meal_time=NOW,
        sent_at=NOW,
        description="овсянка на воде",
        reading_status="pending",
        numbers_basis="none",
    )
    db.add(meal)
    db.commit()
    wire = tap(f"ml:r:{meal.id}", message_id=4901)
    edited = wire.edits()[-1]["text"]
    assert "овсянка на воде" in edited  # known-positive: the line was drawn
    assert M4T_READING not in edited
    view = db.scalar(select(MealMessage.view).where(MealMessage.message_id == 4901))
    assert view == "pending"


def test_back_to_a_read_line_has_no_marker(db, owner, reader):
    send(text("суп с хлебом"))
    meal = _meals(db)[0]
    assert meal.reading_status == "read"
    back = tap(f"ml:r:{meal.id}").edits()[-1]["text"]
    assert back.startswith("Записано:")
    assert M4T_READING not in back


def test_the_marker_is_one_line_of_its_own():
    # A line Telegram shows under the record, not glued to the words someone typed.
    assert "\n" not in M4T_READING and M4T_READING.endswith("…")
    assert capture.M4T_READING == M4T_READING
