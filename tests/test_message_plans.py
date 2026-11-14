"""One message, several actions, through the bot: meals each at its own time, and dish
changes said in words with one answer and «↩️ Вернуть всё». The planner is a fake
(``reader.plan``); everything after it is real.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from telegram import Update

from app.crud.crud_message_plan import crud_message_plan
from app.models import (
    AiCallLog,
    FavoriteMeal,
    MealSource,
    MessagePlan,
    PersonalFood,
    RecordEvent,
)
from app.services import capture, plan_service
from app.services import telegram as tg
from tests.test_capture_failures import _a_clean_run  # noqa: F401,E402 (autouse)
from tests.test_capture_router import _voice  # noqa: E402
from tests.test_capture_router import (  # noqa: F401 (fixtures)
    CHAT,
    NOW,
    NOW_TS,
    OWNER,
    Session,
    Wire,
    _meals,
    _message,
    _with_app,
    clock,
    db,
    owner,
    reader,
    text,
)
from tests.test_capture_voice import _ogg, seen, whisper  # noqa: F401,E402

DAY = "на завтрак овсянка, в обед суп"


async def _drain() -> None:
    # A plan's task starts the reads of its meals: drain until nothing is left.
    while tg._meal_reads:
        await asyncio.gather(*list(tg._meal_reads), return_exceptions=True)


def run(*updates) -> Wire:
    """Each update through the application, everything it started drained."""
    box = {}

    async def steps(application, wire):
        box["wire"] = wire
        for update in updates:
            await application.process_update(Update.de_json(update, application.bot))
            await _drain()

    asyncio.run(_with_app(steps))
    return box["wire"]


def said(message: dict) -> dict:
    return {"update_id": message["message_id"], "message": message}


def edit(message: dict) -> dict:
    return {
        "update_id": message["message_id"] + 900000,
        "edited_message": {**message, "edit_date": NOW_TS + 300},
    }


def tapped(data: str, message_id: int = 4900) -> dict:
    message = _message()
    return {
        "update_id": message["message_id"],
        "callback_query": {
            "id": "1",
            "from": message["from"],
            "chat_instance": "c",
            "data": data,
            "message": {
                "message_id": message_id,
                "date": NOW_TS,
                "chat": message["chat"],
                "text": "ответ",
            },
        },
    }


def _plan(db) -> MessagePlan:
    db.expire_all()
    return db.scalars(select(MessagePlan)).one()


def _local(meal) -> datetime:
    return meal.meal_time.replace(tzinfo=None) + timedelta(hours=4)


def _dish(db, owner, name, **columns) -> PersonalFood:
    dish = PersonalFood(user_id=owner.id, canonical_name=name, **columns)
    db.add(dish)
    db.commit()
    return dish


def _live_dishes(db, owner) -> set:
    db.expire_all()
    return {
        d.canonical_name
        for d in db.scalars(
            select(PersonalFood).where(
                PersonalFood.user_id == owner.id,
                PersonalFood.merged_into_id.is_(None),
                PersonalFood.hidden_at.is_(None),
            )
        )
    }


def _stars(db, owner) -> list:
    db.expire_all()
    return [
        f.name
        for f in db.scalars(
            select(FavoriteMeal).where(
                FavoriteMeal.user_id == owner.id, FavoriteMeal.removed_at.is_(None)
            )
        )
    ]


def _buttons(params: dict) -> list:
    markup = params.get("reply_markup") or {}
    if isinstance(markup, str):
        import json

        markup = json.loads(markup)
    rows = markup.get("inline_keyboard", [])
    return [[(b["text"], b.get("callback_data")) for b in row] for row in rows]


def _answer(wire: Wire) -> dict:
    """The dish changes' answer: the last line that holds ↩️ or says ✓ / ✖."""
    shown = [
        p
        for n, p in wire.calls
        if n in ("sendMessage", "editMessageText")
        and any(mark in p.get("text", "") for mark in ("✓", "✖"))
    ]
    return shown[-1]


# --- several meals -------------------------------------------------------------------


