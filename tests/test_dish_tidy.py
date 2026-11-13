"""🍲 The weekly tidy-up at the wire: who is due, the send and its failures, every tap
on the batch, the on-demand pass and the 🍲 Блюда row.

On SQLite with a fake bot, and on Postgres when ``TWO_PEOPLE_DB_URL`` names a migrated
throwaway database (its tables are emptied). Sunday 15 November 2026 in Tbilisi
(UTC+4). The embeddings query is stubbed empty; a tripwire makes any model client
construction fail. Nothing here reaches a model or the network.
"""

from __future__ import annotations

import ast
import asyncio
import os
import re
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy import text as sql
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from telegram.error import Forbidden, NetworkError

from app.core.config import settings
from app.crud.crud_dish_tidy import crud_dish_tidy
from app.crud.crud_meal_item import crud_meal_item
from app.crud.crud_personal_food import crud_personal_food
from app.crud.crud_subscription import crud_subscription
from app.db.base import Base
from app.models import FavoriteMeal, Meal, PersonalFood, RecordEvent, User
from app.models.dish_tidy import DishTidyBatch, DishTidyItem
from app.services import close_row, dish_tidy, dish_tidy_push, dishes
from app.services import dish_tidy_screen as screen_mod
from app.services import telegram as tg
from app.services.meal_items import SAME_TOAST

ZONE = ZoneInfo("Asia/Tbilisi")
SUNDAY = date(2026, 11, 15)
ROOT = Path(__file__).resolve().parents[1]
PG_URL = os.environ.get("TWO_PEOPLE_DB_URL")
BACKENDS = pytest.mark.parametrize(
    "factory",
    [
        "sqlite",
        pytest.param(
            "postgres",
            marks=pytest.mark.skipif(not PG_URL, reason="TWO_PEOPLE_DB_URL unset"),
        ),
    ],
    indirect=True,
)


def _utc(day, hour, minute=0):
    return datetime.combine(day, time(hour, minute), tzinfo=ZONE).astimezone(UTC)


AT_12 = _utc(SUNDAY, 12, 5)


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
def factory(monkeypatch, request):
    monkeypatch.setattr(
        crud_subscription, "is_active_subscription", lambda _db, _id: True
    )
    monkeypatch.setattr(settings, "DEFAULT_TIMEZONE", "Asia/Tbilisi")
    monkeypatch.setattr(settings, "BOT_ACCESS_MODE", "open", raising=False)
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "1")
    monkeypatch.setattr(crud_personal_food, "close_pairs", lambda *a, **k: {})
    engine = _engine(getattr(request, "param", "sqlite"))
    make = sessionmaker(bind=engine)
    with make() as session:
        session.add(User(id=1, telegram_id=42, settings={}))
        # Person 2 is off unless a test turns them on: one batch per run to count.
        session.add(User(id=2, telegram_id=43, settings={"dish_tidy": False}))
        session.commit()
    monkeypatch.setattr(tg, "SessionLocal", make)
    yield make
    engine.dispose()


@pytest.fixture(autouse=True)
def no_model(monkeypatch):
    """The tripwire: building any model client fails the test."""
    import openai

    def refuse(*args, **kwargs):
        raise AssertionError("a model client was built")

    monkeypatch.setattr(openai.OpenAI, "__init__", refuse)
    monkeypatch.setattr(openai.AsyncOpenAI, "__init__", refuse)


@pytest.fixture
def db(factory):
    with factory() as session:
        yield session


def _user(db, user_id=1) -> User:
    user = db.get(User, user_id)
    db.refresh(user)
    return user


def _dish(db, name, user_id=1, **columns) -> PersonalFood:
    dish = PersonalFood(user_id=user_id, canonical_name=name, **columns)
    db.add(dish)
    db.commit()
    return dish


def _eat(db, dish, day=SUNDAY - timedelta(days=1), user_id=1):
    meal = Meal(
        user_id=user_id, description=dish.canonical_name, meal_time=_utc(day, 9)
    )
    db.add(meal)
    db.flush()
    crud_meal_item.add(
        db, meal_id=meal.id, name=dish.canonical_name, personal_food_id=dish.id
    )
    db.commit()


def _turn_on(db, user_id=2) -> None:
    user = _user(db, user_id)
    user.settings = {}
    db.commit()


