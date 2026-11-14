"""Two tech debts of the 🍲 tidy-up work.

TD-266: one person's failure (and the ``db.rollback()`` that follows, which expires
every row of the session) must not end a push sweep for everyone after them: in the
week, news, morning and reminder sweeps alike.
TD-270: a ⭐ set from words and taken back with the plan's ↩️ is a decline for the
tidy-up, as a merge taken apart is.

On SQLite; no model client and no network (the autouse tripwire is the tidy-up's).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import RecordEvent, User
from app.services import (
    bot_news,
    dish_ops,
    dish_tidy,
    morning,
    morning_push,
    reminder_push,
    reminders,
    week_push,
)
from app.services.bot_news import News

# The tidy-up's own fixtures and helpers (a fixture module-level import is a fixture).
from tests.test_dish_tidy import (  # noqa: F401
    AT_12,
    _apply_data,
    _bot,
    _eat,
    _kitchen,
    _run,
    _sent,
    _tap,
    _user,
    factory,
    no_model,
)


@pytest.fixture
def db(request):
    with request.getfixturevalue("factory")() as session:
        yield session


NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
DUE = object()  # what a check returns for someone due

# --- TD-266 -------------------------------------------------------------------------


@pytest.fixture
def people():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine)() as session:
        for n in (1, 2, 3):
            session.add(User(id=n, telegram_id=40 + n, settings={}))
        session.commit()
        yield session
    engine.dispose()


def _find_user(args):
    return next(a for a in args if isinstance(a, User))


class Sweep:
    """One module's sweep: how to patch its check and its send, and how to run it."""

    def __init__(self, name, module, check, send, run, value):
        self.name, self.module = name, module
        self.check, self.send, self.run, self.value = check, send, run, value


def _run_week(db):
    delivery = SimpleNamespace(push=AsyncMock())
    return asyncio.run(week_push.run(db, SimpleNamespace(), delivery, NOW))


SWEEPS = [
    Sweep(
        "week",
        week_push,
        "_due_week",
        "_send",
        _run_week,
        date(2026, 11, 9),
    ),
    Sweep(
        "news",
        bot_news,
        "_due",
        "_send",
        lambda db: asyncio.run(bot_news.run(db, SimpleNamespace(), NOW)),
        News(id="x", published=date(2026, 11, 1), text="x"),
    ),
    Sweep(
        "morning",
        morning_push,
        None,  # patched on ``morning``, which the sweep calls
        "_send",
        lambda db: asyncio.run(morning_push.run(db, SimpleNamespace(), NOW)),
        date(2026, 11, 14),
    ),
    Sweep(
        "reminders",
        reminder_push,
        None,  # patched on ``reminders``
        "_send",
        lambda db: asyncio.run(reminder_push.run(db, SimpleNamespace(), NOW)),
        "slot",
    ),
]
IDS = [s.name for s in SWEEPS]


def _arrange(sw, monkeypatch, fail_first_of: int):
    """Everyone is due but ``fail_first_of``, whose check raises. A send records who."""
    state = SimpleNamespace(sent=[], raised=0, on_raise=lambda: None)

    def check(*args):
        user = _find_user(args)
        if user.id == fail_first_of:
            state.raised += 1
            state.on_raise()
            raise ValueError("bad row")
        return sw.value

    async def send(*args):
        state.sent.append(_find_user(args).telegram_id)
        return "sent"

    if sw.name == "morning":
        monkeypatch.setattr(morning, "due", check)
    elif sw.name == "reminders":
        monkeypatch.setattr(reminders, "due", check)
    else:
        monkeypatch.setattr(sw.module, sw.check, check)
    monkeypatch.setattr(sw.module, sw.send, send)
    return state


@pytest.mark.parametrize("sw", SWEEPS, ids=IDS)
def test_a_check_failing_midway_does_not_cost_the_rest_their_turn(
    sw, people, monkeypatch
):
    """The rollback after person 2's failed check expires person 1's row (already
    found due); person 1 and person 3 are still sent."""
    state = _arrange(sw, monkeypatch, fail_first_of=2)
    due = sw.module.users_due(people, NOW)
    assert state.raised == 1
    assert [user.id for user, _ in due] == [1, 3]
    state.raised = 0
    counts = sw.run(people)
    assert state.raised == 1
    assert state.sent == [41, 43]
    assert counts["failed"] == 0


@pytest.mark.parametrize("sw", SWEEPS, ids=IDS)
def test_a_reload_that_fails_after_a_rollback_costs_one_person_not_the_sweep(
    sw, people, monkeypatch
):
    """The rollback expires the rows already read; the next read of one fails (the
    connection dropped for a moment). Before, that read sat outside any try and ended
    the sweep: now only that person waits for the next sweep."""
    state = _arrange(sw, monkeypatch, fail_first_of=2)
    held = people.query(User).all()  # strong refs: the rows stay in the session
    assert len(held) == 3
    armed = SimpleNamespace(on=False)
    seen = SimpleNamespace(failures=0)

    @event.listens_for(people, "do_orm_execute")
    def fail_once(execute_state):
        if armed.on and execute_state.is_column_load:
            armed.on = False
            seen.failures += 1
            raise RuntimeError("connection lost")

    state.on_raise = lambda: setattr(armed, "on", True)
    try:
        sw.run(people)  # must not raise
    finally:
        event.remove(people, "do_orm_execute", fail_once)
    assert seen.failures == 1  # the failure was really met
    # One of the two others lost this sweep (whoever's reload met it), the other sent.
    assert len(state.sent) == 1 and set(state.sent) <= {41, 43}