def test_a_day_in_one_message_is_a_meal_per_part(db, owner, reader, clock):
    reader.plan = 'meal "на завтрак овсянка"\nmeal "в обед суп"'
    wire = run(said(text(DAY)))

    meals = sorted(_meals(db), key=lambda m: m.id)
    assert [(_local(m).hour, m.time_precision) for m in meals] == [
        (9, "approx"),
        (13, "approx"),
    ]
    assert {_local(m).date() for m in meals} == {datetime(2026, 10, 26).date()}
    assert sorted(reader.text) == ["овсянка", "суп"]  # each read alone, no time words
    sources = db.scalars(select(MealSource).order_by(MealSource.part)).all()
    assert [(s.part, s.text) for s in sources] == [
        (1, "на завтрак овсянка"),
        (2, "в обед суп"),
    ]
    assert len({s.message_id for s in sources}) == 1
    # Meal 1 takes the «читаю…» line; meal 2 is a reply of its own.
    first = wire.line(0)
    assert first[0] == plan_service.READING_LINE
    assert "овсянка" in first[1]
    assert "суп" in wire.line(1)[0]
    plan = _plan(db)
    assert plan.status == crud_message_plan.DONE
    assert plan.meal_ids == [m.id for m in meals]
    assert db.scalars(select(AiCallLog.kind)).all().count("plan") == 1


def test_parts_of_one_message_are_not_offered_to_join(db, owner, reader, clock):
    reader.plan = 'meal "на завтрак овсянка"\nmeal "в обед суп"'
    wire = run(said(text(DAY)))
    chips = [
        data
        for n, p in wire.calls
        if n in ("sendMessage", "editMessageText")
        for row in _buttons(p)
        for _label, data in row
    ]
    assert not any((data or "").startswith("ml:j:") for data in chips), chips


def test_a_replayed_message_plans_once(db, owner, reader, clock):
    reader.plan = 'meal "на завтрак овсянка"\nmeal "в обед суп"'
    message = text(DAY)
    run(said(message), said(message))
    assert len(_meals(db)) == 2
    assert len(reader.planned) == 1


def test_a_replayed_dish_command_plans_once(db, owner, reader, clock):
    # No meal, so no source holds the message: only the plan row stops the replay.
    _dish(db, owner, "гречка")
    reader.plan = 'star "гречка"'
    message = text("добавь гречку в избранное")
    wire = run(said(message), said(message))
    assert len(reader.planned) == 1
    assert sum("✓ ⭐ гречка" in t for t in wire.seen()) == 1


def test_each_replay_guard_holds_on_its_own(db, owner, reader, clock):
    # Three layers stop a replay; the flow test above cannot tell them apart.
    _dish(db, owner, "гречка")
    reader.plan = 'star "гречка"'
    message = text("добавь гречку в избранное")
    run(said(message))
    plan = _plan(db)
    # 1. capture.seen: a planned message with no source is seen.
    seen = SimpleNamespace(chat_id=CHAT, message_id=message["message_id"], kind="text")
    assert capture.seen(db, seen)
    # 2. The claim: a second plan on the message is refused by the key.
    again = crud_message_plan.open(
        db,
        user_id=owner.id,
        chat_id=CHAT,
        message_id=message["message_id"],
        kind="text",
        text="x",
        sent_at=NOW,
        want="dish",
    )
    assert again is None
    # 3. A plan that is no longer reading is not run again.
    asyncio.run(
        _with_app(lambda application, _wire: tg._run_plan(application, plan.id))
    )
    assert len(reader.planned) == 1 and _stars(db, owner) == ["гречка"]


def test_past_the_cap_the_rest_is_named(db, owner, reader, clock):
    times = [f"в {h}:00" for h in range(4, 13)]  # nine parts, all before 13:20
    words = ", ".join(f"{t} чай" for t in times)
    reader.plan = "\n".join(f'meal "{t} чай"' for t in times)
    wire = run(said(text(words)))
    assert len(_meals(db)) == 8
    assert any("Больше 8" in t for t in wire.seen())


def test_food_the_planner_dropped_is_said_back(db, owner, reader, clock):
    reader.plan = 'meal "на завтрак овсянка"\nmeal "в обед суп"'
    wire = run(said(text(DAY + ", вечером сырники")))
    assert len(_meals(db)) == 2
    assert any("Не записано: «вечером сырники»" in t for t in wire.seen())