def _kitchen(db, user_id=1) -> SimpleNamespace:
    """One of each row: a near-identical pair (merged by itself), a likely pair
    (proposed, ticked) and a dish eaten on three days (⭐)."""
    sauce = _dish(db, "соус томатный", user_id=user_id)
    sauce_too = _dish(db, "томатный соус", user_id=user_id)
    buckwheat = _dish(db, "гречка", user_id=user_id)
    boiled = _dish(db, "гречки", user_id=user_id)  # a word form: proposed ticked
    oats = _dish(db, "овсянка", user_id=user_id)
    for _ in range(3):
        _eat(db, buckwheat, user_id=user_id)
    _eat(db, sauce, user_id=user_id)
    for ago in (1, 2, 3):
        _eat(db, oats, day=SUNDAY - timedelta(days=ago), user_id=user_id)
    return SimpleNamespace(
        sauce=sauce, sauce_too=sauce_too, buckwheat=buckwheat, boiled=boiled, oats=oats
    )


def _bot(**kw):
    return SimpleNamespace(
        send_message=AsyncMock(
            side_effect=kw.get("error"),
            return_value=SimpleNamespace(message_id=kw.get("message_id", 900)),
        ),
        edit_message_text=AsyncMock(side_effect=kw.get("edit_error")),
    )


def _run(db, bot, now=AT_12):
    return asyncio.run(dish_tidy_push.run(db, bot, now))


def _tap(db, data, now=AT_12 + timedelta(minutes=5), telegram_id=42):
    return screen_mod.handle_callback(db, telegram_id, data, now=now, message_id=900)


def _labels(markup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row] if markup else []


def _callbacks(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def _batch(db, user_id=1) -> DishTidyBatch:
    db.expire_all()
    return db.scalars(
        select(DishTidyBatch)
        .where(DishTidyBatch.user_id == user_id)
        .order_by(DishTidyBatch.id.desc())
    ).first()


def _sent_markup(bot):
    return bot.send_message.await_args.kwargs["reply_markup"]


def _apply_data(markup) -> str:
    return next(d for d in _callbacks(markup) if d.startswith("dt:a:"))


def _count(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model))


def _live(db, dish) -> bool:
    db.expire_all()
    row = db.get(PersonalFood, dish.id)
    return row.merged_into_id is None and row.hidden_at is None


def _starred(db, dish) -> bool:
    return (
        db.scalar(
            select(func.count())
            .select_from(FavoriteMeal)
            .where(
                FavoriteMeal.personal_food_id == dish.id,
                FavoriteMeal.removed_at.is_(None),
            )
        )
        > 0
    )


# --- who is due -------------------------------------------------------------------


@pytest.mark.parametrize(
    "when, due",
    [
        (_utc(SUNDAY, 12, 0), True),
        (_utc(SUNDAY, 20, 59), True),
        (_utc(SUNDAY, 11, 59), False),
        (_utc(SUNDAY, 21, 0), False),
        (_utc(SUNDAY - timedelta(days=1), 12, 5), False),  # Saturday
    ],
)
def test_due_on_sunday_from_12_to_20_59_local(db, when, due):
    assert (dish_tidy_push.week_due(db, _user(db), when) is not None) is due


def test_the_hour_is_the_persons_own_zone(db):
    user = _user(db)
    user.settings = {"timezone": "Europe/Moscow"}  # UTC+3: 12:05 Tbilisi is 11:05
    db.commit()
    assert dish_tidy_push.week_due(db, _user(db), AT_12) is None
    assert dish_tidy_push.week_due(db, _user(db), AT_12 + timedelta(hours=1))


def test_the_switch_off_and_a_claimed_week_are_not_due(db):
    user = _user(db)
    user.settings = {screen_mod.SWITCH: False}
    db.commit()
    assert dish_tidy_push.week_due(db, _user(db), AT_12) is None
    # the control: on again, due; then a batch for the week, not due
    user.settings = {screen_mod.SWITCH: True}
    db.commit()
    assert dish_tidy_push.week_due(db, _user(db), AT_12) == SUNDAY
    crud_dish_tidy.claim(db, user_id=1, week=SUNDAY, group_id="g", now=AT_12)
    assert dish_tidy_push.week_due(db, _user(db), AT_12) is None


