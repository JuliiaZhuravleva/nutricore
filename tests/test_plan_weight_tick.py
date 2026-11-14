"""D2 inside a plan: «вес 72,4, зарядку сделала, на завтрак каша» is one plan, one answer
and one «↩️ Отменить». The planner is scripted; everything after it is real.

Three layers: the grammar and the check with no rows, the plan's execute and undo on a
real database (SQLite always, Postgres when ``TWO_PEOPLE_DB_URL`` is set: a unique
(user, routine, day) clash aborts a whole commit there), and the bot end to end.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy import text as sql
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.crud.crud_message_plan import crud_message_plan
from app.db.base import Base
from app.models import BodyMetric, Meal, MessagePlan, RoutineTick, User
from app.services import capture, plan_grammar, plan_service
from app.services import plan_words as words
from app.services import routines, stats_screen
from app.services.plan_service import Book, check
from app.services.settings_screen import user_zone
from tests.test_capture_failures import _a_clean_run  # noqa: F401,E402 (autouse)
from tests.test_capture_router import (  # noqa: F401 (fixtures)
    CHAT,
    NOW,
    OWNER,
    Session,
    _meals,
    clock,
    db,
    owner,
    reader,
    text,
)
from tests.test_message_plans import _answer, _buttons, _plan, run, said, tapped

PG_URL = os.environ.get("TWO_PEOPLE_DB_URL")
BACKENDS = pytest.mark.parametrize(
    "backend",
    [
        "sqlite",
        pytest.param(
            "postgres",
            marks=pytest.mark.skipif(not PG_URL, reason="TWO_PEOPLE_DB_URL unset"),
        ),
    ],
)
MESSAGE = "вес 72,4, зарядку сделала, на завтрак каша"
LINES = 'weight 72.4\ntick @1 "Зарядка"\nmeal "на завтрак каша"'
ROUTINE = {
    "id": "r1",
    "name": "Зарядка",
    "icon": "💊",
    "schedule": routines.DAILY,
    "days": [],
}


def _book(yesterday: bool = False, names=("Зарядка",)) -> Book:
    book = Book()
    book.yesterday = yesterday
    book.day = datetime(2026, 11, 14).date() - timedelta(days=int(yesterday))
    book.routines = [(f"r{n}", name) for n, name in enumerate(names, start=1)]
    return book


def _checked(answer: str, message: str = MESSAGE, book: Book | None = None):
    return check(plan_grammar.parse(answer), message, book or _book())


# --- the grammar ---------------------------------------------------------------------


def test_weight_and_tick_lines_parse_with_a_routine_number_of_their_own():
    parsed = plan_grammar.parse('weight 72,4 day="вчера"\ntick @2 "Зарядка"')
    (weight,) = parsed.weights
    assert (weight.kg, weight.yesterday) == (72.4, True)
    (tick,) = parsed.ticks
    assert tick.refs == (plan_grammar.Ref(2, "Зарядка"),)
    assert parsed.bad == []


@pytest.mark.parametrize(
    "line",
    [
        'tick #1 "Зарядка"',  # a dish's #n never ticks a routine
        'tick #1 @1 "Зарядка"',
        'tick @1 #1 "Зарядка"',
        "tick @1",
        'tick "Зарядка"',
        'star @1 "борщ"',  # and a routine's @n never stars a dish
        "weight",
        "weight много",
        'weight 72.4 day="завтра"',
    ],
)
def test_a_line_that_mixes_the_numbers_does_not_parse(line):
    parsed = plan_grammar.parse(line)
    assert parsed.commands == [] and len(parsed.bad) == 1, line


# --- the door and the prompt ---------------------------------------------------------

SENT = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "message, want",
    [
        (MESSAGE, words.MEALS),
        ("зарядку сделала, на завтрак каша", words.MEALS),
        ("вешу 72,4 кг. съела суп", words.MEALS),
        ("вес 72,4", None),  # the plain-words path's own
        ("отметь зарядку", None),
        ("вес 72,4 кг?", None),
        ("на завтрак каша", None),
    ],
)
def test_a_weight_or_tick_next_to_other_content_wants_the_planner(message, want):
    assert words.wants_plan(message, SENT, UTC) == want


def test_without_the_words_the_prompt_is_as_it_was():
    plain = plan_service._user_message(
        "суп", datetime(2026, 11, 14, 9, 0), "meals", Book()
    )
    assert "Routines" not in plain and plain.endswith("Message:\nсуп")
    listed = plan_service._user_message(
        MESSAGE, datetime(2026, 11, 14, 9, 0), "meals", _book(names=("Зарядка", "Йога"))
    )
    assert 'Routines (today):\n@1 "Зарядка"\n@2 "Йога"\nMessage:' in listed
    again = _book(yesterday=True)
    assert "Routines (yesterday)" in plan_service._user_message(
        MESSAGE, datetime(2026, 11, 14, 9, 0), "meals", again
    )
    assert words.says_weight_or_tick("Вешу 72") and words.says_weight_or_tick(
        "отметь x"
    )
    assert not words.says_weight_or_tick("весёлый салат, делал суп")


# --- the check -----------------------------------------------------------------------


def test_weight_and_tick_count_and_are_not_missed():
    checked = _checked(LINES)
    assert checked.weight == plan_service.Weigh(72.4, False)
    assert [t.name for t in checked.ticks] == ["Зарядка"]
    assert [p.text for p in checked.meals] == ["на завтрак каша"]
    assert checked.missed == [] and checked.refused == [] and not checked.single
    only = _checked("weight 72.4", "вес 72,4 вот")
    assert not only.single and only.meals == []
    assert _checked('tick @1 "Зарядка"', "зарядку сделала вот").single is False


def test_without_the_lines_the_words_are_missed_as_before():
    checked = _checked('meal "на завтрак каша"')
    assert [m.strip() for m in checked.missed] == ["вес 72", "зарядку сделала"]


def test_a_weight_out_of_range_is_refused_with_the_approved_text():
    checked = _checked(
        'weight 7.24\nmeal "на завтрак каша"', "вес 7,24, на завтрак каша"
    )
    (refusal,) = checked.refused
    assert checked.weight is None and refusal.final
    assert plan_service.NOT_DONE.format(label=refusal.label) + refusal.why == (
        "✖ Не вышло: «вес 7,24» — вес записываю от 20 до 300 кг."
    )
    assert _checked("weight 301", "вес 301").weight is None
    assert _checked("weight 20", "вес 20").weight is not None


def test_a_refused_weight_or_tick_is_not_also_missed():
    weight = _checked(
        'weight 7.24\nmeal "на завтрак каша"', "вес 7,24, на завтрак каша"
    )
    assert len(weight.refused) == 1 and weight.missed == []
    tick = _checked('tick @9 "медитация"\nmeal "каша"', "отметь медитацию, каша")
    assert len(tick.refused) == 1 and tick.missed == []


def test_the_weight_is_the_number_after_its_word():
    # «каша 200 г» says 200, but not as the weight: the model's «weight 200» is refused.
    checked = _checked("weight 200", "вес 72,4, каша 200 г")
    assert checked.weight is None and checked.refused[0].why == plan_service.WHY_WORDS
    assert _checked("weight 72.4", "вешу 72,4 кг, каша").weight is not None


def test_a_weight_the_message_does_not_say_is_refused():
    checked = _checked("weight 80", MESSAGE)
    assert checked.weight is None
    assert checked.refused[0].why == plan_service.WHY_WORDS
    day = _checked('weight 72.4 day="вчера"', MESSAGE)
    assert day.weight is None  # «вчера» is not in the message


def test_a_routine_out_of_the_list_is_refused_with_the_approved_text():
    checked = _checked('tick @9 "медитация"', "медитацию сделала, а")
    (refusal,) = checked.refused
    assert plan_service.NOT_DONE.format(label=refusal.label) + refusal.why == (
        "✖ Не вышло: «медитация» — этой рутины сегодня нет на карточке."
    )
    yesterday = _checked('tick @9 "медитация"', "вчера медитацию сделала", _book(True))
    assert yesterday.refused[0].why.endswith("этой рутины вчера нет на карточке.")
    # The number must carry the routine's own name: a wrong @n is no tick.
    assert _checked('tick @1 "Йога"', "йогу сделала, а").ticks == []


# --- the database --------------------------------------------------------------------


def _engine(backend: str):
    if backend == "postgres":
        engine = create_engine(PG_URL)
        names = ", ".join(t.name for t in Base.metadata.sorted_tables)
        with engine.begin() as connection:
            connection.execute(sql(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        return engine
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture
def wdb(backend):
    engine = _engine(backend)
    make = sessionmaker(bind=engine)
    with make() as session:
        session.add(User(id=1, telegram_id=42, settings={routines.ROUTINES: [ROUTINE]}))
        session.add(User(id=2, telegram_id=43, settings={}))
        session.commit()
        yield session
    engine.dispose()


_messages = iter(range(1, 10000))


def _go(db, answer: str, message: str = MESSAGE, now=None) -> tuple:
    """(executed, plan id): the plan opened, its routines listed, the scripted lines
    checked and executed, as the bot does."""
    now = now or datetime.now(UTC)
    user = db.get(User, 1)
    plan = crud_message_plan.open(
        db,
        user_id=user.id,
        chat_id=42,
        message_id=next(_messages),
        kind="text",
        text=message,
        sent_at=now,
        want="meals",
    )
    book = Book()
    plan_service._routines_of(db, user, message, book, datetime.now(user_zone(user)))
    checked = check(plan_grammar.parse(answer), message, book)
    came = capture.plan_came(plan, user.telegram_id)
    return plan_service.execute(db, plan.id, checked, came, now=now), plan.id


def _weights(db) -> list:
    db.expire_all()
    return [(m.weight, m.metrics) for m in db.scalars(select(BodyMetric))]


def _ticks(db) -> list:
    db.expire_all()
    return [(t.routine_id, t.day) for t in db.scalars(select(RoutineTick))]


def _today(db):
    return datetime.now(user_zone(db.get(User, 1))).date()


@BACKENDS
def test_the_meal_the_weight_and_the_tick_in_one_answer(wdb):
    executed, plan_id = _go(wdb, LINES)

    assert "завтрак" in executed.born[0].first.text
    assert executed.answer == "⚖️ 72,4 кг\n✓ Зарядка"
    assert "Не записано" not in executed.answer
    assert executed.changed
    assert _weights(wdb) == [(72.4, {"source": "plan"})]
    assert _ticks(wdb) == [("r1", _today(wdb))]
    rows = plan_service.answer_markup(plan_id, executed.changed).inline_keyboard
    assert [b.text for row in rows for b in row] == ["✅ ОК", "↩️ Отменить"]  # ОК first
    plan = wdb.get(MessagePlan, plan_id)
    assert plan.commands["weight"]["kg"] == 72.4
    assert plan.commands["ticks"] == [
        {
            "routine": "r1",
            "day": _today(wdb).isoformat(),
            "name": "Зарядка",
            "new": True,
        }
    ]


@BACKENDS
def test_undo_takes_the_weight_and_the_tick_back_and_the_meal_stays(wdb):
    _executed, plan_id = _go(wdb, LINES)

    head, toast = plan_service.undo(wdb, 42, plan_id)

    assert (head, toast) == ("↩️ Отменено: ⚖️ 72,4 кг, ✓ Зарядка.", None)
    assert _weights(wdb) == [] and _ticks(wdb) == []
    assert len(wdb.scalars(select(Meal)).all()) == 1  # each meal has its own ➖


@BACKENDS
def test_a_plan_with_only_a_weight_can_be_undone(wdb):
    executed, plan_id = _go(wdb, "weight 72.4", "вес 72,4 вот")
    assert executed.born == [] and executed.answer == "⚖️ 72,4 кг" and executed.changed

    assert plan_service.undo(wdb, 42, plan_id) == ("↩️ Отменено: ⚖️ 72,4 кг.", None)
    assert _weights(wdb) == []


@BACKENDS
def test_a_second_tap_changes_nothing_and_another_persons_is_refused(wdb):
    _executed, plan_id = _go(wdb, LINES)

    assert plan_service.undo(wdb, 43, plan_id) == (None, plan_service.GONE_TOAST)
    assert _weights(wdb) != [] and _ticks(wdb) != []  # nothing taken back by a stranger

    plan_service.undo(wdb, 42, plan_id)
    assert plan_service.undo(wdb, 42, plan_id) == (None, plan_service.SAME_TOAST)
    assert _weights(wdb) == [] and _ticks(wdb) == []


@BACKENDS
def test_a_routine_already_ticked_is_not_written_again_nor_taken_back(wdb):
    wdb.add(RoutineTick(user_id=1, routine_id="r1", day=_today(wdb)))
    wdb.commit()

    executed, plan_id = _go(wdb, LINES)

    assert executed.answer == "⚖️ 72,4 кг\n✓ Зарядка"  # said anyway
    assert _ticks(wdb) == [("r1", _today(wdb))]  # no second row, no crash
    head, _ = plan_service.undo(wdb, 42, plan_id)
    assert head == "↩️ Отменено: ⚖️ 72,4 кг."
    assert _ticks(wdb) == [("r1", _today(wdb))]  # the old tick stays


@BACKENDS
def test_only_an_old_tick_leaves_nothing_to_take_back(wdb):
    wdb.add(RoutineTick(user_id=1, routine_id="r1", day=_today(wdb)))
    wdb.commit()
    executed, plan_id = _go(wdb, 'tick @1 "Зарядка"', "зарядку сделала вот")
    assert executed.answer == "✓ Зарядка" and not executed.changed
    assert plan_service.undo(wdb, 42, plan_id) == (None, plan_service.SAME_TOAST)
    assert _ticks(wdb) == [("r1", _today(wdb))]


@BACKENDS
@pytest.mark.parametrize("by_hand", ["edited", "deleted"])
def test_a_weight_changed_after_the_plan_is_left_and_named(wdb, by_hand):
    _executed, plan_id = _go(wdb, LINES)
    row = wdb.scalars(select(BodyMetric)).one()
    if by_hand == "edited":
        row.weight = 73.0
    else:
        wdb.delete(row)
    wdb.commit()

    head, _ = plan_service.undo(wdb, 42, plan_id)

    assert head == (
        "↩️ Отменено: ✓ Зарядка. Не отменилось: «⚖️ 72,4 кг» — его меняли после."
    )
    assert _ticks(wdb) == []
    assert _weights(wdb) == (
        [(73.0, {"source": "plan"})] if by_hand == "edited" else []
    )


@BACKENDS
def test_yesterday_moves_the_weight_and_the_card(wdb):
    now = datetime.now(UTC)
    message = "вчера вес 72,4, зарядку сделала, на завтрак каша"
    executed, plan_id = _go(
        wdb, LINES.replace("72.4", '72.4 day="вчера"'), message, now
    )

    assert executed.answer == "⚖️ 72,4 кг — вчера\n✓ Зарядка — вчера"
    wdb.expire_all()
    (metric,) = wdb.scalars(select(BodyMetric)).all()
    stamp = metric.timestamp.replace(tzinfo=UTC)
    assert abs(stamp - (now - timedelta(days=1))) < timedelta(seconds=5)
    assert _ticks(wdb) == [("r1", _today(wdb) - timedelta(days=1))]
    assert plan_service.undo(wdb, 42, plan_id)[0] == (
        "↩️ Отменено: ⚖️ 72,4 кг, ✓ Зарядка."
    )


@BACKENDS
def test_refusals_say_so_and_write_nothing(wdb):
    executed, _ = _go(
        wdb,
        'weight 7.24\ntick @9 "медитация"\nmeal "на завтрак каша"',
        "вес 7,24, медитацию сделала, на завтрак каша",
    )
    assert "✖ Не вышло: «вес 7,24» — вес записываю от 20 до 300 кг." in executed.answer
    assert (
        "✖ Не вышло: «медитация» — этой рутины сегодня нет на карточке."
        in executed.answer
    )
    assert _weights(wdb) == [] and _ticks(wdb) == [] and not executed.changed


@BACKENDS
def test_a_failed_write_leaves_the_rest(wdb, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    def boom(*_a, **_k):
        raise IntegrityError("x", {}, Exception("clash"))

    monkeypatch.setattr(plan_service.crud_routine_tick, "tick_flush", boom)
    executed, _ = _go(wdb, LINES)
    assert (
        "⚖️ 72,4 кг" in executed.answer and "✖ Не вышло: «Зарядка»." in executed.answer
    )
    assert _weights(wdb) != [] and _ticks(wdb) == []


# --- the bot, end to end -------------------------------------------------------------

DAY = "вес 72,4, зарядку сделала, на завтрак каша, в обед суп"


def _with_routine(db, owner):
    owner.settings = {**owner.settings, routines.ROUTINES: [ROUTINE]}
    db.commit()


def test_the_bot_writes_all_three_with_one_undo(db, owner, reader, clock):
    _with_routine(db, owner)
    reader.plan = (
        'weight 72.4\ntick @1 "Зарядка"\nmeal "на завтрак каша"\nmeal "в обед суп"'
    )

    wire = run(said(text(DAY)))

    asked = reader.planned[0][-1]["content"]
    assert 'Routines (today):\n@1 "Зарядка"' in asked
    assert len(_meals(db)) == 2
    answer = _answer(wire)
    assert answer["text"] == "⚖️ 72,4 кг\n✓ Зарядка"
    assert not any("Не записано" in t or "Не понял" in t for t in wire.seen())
    rows = _buttons(answer)
    plan = _plan(db)
    assert rows[0][0][0] == "✅ ОК"
    assert rows[1] == [("↩️ Отменить", f"pl:u:{plan.id}")]
    assert [m.weight for m in db.scalars(select(BodyMetric))] == [72.4]

    undone = run(tapped(f"pl:u:{plan.id}"))
    heads = [line for t in undone.seen() for line in t.splitlines() if "↩️" in line]
    assert heads == ["↩️ Отменено: ⚖️ 72,4 кг, ✓ Зарядка."]
    db.expire_all()
    assert db.scalars(select(BodyMetric)).all() == []
    assert db.scalars(select(RoutineTick)).all() == []
    assert len(_meals(db)) == 2  # the meals stay

    again = run(tapped(f"pl:u:{plan.id}"))
    assert again.seen() == []


def test_the_bot_without_the_words_sends_no_routines(db, owner, reader, clock):
    _with_routine(db, owner)
    reader.plan = 'meal "на завтрак каша"\nmeal "в обед суп"'
    run(said(text("на завтрак каша, в обед суп")))
    assert "Routines" not in reader.planned[0][-1]["content"]


def test_the_plain_words_path_still_takes_a_whole_message_weight(
    db, owner, reader, clock
):
    run(said(text("вес 72,4")))
    assert reader.planned == []  # a whole-message weight never reaches the planner
    db.expire_all()
    assert [m.weight for m in db.scalars(select(BodyMetric))] == [72.4]
    assert [m.metrics["source"] for m in db.scalars(select(BodyMetric))] == ["bot"]


# --- the route: a weight or a tick next to food reaches the planner --------------------


def _came(text_: str) -> capture.Incoming:
    return capture.Incoming(
        telegram_id=OWNER,
        username="u",
        chat_id=CHAT,
        message_id=1,
        kind=capture.TEXT,
        text=text_,
        file_id=None,
        file_unique_id=None,
        file_size=None,
        duration_s=None,
        media_group_id=None,
        reply_to=None,
        sent_at=datetime.now(UTC),
        forward_date=None,
    )


def _route(db, text_: str) -> capture.Route:
    return capture.route_text(db, _came(text_))


@pytest.mark.parametrize(
    "message",
    [
        "вес 72,4, зарядку сделала, на завтрак каша",
        "отметь зарядку, на завтрак каша",
        "на завтрак каша, зарядку сделала",
        "на завтрак каша. вчера вес 72,4",
    ],
)
def test_a_weight_or_tick_beside_food_goes_to_the_planner(db, owner, message):
    _with_routine(db, owner)
    route = _route(db, message)
    assert (route.action, route.plan) == (capture.PLAN, words.MEALS)


@pytest.mark.parametrize(
    "message",
    [
        "сделала омлет",
        "суп и хлеб",
        "вешу себе кашу",
        "вес порции 300 г, каша",
        "омлет сделала, вкусно",
        "вес 72,4 и каша",
        "вес 72,4, это нормально?",  # a question asks, it writes nothing
        "зарядку сделала, а суп можно?",
    ],
)
def test_other_words_do_not_become_a_plan(db, owner, message):
    _with_routine(db, owner)
    assert _route(db, message).action != capture.PLAN


@pytest.mark.parametrize(
    "message, action",
    [
        ("вес 72,4", capture.WEIGHT),
        ("вчера вес 72,4", capture.WEIGHT),
        ("отметь зарядку", capture.TICK),
        ("зарядку сделала", capture.TICK),
        ("вчера отметь зарядку", capture.TICK),
    ],
)
def test_a_whole_message_weight_or_tick_keeps_its_route(db, owner, message, action):
    _with_routine(db, owner)
    assert _route(db, message).action == action


def test_the_route_ticks_nothing_by_itself(db, owner):
    _with_routine(db, owner)
    _route(db, "отметь зарядку, на завтрак каша")
    db.expire_all()
    assert db.scalars(select(RoutineTick)).all() == []


def test_the_headline_message_with_one_meal(db, owner, reader, clock):
    _with_routine(db, owner)
    reader.plan = 'weight 72.4\ntick @1 "Зарядка"\nmeal "на завтрак каша"'

    wire = run(said(text("вес 72,4, зарядку сделала, на завтрак каша")))

    assert len(reader.planned) == 1  # it went to the planner, not to NEW_MEAL
    assert len(_meals(db)) == 1
    answer = _answer(wire)
    assert answer["text"] == "⚖️ 72,4 кг\n✓ Зарядка"
    rows = _buttons(answer)
    assert rows[0][0][0] == "✅ ОК"
    assert rows[1] == [("↩️ Отменить", f"pl:u:{_plan(db).id}")]
    assert [m.weight for m in db.scalars(select(BodyMetric))] == [72.4]
    assert len(db.scalars(select(RoutineTick)).all()) == 1


@BACKENDS
def test_a_tick_taken_off_by_hand_is_not_listed_in_the_undo(wdb):
    _executed, plan_id = _go(wdb, LINES)
    for tick in wdb.scalars(select(RoutineTick)).all():
        wdb.delete(tick)
    wdb.commit()

    head, toast = plan_service.undo(wdb, 42, plan_id)

    assert (head, toast) == ("↩️ Отменено: ⚖️ 72,4 кг.", None)
    assert "Зарядка" not in head