def test_a_failing_planner_on_meals_writes_one_meal_as_before(db, owner, reader, clock):
    reader.plan = None
    wire = run(said(text(DAY)))
    (meal,) = _meals(db)
    assert meal.time_precision == "day_only"  # R0, as before plans
    assert _plan(db).status == crud_message_plan.SINGLE
    assert wire.line(0)[1].startswith("Записано")


def test_an_edited_planned_message_is_not_read_again(db, owner, reader, clock):
    reader.plan = 'meal "на завтрак овсянка"\nmeal "в обед суп"'
    message = text(DAY)
    wire = run(said(message), edit({**message, "text": DAY + ", вечером сырники"}))
    assert len(_meals(db)) == 2
    assert capture.M11_PLANNED in wire.seen()


def test_a_correction_waits_while_a_plan_reads(db, owner, reader, clock):
    crud_message_plan.open(
        db,
        user_id=owner.id,
        chat_id=CHAT,
        message_id=1,
        kind="text",
        text=DAY,
        sent_at=NOW,
        want="meals",
        created_at=NOW,
    )
    wire = run(said(text("12:30")))
    assert capture.STILL_READING in wire.seen()


# --- dish changes in words -----------------------------------------------------------


def test_dish_changes_apply_at_once_with_one_answer_and_undo(db, owner, reader, clock):
    _dish(db, owner, "сырок глазированный")
    _dish(db, owner, "сырки глазированные")
    _dish(db, owner, "гречка")
    reader.plan = 'merge "сырок глазированный" "сырки глазированные"\nstar "гречка"'
    wire = run(
        said(text("сырки глазированные — одно блюдо, добавь гречку в избранное"))
    )

    assert _meals(db) == []
    assert len(_live_dishes(db, owner)) == 2
    assert _stars(db, owner) == ["гречка"]
    answer = _answer(wire)
    assert "✓ Одно блюдо" in answer["text"] and "✓ ⭐ гречка" in answer["text"]
    rows = _buttons(answer)
    assert rows[0][0][0] == "✅ ОК"  # ✅ ОК first: the answer holds an undo
    plan = _plan(db)
    assert rows[1] == [("↩️ Вернуть всё", f"pl:u:{plan.id}")]

    undone = run(tapped(f"pl:u:{plan.id}"))
    assert _live_dishes(db, owner) == {
        "сырок глазированный",
        "сырки глазированные",
        "гречка",
    }
    assert _stars(db, owner) == []
    assert any("Возвращено" in t for t in undone.seen())

    again = run(tapped(f"pl:u:{plan.id}"))  # a second tap moves nothing
    assert len(_live_dishes(db, owner)) == 3
    assert again.seen() == []


def test_a_refused_line_says_so_and_the_rest_is_done(db, owner, reader, clock):
    _dish(db, owner, "гречка")
    reader.plan = 'star "гречка"\nhide "пицца"'
    events = db.scalars(select(RecordEvent)).all()
    wire = run(said(text("добавь гречку в избранное, убери пиццу из блюд")))
    words = _answer(wire)["text"]
    assert "✓ ⭐ гречка" in words
    assert "✖ Не вышло: «пицца» — такого блюда нет." in words
    assert plan_service.REST_DONE in words
    db.expire_all()
    new = db.scalars(select(RecordEvent)).all()[len(events) :]
    assert [e.kind for e in new] == ["food_star"]  # nothing for the refused hide


def test_a_dish_command_that_finds_nothing_writes_no_meal(db, owner, reader, clock):
    _dish(db, owner, "борщ")
    reader.plan = "none"
    wire = run(said(text("добавь гречку в избранное")))
    assert _meals(db) == []  # control: before plans this became an ADD or a meal
    assert plan_service.NOTHING_LINE in wire.seen()


def test_a_failing_planner_on_dishes_offers_again(db, owner, reader, clock):
    _dish(db, owner, "гречка")
    reader.plan = None
    wire = run(said(text("добавь гречку в избранное")))
    assert _meals(db) == [] and _stars(db, owner) == []
    plan = _plan(db)
    assert plan.status == crud_message_plan.FAILED
    failed = wire.line(0)[-1]
    assert failed.startswith(capture.PLAN_FAILED)

    reader.plan = 'star "гречка"'
    run(tapped(f"pl:r:{plan.id}", message_id=plan.line_message_id))
    assert _stars(db, owner) == ["гречка"]
    assert _plan(db).status == crud_message_plan.DONE