def test_someone_who_may_not_receive_gets_nothing(db, monkeypatch):
    _turn_on(db)
    monkeypatch.setattr(dish_tidy_push, "may_receive", lambda _db, tid: tid != 42)
    assert dish_tidy_push.week_due(db, _user(db), AT_12) is None
    assert dish_tidy_push.week_due(db, _user(db, 2), AT_12) == SUNDAY


# --- the send ----------------------------------------------------------------------


@BACKENDS
def test_it_merges_first_then_sends_once_silently(db):
    k = _kitchen(db)
    bot = _bot()
    assert _run(db, bot)["sent"] == 1
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["disable_notification"] is True and kwargs["chat_id"] == 42
    text = kwargs["text"]
    assert text.startswith("🍲 <b>Порядок в блюдах</b> · 15 ноября")
    for head in screen_mod.HEADS.values():
        assert head in text
    assert "до 22 ноября" in text
    # the near-identical pair is merged already, the proposals are not
    assert _live(db, k.sauce) != _live(db, k.sauce_too)
    assert _live(db, k.boiled) and not _starred(db, k.oats)
    batch = _batch(db)
    assert batch.state == "sent" and batch.message_id == 900 and batch.week == SUNDAY
    labels = _labels(kwargs["reply_markup"])
    assert labels[0].startswith("✓ 1. ") and "→" in labels[0]
    assert "✅ Применить отмеченное (3)" in labels
    assert screen_mod.OFF_LABEL in labels
    # the same afternoon, nothing more
    bot = _bot()
    assert _run(db, bot, AT_12 + timedelta(minutes=15))["sent"] == 0
    bot.send_message.assert_not_awaited()


@BACKENDS
def test_nothing_found_sends_nothing(db):
    _dish(db, "суп")
    bot = _bot()
    assert _run(db, bot)["empty"] == 1
    bot.send_message.assert_not_awaited()
    assert _batch(db).state == "empty"
    assert _run(db, bot, AT_12 + timedelta(minutes=15))["empty"] == 0


@BACKENDS
def test_blocked_takes_the_merges_back_and_declines_nothing(db):
    k = _kitchen(db)
    assert _run(db, _bot(error=Forbidden("blocked by the user")))["blocked"] == 1
    assert _live(db, k.sauce) and _live(db, k.sauce_too)
    batch = _batch(db)
    assert batch.state == "blocked"
    states = {item.state for item in crud_dish_tidy.items(db, batch.id)}
    assert states == {"rolled_back"}
    assert dish_tidy.declines(db, _user(db), AT_12) == {}


@BACKENDS
def test_may_have_arrived_keeps_the_merges_and_does_not_resend(db):
    k = _kitchen(db)
    error = NetworkError("httpx.ReadTimeout: no answer")
    error.__cause__ = httpx.ReadTimeout("no answer")
    assert _run(db, _bot(error=error))["failed"] == 1
    assert _batch(db).state == "maybe"
    assert _live(db, k.sauce) != _live(db, k.sauce_too)
    bot = _bot()
    _run(db, bot, AT_12 + timedelta(hours=1))
    bot.send_message.assert_not_awaited()


@BACKENDS
def test_certainly_not_sent_is_resent_as_stored_and_given_up_after_a_day(db):
    k = _kitchen(db)
    error = NetworkError("httpx.ConnectError: no route")
    error.__cause__ = httpx.ConnectError("no route")
    assert _run(db, _bot(error=error))["failed"] == 1
    batch = _batch(db)
    assert batch.state == "retry" and batch.tries == 1
    first_items = [i.id for i in crud_dish_tidy.items(db, batch.id)]
    bot = _bot(message_id=901)
    assert _run(db, bot, AT_12 + timedelta(minutes=15))["sent"] == 1
    batch = _batch(db)
    assert batch.state == "sent" and batch.message_id == 901
    assert [i.id for i in crud_dish_tidy.items(db, batch.id)] == first_items
    assert _count(db, DishTidyBatch) == 1
    # A second person whose sends fail for a day: the merges come apart.
    _turn_on(db)
    _kitchen(db, user_id=2)
    _run(db, _bot(error=error), AT_12 + timedelta(minutes=30))
    theirs = _batch(db, 2)
    assert theirs.state == "retry"
    assert _run(db, _bot(error=error), AT_12 + timedelta(hours=25))["failed"] >= 1
    theirs = _batch(db, 2)
    assert theirs.state == "failed"
    sauces = db.scalars(
        select(PersonalFood).where(
            PersonalFood.user_id == 2, PersonalFood.canonical_name.like("%соус%")
        )
    ).all()
    assert all(row.merged_into_id is None for row in sauces)
    assert not _live(db, k.sauce) or not _live(db, k.sauce_too)  # person 1 kept