@pytest.mark.parametrize("sw", SWEEPS, ids=IDS)
def test_a_send_that_raises_is_counted_and_the_next_person_is_sent(
    sw, people, monkeypatch
):
    sent = []

    async def send(*args):
        user = _find_user(args)
        if user.id == 1:
            raise RuntimeError("row gone")
        sent.append(user.telegram_id)
        return "sent"

    check = SimpleNamespace(value=sw.value)
    if sw.name == "morning":
        monkeypatch.setattr(morning, "due", lambda *a: check.value)
    elif sw.name == "reminders":
        monkeypatch.setattr(reminders, "due", lambda *a: check.value)
    else:
        monkeypatch.setattr(sw.module, sw.check, lambda *a: check.value)
    monkeypatch.setattr(sw.module, sw.send, send)
    counts = sw.run(people)
    assert sent == [42, 43]
    assert counts["failed"] == 1


def test_the_marks_rewrite_rereads_the_row_under_the_lock(people, monkeypatch):
    """After a failed send's rollback the row is expired: ``_mark`` must be preceded
    by the lock that reads it afresh (as ``_claim`` does), in both modules."""
    for module, name, value in (
        (week_push, "WEEK_PUSH_SENT", "2026-11-09"),
        (bot_news, "NEWS_SEEN", "x"),
    ):
        calls = []
        real_lock = module.lock_user
        monkeypatch.setattr(
            module,
            "lock_user",
            lambda db, user: (calls.append("lock"), real_lock(db, user))[1],
        )
        real_mark = module._mark
        monkeypatch.setattr(
            module,
            "_mark",
            lambda db, user, value_, _calls=calls, _real=real_mark: (
                _calls.append("mark"),
                _real(db, user, value_),
            )[1],
        )
        user = people.get(User, 1)
        key = getattr(module, name) if hasattr(module, name) else None
        assert key is not None

        async def boom(*a, **k):
            raise RuntimeError("send failed")

        # A claim that holds the mark, then a send that fails: the mark is cleared
        # under a lock taken after the rollback.
        if module is week_push:
            news_or_week = date(2026, 11, 9)
            delivery = SimpleNamespace(push=boom)
            asyncio.run(
                module._send(
                    people, SimpleNamespace(), delivery, user, news_or_week, NOW
                )
            )
        else:
            item = News(id="x", published=date(2026, 11, 1), text="x")
            bot = SimpleNamespace(send_message=boom)
            asyncio.run(module._send(people, bot, user, item))
        # claim: lock, mark; failure: lock, mark (the second lock is the new one)
        assert calls == ["lock", "mark", "lock", "mark"], module.__name__
        people.expire_all()
        assert key not in (people.get(User, 1).settings or {})


# --- TD-270 -------------------------------------------------------------------------


def _star_rows(db, user, now=AT_12):
    return [r for r in dish_tidy.find(db, user, now).rows if r.kind == dish_tidy.STAR]


def _starred_from_words_and_undone(db, user, dish, group="plan-group"):
    star = dish_ops.star(db, user, dish, group_id=group)
    db.commit()
    dish_ops.undo(db, user, star, group_id=group)
    db.commit()


def test_a_star_from_words_taken_back_is_not_offered_again(db):
    k = _kitchen(db)
    user = _user(db)
    assert [r.source_id for r in _star_rows(db, user)] == [k.oats.id]  # the control
    _starred_from_words_and_undone(db, user, k.oats)
    assert _star_rows(db, user) == []
    key = dish_tidy.star_key(k.oats.id)
    found = dish_tidy.declines(db, user, AT_12)
    assert found[key].fingerprint is None  # whatever changes, as a merge by hand
    # Within DECLINE_DAYS only.
    later = AT_12 + timedelta(days=dish_tidy.DECLINE_DAYS + 1)
    assert dish_tidy.taken_back_stars(db, user, later) == {}


def test_a_star_taken_back_inside_a_tidy_batch_counts_once(db):
    k, markup, batch = _sent(db)
    _tap(db, _apply_data(markup))
    db.expire_all()
    assert any(i.kind == dish_tidy.STAR and i.state == "applied" for i in _items(db))
    _tap(db, f"dt:u:{batch.id}")
    user = _user(db)
    assert dish_tidy.taken_back_stars(db, user, AT_12) == {}  # not the words' count
    key = dish_tidy.star_key(k.oats.id)
    found = dish_tidy.declines(db, user, AT_12 + timedelta(minutes=10))
    assert found[key].fingerprint is not None  # the batch's own decline, once
    undone = [
        e
        for e in db.scalars(select(RecordEvent).where(RecordEvent.kind == "food_star"))
        if e.undoes_event_id is not None
    ]
    assert len(undone) == 1
    assert _star_rows(db, user, AT_12 + timedelta(minutes=10)) == []


def test_a_star_removed_by_hand_is_as_before(db):
    """⭐ Избранное's removal (an unstar) is not a taken-back star: the dish stays
    ``star_removed``, never offered, and no decline row is made for it."""
    k = _kitchen(db)
    user = _user(db)
    dish_ops.star(db, user, k.oats)
    db.commit()
    dish_ops.unstar(db, user, k.oats, now=AT_12)
    db.commit()
    assert dish_tidy.taken_back_stars(db, user, AT_12) == {}
    assert dish_tidy.star_key(k.oats.id) not in dish_tidy.declines(db, user, AT_12)
    assert _star_rows(db, user) == []
    active, removed = dish_tidy._stars(db, user)
    assert k.oats.id in removed and k.oats.id not in active


def _items(db):
    from app.models.dish_tidy import DishTidyItem

    return db.scalars(select(DishTidyItem)).all()