def test_another_persons_plan_cannot_be_undone(db, owner, reader, clock):
    _dish(db, owner, "гречка")
    reader.plan = 'star "гречка"'
    run(said(text("добавь гречку в избранное")))
    plan = _plan(db)
    plan.user_id = owner.id + 1000  # as if it were someone else's
    db.commit()
    run(tapped(f"pl:u:{plan.id}"))
    assert _stars(db, owner) == ["гречка"]


# --- the sweep -----------------------------------------------------------------------


def test_a_plan_lost_in_a_restart_gets_its_failure_line(db, owner, reader, clock):
    long_ago = NOW - capture.STUCK_AFTER - timedelta(minutes=1)
    plan_id = crud_message_plan.open(
        db,
        user_id=owner.id,
        chat_id=CHAT,
        message_id=1,
        kind="text",
        text="добавь гречку в избранное",
        sent_at=long_ago,
        want="dish",
        line_message_id=777,
        created_at=long_ago,
    ).id
    box = {}

    async def steps(application, wire):
        box["wire"] = wire
        await tg._sweep_plans(application)

    asyncio.run(_with_app(steps))
    plan = _plan(db)
    assert plan.id == plan_id and plan.status == crud_message_plan.FAILED
    (shown,) = box["wire"].edits()
    assert shown["message_id"] == 777 and shown["text"].startswith(capture.PLAN_FAILED)


@pytest.mark.parametrize("data", ["pl:u:1", "pl:r:1", "pl:x:1", "pl:u:abc"])
def test_callback_data_is_short_and_a_strange_one_moves_nothing(db, owner, data):
    assert len(f"pl:u:{2**31}".encode()) <= 64
    run(tapped(data))
    assert db.scalars(select(MessagePlan)).all() == []


# --- a voice -------------------------------------------------------------------------


def _live_meals(db) -> list:
    return sorted((m for m in _meals(db) if m.deleted_at is None), key=lambda m: m.id)


def test_a_voice_day_is_a_meal_per_part_and_the_voice_meal_retires(
    db, seen, reader, whisper, clock
):
    reader.plan = 'meal "на завтрак овсянка"\nmeal "в обед суп"'
    whisper.answers(DAY)
    note = _voice()
    wire = run(said(note))

    meals = _live_meals(db)
    assert [(_local(m).hour, m.time_precision) for m in meals] == [
        (9, "approx"),
        (13, "approx"),
    ]
    parts = db.scalars(
        select(MealSource).where(MealSource.part > 0).order_by(MealSource.part)
    ).all()
    assert [(s.part, s.kind, s.message_id) for s in parts] == [
        (1, "text", note["message_id"]),
        (2, "text", note["message_id"]),
    ]
    assert len(_meals(db)) == 3  # the voice's own meal, retired
    assert sorted(reader.text) == ["овсянка", "суп"]
    # Meal 1 takes the voice's line; meal 2 is a reply of its own.
    assert "≈ 9:00" in wire.line(0)[-1]
    assert "≈ 13:00" in wire.line(1)[-1]


def test_a_voice_with_a_failing_planner_is_read_as_food_as_before(
    db, seen, reader, whisper, clock
):
    reader.plan = None
    whisper.answers(DAY)
    run(said(_voice()))
    (meal,) = _live_meals(db)
    assert meal.time_precision == "day_only"  # R0, as before plans
    assert len(reader.planned) == 1
    assert _plan(db).status == crud_message_plan.SINGLE


def test_a_voice_dish_command_is_done_and_answered_on_its_line(
    db, seen, reader, whisper, clock
):
    _dish(db, seen, "гречка")
    reader.plan = 'star "гречка"'
    whisper.answers("добавь гречку в избранное")
    wire = run(said(_voice()))
    assert _live_meals(db) == []
    assert _stars(db, seen) == ["гречка"]
    assert "✓ ⭐ гречка" in wire.line(0)[-1]