def test_a_batch_failing_before_the_send_is_given_up_after_a_day(db, monkeypatch):
    """Not the send but the drawing fails: every sweep would fail the same way."""
    k = _kitchen(db)
    error = NetworkError("httpx.ConnectError: no route")
    error.__cause__ = httpx.ConnectError("no route")
    _run(db, _bot(error=error))
    assert _batch(db).state == "retry"

    def broken(*args, **kwargs):
        raise KeyError("label")

    monkeypatch.setattr(screen_mod, "open_view", broken)
    assert _run(db, _bot(), AT_12 + timedelta(hours=1))["failed"] == 1
    assert _batch(db).state == "retry"  # within the day: tried again
    assert not (_live(db, k.sauce) and _live(db, k.sauce_too))
    assert _run(db, _bot(), AT_12 + timedelta(hours=25))["failed"] == 1
    assert _batch(db).state == "failed"
    assert _live(db, k.sauce) and _live(db, k.sauce_too)


def test_a_rollback_that_leaves_a_merge_keeps_the_batch_visible(db):
    """The person changed a merged dish meanwhile: that merge stays, so the batch stays
    open on demand with ↩️ rather than closed where nobody sees it."""
    _kitchen(db)
    error = NetworkError("httpx.ConnectError: no route")
    error.__cause__ = httpx.ConnectError("no route")
    _run(db, _bot(error=error))
    batch = _batch(db)
    same = next(i for i in crud_dish_tidy.items(db, batch.id) if i.kind == "same")
    other = _dish(db, "кетчуп")
    db.get(PersonalFood, same.source_id).merged_into_id = other.id
    db.commit()
    _run(db, _bot(error=error), AT_12 + timedelta(hours=25))
    assert _batch(db).state == "maybe"
    db.expire_all()
    assert db.get(DishTidyItem, same.id).state == "applied"
    screen = _tap(db, "dt:o", now=AT_12 + timedelta(hours=26))
    assert "✅ Применить отмеченное" in " ".join(_labels(screen.markup))


def test_a_batch_settled_from_the_list_is_not_resent_nor_taken_back(db):
    """The send failed, the person opened 🍲 Порядок and applied it before the sweep
    resent the batch it had listed: no second message, nothing they accepted undone."""
    k = _kitchen(db)
    error = NetworkError("httpx.ConnectError: no route")
    error.__cause__ = httpx.ConnectError("no route")
    _run(db, _bot(error=error))
    listed = _batch(db)
    assert listed.state == "retry"
    shown = _tap(db, "dt:o")
    _tap(db, _apply_data(shown.markup))
    assert _batch(db).state == "applied"
    bot = _bot()
    sent = asyncio.run(
        dish_tidy_push.deliver(db, bot, _user(db), listed, AT_12 + timedelta(hours=25))
    )
    assert sent == "skipped"
    bot.send_message.assert_not_awaited()
    dish_tidy_push._give_up(db, _user(db), listed.id, AT_12 + timedelta(hours=25))
    assert _batch(db).state == "applied"
    assert not (_live(db, k.sauce) and _live(db, k.sauce_too))  # the merge stays
    assert _starred(db, k.oats)
    # And the sweep itself leaves it alone (a waiting one is resent: see above).
    assert _run(db, _bot(), AT_12 + timedelta(minutes=15))["sent"] == 0


def test_twenty_rows_of_long_names_fit_one_message(db):
    batch = crud_dish_tidy.claim(db, user_id=1, week=None, group_id="g", now=AT_12)
    for idx in range(20):
        db.add(
            DishTidyItem(
                batch_id=batch.id,
                idx=idx,
                kind="same" if idx < 10 else "merge",
                key=f"m:{idx}:{idx + 100}",
                label=["я" * 120, "ю" * 120],
                fingerprint="0" * 16,
                state="proposed",
            )
        )
    batch.state = "sent"
    batch.expires_at = AT_12 + timedelta(days=7)
    db.commit()
    screen = screen_mod.open_view(db, batch, _user(db), mask=0)
    assert len(screen.text) <= 4096, len(screen.text)
    assert "я" * 120 not in screen.text  # cut, and the control: uncut it would not fit
    assert 20 * 2 * 120 > 4096
    assert _labels(screen.markup)[-1] == screen_mod.BACK  # on demand: the way back


def test_a_batch_stuck_claimed_is_cleared_after_half_an_hour(db):
    _kitchen(db)
    crud_dish_tidy.claim(db, user_id=1, week=SUNDAY, group_id="g", now=AT_12)
    bot = _bot()
    assert _run(db, bot, AT_12 + timedelta(minutes=15))["sent"] == 0  # 15 min: kept
    assert _batch(db).state == "claimed"
    assert _run(db, bot, AT_12 + timedelta(minutes=45))["sent"] == 1
    assert _count(db, DishTidyBatch) == 1 and _batch(db).state == "sent"


def test_a_failed_pass_is_not_tried_again_that_week(db, monkeypatch):
    _kitchen(db)

    def broken(*args, **kwargs):
        raise RuntimeError("bad data")

    monkeypatch.setattr(dish_tidy, "find", broken)
    assert _run(db, _bot())["failed"] == 1
    assert _batch(db).state == "failed"
    assert _run(db, _bot(), AT_12 + timedelta(hours=1))["failed"] == 0


def test_one_persons_bad_data_does_not_cost_the_other(db, monkeypatch):
    _turn_on(db)
    _kitchen(db)
    _kitchen(db, user_id=2)
    real = dish_tidy.find

    def broken_for_one(db_, user, now):
        if user.id == 1:
            raise RuntimeError("bad data")
        return real(db_, user, now)

    monkeypatch.setattr(dish_tidy, "find", broken_for_one)
    counts = _run(db, _bot())
    assert counts["failed"] == 1 and counts["sent"] == 1


# --- the taps ---------------------------------------------------------------------


def _sent(db) -> tuple:
    k = _kitchen(db)
    bot = _bot()
    _run(db, bot)
    return k, _sent_markup(bot), _batch(db)


def _writes(db) -> tuple:
    db.expire_all()
    return (
        _count(db, RecordEvent),
        _count(db, FavoriteMeal),
        tuple(
            (i.state, i.decided_at)
            for i in db.scalars(select(DishTidyItem).order_by(DishTidyItem.id))
        ),
        _batch(db).state,
    )


@BACKENDS
def test_a_tick_redraws_and_writes_nothing(db):
    _k, markup, _batch_row = _sent(db)
    before = _writes(db)
    row = _callbacks(markup)[1]  # «✓ 2. …»: untick the proposal
    screen = _tap(db, row)
    assert _writes(db) == before
    labels = _labels(screen.markup)
    assert not labels[1].startswith("✓") and labels[0].startswith("✓")
    assert "✅ Применить отмеченное (2)" in labels
    # the control: tapping again ticks it back
    again = _callbacks(screen.markup)[1]
    assert _labels(_tap(db, again).markup)[1].startswith("✓")


def test_every_callback_fits_telegrams_64_bytes(db):
    _k, markup, batch = _sent(db)
    batch.id_check = None
    datas = _callbacks(markup) + [f"dt:k:{2**31 - 1}:19:fffff", f"dt:u:{2**31 - 1}"]
    assert all(len(d.encode()) <= 64 for d in datas)
    assert len(("dt:k:" + "9" * 60).encode()) > 64  # the control: the check bites


@BACKENDS
def test_apply_does_what_is_ticked_and_ok_comes_first(db):
    k, markup, batch = _sent(db)
    screen = _tap(db, _apply_data(markup))
    assert not _live(db, k.boiled)  # the proposal, merged
    assert _starred(db, k.oats)
    assert _live(db, k.sauce) != _live(db, k.sauce_too)  # the auto merge, kept
    assert _batch(db).state == "applied"
    rows = screen.markup.inline_keyboard
    assert list(rows[0]) == close_row.ok_row()
    assert rows[1][0].text == screen_mod.UNDO_ALL
    assert screen_mod.APPLIED in screen.text
    states = [i.state for i in crud_dish_tidy.items(db, batch.id)]
    assert states == ["applied", "applied", "applied"]


@BACKENDS
def test_unticked_rows_are_declined_and_an_unticked_auto_merge_comes_apart(db):
    k, markup, batch = _sent(db)
    data = _apply_data(markup)  # dt:a:<bid>:7
    screen = _tap(db, data.rsplit(":", 1)[0] + ":0")
    assert _live(db, k.sauce) and _live(db, k.sauce_too)
    assert _live(db, k.boiled) and not _starred(db, k.oats)
    states = [i.state for i in crud_dish_tidy.items(db, batch.id)]
    assert states == ["declined", "declined", "declined"]
    assert screen.markup is None  # nothing left to take back
    # Next Sunday, nothing of it comes back (declined for 90 days).
    found = dish_tidy.find(db, _user(db), AT_12 + timedelta(days=7))
    assert found.empty


@BACKENDS
def test_a_dish_changed_after_the_batch_is_not_touched_and_named(db):
    k, markup, batch = _sent(db)
    boiled = db.get(PersonalFood, k.boiled.id)
    boiled.canonical_name = "гречка с маслом"
    db.commit()
    screen = _tap(db, _apply_data(markup))
    assert _live(db, k.boiled)
    assert screen_mod.STALE_LINE in screen.text
    states = [i.state for i in crud_dish_tidy.items(db, batch.id)]
    assert states[1] == "stale"


def test_a_week_later_the_proposals_have_expired_and_nothing_is_written(db):
    k, markup, _batch_row = _sent(db)
    before = _writes(db)[:2]
    screen = _tap(db, _apply_data(markup), now=AT_12 + timedelta(days=7, minutes=1))
    head = screen.text.splitlines()[1]
    assert head == screen_mod.EXPIRED + screen_mod.EXPIRED_SAME
    assert screen.toast is None  # the text says why
    assert _writes(db)[:2] == before
    assert _live(db, k.boiled) and _batch(db).state == "expired"
    # the auto merge can still be taken back
    assert screen_mod.UNDO_ALL in _labels(screen.markup)


def test_a_second_apply_says_so_and_writes_nothing(db):
    _k, markup, _batch_row = _sent(db)
    _tap(db, _apply_data(markup))
    before = _writes(db)
    screen = _tap(db, _apply_data(markup))
    assert screen.toast == SAME_TOAST and _writes(db) == before


@BACKENDS
def test_undo_all_takes_everything_back_and_a_second_tap_writes_nothing(db):
    k, markup, batch = _sent(db)
    applied = _tap(db, _apply_data(markup))
    undo = next(d for d in _callbacks(applied.markup) if d.startswith("dt:u:"))
    screen = _tap(db, undo)
    assert screen.text.splitlines()[1] == "↩️ Возвращено: 3 из 3."
    assert _live(db, k.sauce) and _live(db, k.sauce_too) and _live(db, k.boiled)
    assert not _starred(db, k.oats)
    assert _batch(db).state == "undone"
    before = _writes(db)
    again = _tap(db, undo)
    assert again.toast == SAME_TOAST and _writes(db) == before
    assert again.text.splitlines()[1] == screen_mod.UNDONE_HEAD  # not «Применено»
    stale_tick = next(d for d in _callbacks(markup) if d.startswith("dt:k:"))
    assert _tap(db, stale_tick).text.splitlines()[1] == screen_mod.UNDONE_HEAD
    # returned rows count as declined
    assert dish_tidy.find(db, _user(db), AT_12 + timedelta(days=7)).empty


def test_undo_names_what_did_not_come_back(db):
    k, markup, _batch_row = _sent(db)
    applied = _tap(db, _apply_data(markup))
    # The proposal's source moved on to another dish since: it stays there.
    other = _dish(db, "каша гречневая")
    db.get(PersonalFood, k.boiled.id).merged_into_id = other.id
    db.commit()
    undo = next(d for d in _callbacks(applied.markup) if d.startswith("dt:u:"))
    screen = _tap(db, undo)
    head = screen.text.splitlines()[1]
    assert head.startswith("↩️ Возвращено: 2 из 3. Не вернулось: «")
    assert head.endswith("— его меняли после.")


def test_a_new_batch_closes_the_old_one_and_its_undo_still_works(db):
    k, _markup, old = _sent(db)
    _dish(db, "кефир")
    _dish(db, "Кефир")
    bot = _bot(message_id=950)
    _run(db, bot, AT_12 + timedelta(days=7))
    assert _batch(db).id != old.id and _batch(db).state == "sent"
    db.refresh(old)
    assert old.state == "retired"
    edit = bot.edit_message_text.await_args.kwargs
    assert edit["message_id"] == 900 and screen_mod.RETIRED in edit["text"]
    assert screen_mod.UNDO_ALL in _labels(edit["reply_markup"])
    undo = next(d for d in _callbacks(edit["reply_markup"]) if d.startswith("dt:u:"))
    _tap(db, undo, now=AT_12 + timedelta(days=7, minutes=5))
    assert _live(db, k.sauce) and _live(db, k.sauce_too)


def test_the_switch_on_the_message(db):
    _k, markup, _batch_row = _sent(db)
    off = next(d for d in _callbacks(markup) if d.startswith("dt:x:"))
    screen = _tap(db, off)
    assert screen.toast == screen_mod.OFF_TOAST
    assert screen_mod.tidy_on(_user(db)) is False
    assert screen_mod.ON_LABEL in _labels(screen.markup)
    assert dish_tidy_push.week_due(db, _user(db), AT_12 + timedelta(days=7)) is None
    on = next(d for d in _callbacks(screen.markup) if d.startswith("dt:s:"))
    assert _tap(db, on).toast == screen_mod.ON_TOAST
    assert screen_mod.tidy_on(_user(db)) is True


# --- refusals ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "data",
    [
        "dt:",
        "dt:a",
        "dt:a:1",
        "dt:a:x:1",
        "dt:a:1:zz",
        "dt:k:1:0",
        "dt:u:99999999999",
        "dt:a:1:fffff",  # ticks past the batch's rows
        "dt:q:1",
    ],
)
def test_malformed_and_unknown_change_nothing(db, data):
    _sent(db)
    before = _writes(db)
    screen = _tap(db, data)
    assert screen.text and screen_mod.APPLIED not in screen.text
    assert _writes(db) == before


@BACKENDS
def test_another_persons_batch_is_refused_and_unchanged(db):
    _k, markup, batch = _sent(db)
    before = _writes(db)
    for data in _callbacks(markup) + [f"dt:u:{batch.id}"]:
        screen = _tap(db, data, telegram_id=43)
        assert screen_mod.APPLIED not in screen.text
    assert _writes(db) == before
    assert screen_mod.tidy_on(_user(db)) is True
    assert _tap(db, _callbacks(markup)[0], telegram_id=999).text  # nobody


# --- on demand and the 🍲 Блюда row ----------------------------------------------


def test_on_demand_runs_the_pass_and_then_shows_the_same_batch(db):
    _kitchen(db)
    screen = _tap(db, "dt:o")
    batch = _batch(db)
    assert batch.week is None and batch.state == "sent"
    assert "✅ Применить отмеченное (3)" in _labels(screen.markup)
    again = _tap(db, "dt:o")
    assert _count(db, DishTidyBatch) == 1 and again.text == screen.text


def test_on_demand_with_nothing_found_says_so(db):
    _dish(db, "суп")
    _dish(db, "борщ")
    screen = _tap(db, "dt:o")
    assert screen_mod.NOTHING in screen.text
    assert _callbacks(screen.markup) == ["dh:l:0"]


def test_with_nothing_found_a_switched_off_batch_can_be_turned_on(db):
    _dish(db, "суп")
    _dish(db, "борщ")
    user = _user(db)
    user.settings = {"dish_tidy": False}
    db.commit()
    screen = _tap(db, "dt:o")
    assert _callbacks(screen.markup) == ["dt:on", "dh:l:0"]
    assert _labels(screen.markup)[0] == screen_mod.ON_LABEL
    on = _tap(db, "dt:on")
    assert on.toast == screen_mod.ON_TOAST
    assert screen_mod.tidy_on(_user(db))
    assert _callbacks(on.markup) == ["dh:l:0"]


def test_on_demand_shows_a_sunday_batch_that_may_not_have_arrived(db):
    _kitchen(db)
    error = NetworkError("httpx.ReadTimeout: no answer")
    error.__cause__ = httpx.ReadTimeout("no answer")
    _run(db, _bot(error=error))
    screen = _tap(db, "dt:o")
    assert _count(db, DishTidyBatch) == 1
    assert "✅ Применить отмеченное (3)" in _labels(screen.markup)


def test_the_dish_list_has_the_row_from_two_dishes(db):
    _dish(db, "суп")
    one = dishes.handle_callback(db, 42, "dh:l:0")
    assert screen_mod.ENTRY_LABEL not in _labels(one.markup)
    _dish(db, "борщ")
    two = dishes.handle_callback(db, 42, "dh:l:0")
    assert _labels(two.markup)[-1] == screen_mod.ENTRY_LABEL
    assert _callbacks(two.markup)[-1] == "dt:o"
    assert len(two.markup.inline_keyboard[-1]) == 2  # beside «🔍 Найти»


def test_the_entry_is_on_the_first_page_only_and_the_list_keeps_five_rows(db):
    for n in range(9):
        _dish(db, f"блюдо {n}")
    first = dishes.handle_callback(db, 42, "dh:l:0")
    pager = [b.text for b in first.markup.inline_keyboard[-1]]
    assert pager == ["🔍 Найти", screen_mod.ENTRY_LABEL, "▶️"]
    assert len(first.markup.inline_keyboard) <= 5
    middle = dishes.handle_callback(db, 42, "dh:l:1")
    assert screen_mod.ENTRY_LABEL not in _labels(middle.markup)
    pager = [b.text for b in middle.markup.inline_keyboard[-1]]
    assert pager == ["◀️", "🔍 Найти", "▶️"]


# --- wiring and words ------------------------------------------------------------


def test_the_task_the_beat_entry_and_the_handler_are_registered():
    from celery_app.celery_app import app

    entry = app.conf.beat_schedule["dish-tidy"]
    assert entry["task"] == "celery_app.tasks.dish_tidy.tidy_dishes"
    assert entry["options"] == {"expires": 840}
    assert "celery_app.tasks.dish_tidy" in app.conf.include
    source = (ROOT / "app/services/telegram.py").read_text()
    assert 'dish_tidy_callback, pattern=f"^{dish_tidy_screen.CALLBACK_PREFIX}"' in (
        re.sub(r"\s+", " ", source).replace("( ", "(").replace(" )", ")")
    )
    prefixes = []
    for path in (ROOT / "app/services").glob("*.py"):
        prefixes += re.findall(
            r'^CALLBACK_PREFIX\s*=\s*"([^"]+)"', path.read_text(), re.M
        )
    assert prefixes.count("dt:") == 1 and len(prefixes) > 8


def test_the_callback_edits_the_message(db, factory):
    _k, markup, _batch_row = _sent(db)
    query = SimpleNamespace(
        data=_callbacks(markup)[1],
        message=SimpleNamespace(message_id=900, chat_id=42),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    update = SimpleNamespace(
        callback_query=query, effective_user=SimpleNamespace(id=42)
    )
    asyncio.run(tg.dish_tidy_callback(update, SimpleNamespace()))
    query.answer.assert_awaited_once()
    assert query.edit_message_text.await_args.args[0].startswith("🍲 <b>Порядок")


_BANNED = re.compile(
    r"\bнадо\b|нужно|пора\b|хорошо|плохо|осталось|забыл|пропуск|пропущен"
    r"|\bвы\b|\bвам\b|\bваш",
    re.IGNORECASE,
)
_GENDERED = re.compile(r"\b\w+(?:ла|лась|ал[аи])\b", re.IGNORECASE)


def _literals(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and re.search("[а-яё]", node.value, re.IGNORECASE)
    ]


def test_the_words_never_said(db):
    texts = _literals(ROOT / "app/services/dish_tidy_screen.py")
    _k, markup, _batch_row = _sent(db)
    applied = _tap(db, _apply_data(markup))
    texts += [applied.text, *(_labels(markup))]
    assert len(texts) > 20
    assert [t for t in texts if _BANNED.search(t)] == []
    assert [t for t in texts if _GENDERED.search(t)] == []
    assert _BANNED.search("Тебе нужно") and _GENDERED.search("Ты забыла")
