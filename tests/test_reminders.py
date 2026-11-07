"""🔔 The evening reminder (stage 6, slice R1a): when it goes out, what it says, the ⭐
tap, the ladder that quiets it, the claim, and its ⚙️ screen.

On an in-memory SQLite DB with a fake bot: no model and no network anywhere. Friday
6 November 2026 in Tbilisi (UTC+4) unless a test says otherwise. The claim and send
tests also run on Postgres when ``TWO_PEOPLE_DB_URL`` names a migrated one, whose
tables they empty: never point it at anything but a throwaway database.
"""

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
from sqlalchemy import create_engine
from sqlalchemy import text as sql
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from telegram.error import BadRequest, Forbidden, NetworkError, TimedOut

from app.core.config import settings
from app.crud.crud_reminder_send import crud_reminder_send
from app.crud.crud_subscription import crud_subscription
from app.db.base import Base
from app.models.body_metric import BodyMetric
from app.models.meal import Meal
from app.models.meal_item import MealItem
from app.models.reminder_send import ReminderSend
from app.models.user import User
from app.services import capture, explain_screen
from app.services import favorites as fv
from app.services import (
    meal_card,
    openai_service,
    reminder_push,
    reminders,
    settings_screen,
)
from app.services import telegram as tg
from app.services.day_card import DAY_ONLY
from app.services.settings_screen import TIMEZONE, WEEK_PUSH

ZONE = ZoneInfo("Asia/Tbilisi")
DAY = date(2026, 11, 6)  # a Friday
SUNDAY = date(2026, 11, 8)
ROOT = Path(__file__).resolve().parents[1]
PG_URL = os.environ.get("TWO_PEOPLE_DB_URL")
# The claim, the send and the ladder's dates on both: the UNIQUE claim, its rollback
# and the aware datetimes behave differently on Postgres (sqlite-gate-hides-postgres).
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


def _utc(day, hour, minute=0, zone=ZONE):
    return datetime.combine(day, time(hour, minute), tzinfo=zone).astimezone(UTC)


AT_21 = _utc(DAY, 21, 5)  # the first sweep inside the default slot


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
    # Not the owner: D1 turns reminders on for everyone, not only for her.
    monkeypatch.setattr(settings, "TELEGRAM_ADMIN_IDS", "1")
    engine = _engine(getattr(request, "param", "sqlite"))
    make = sessionmaker(bind=engine)
    with make() as session:
        session.add(User(id=1, telegram_id=42, settings={}))
        session.commit()
    monkeypatch.setattr(tg, "SessionLocal", make)
    yield make
    engine.dispose()


@pytest.fixture
def db(factory):
    with factory() as session:
        yield session


def _user(db, user_id=1) -> User:
    user = db.get(User, user_id)
    db.refresh(user)
    return user


def _set(db, **values):
    """The reminders' dict as stored, beside the rest of the settings."""
    user = _user(db)
    user.settings = {**(user.settings or {}), reminders.KEY: values}
    db.commit()


def _setting(db, key, value):
    user = _user(db)
    user.settings = {**(user.settings or {}), key: value}
    db.commit()


def _row(db, sent, *, user_id=1, draft=False, deleted=False, meal_time=None):
    meal = Meal(
        user_id=user_id,
        description="суп",
        meal_time=meal_time or sent,
        sent_at=sent,
        calories=300,
        proteins=10,
        fats=5,
        carbohydrates=40,
        is_draft=draft,
        deleted_at=sent if deleted else None,
    )
    db.add(meal)
    db.commit()
    return meal


def _usual(db):
    """A record that morning: the person keeps a diary, nothing fresh by 21:00."""
    return _row(db, _utc(DAY, 10))


def _reason(db, now=AT_21, slot=reminders.Slot("21:00", reminders.EVENING)):
    return reminders.skip_reason(db, _user(db), slot, now)


def _send(db, day, *, answer=None, status="sent", slot="21:00", user_id=1):
    """A reminder of an earlier day, as the sweep left it."""
    row = crud_reminder_send.claim(
        db,
        user_id=user_id,
        local_day=day,
        slot=slot,
        kind=reminders.EVENING if slot >= "18:00" else "meal",
        chat_id=42 if user_id == 1 else 43,
        now=_utc(day, int(slot[:2]), int(slot[3:])),
    )
    row.status = status
    row.answer = answer
    row.message_id = 500 + row.id
    db.commit()
    return row


def _send_now(db, now):
    """A reminder that went out a minute before ``now``, on its day."""
    return crud_reminder_send.claim(
        db,
        user_id=1,
        local_day=now.astimezone(ZONE).date(),
        slot="21:00",
        kind=reminders.EVENING,
        chat_id=42,
        now=now - timedelta(minutes=1),
    )


def _reentry_send(db, day, tag, *, answer=None):
    """A re-entry message of an earlier day, as the sweep left it (D6)."""
    row = crud_reminder_send.claim(
        db,
        user_id=1,
        local_day=day,
        slot=tag,
        kind=reminders.REENTRY,
        chat_id=42,
        now=_utc(day, 21, 5),
    )
    row.status = "sent"
    row.answer = answer
    row.message_id = 500 + row.id
    db.commit()
    return row


def _bot(**kw):
    return SimpleNamespace(
        send_message=AsyncMock(
            side_effect=kw.get("error"),
            return_value=SimpleNamespace(message_id=kw.get("message_id", 900)),
        ),
        delete_message=AsyncMock(side_effect=kw.get("delete_error")),
        edit_message_reply_markup=AsyncMock(),
    )


def _run(db, bot, now=AT_21):
    return asyncio.run(reminder_push.run(db, bot, now))


def _never_made():
    error = NetworkError("httpx.ConnectError: no route")
    error.__cause__ = httpx.ConnectError("no route")
    return error


def _labels(markup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]


def _callbacks(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def _fav(db, name, user_id=1):
    """A ⭐ saved long ago, so its own meal counts in no ranking."""
    source = _row(db, _utc(DAY - timedelta(days=100), 12), user_id=user_id)
    source.description = name
    db.commit()
    fav, _ = fv.save_from_meal(db, source, name)
    return fav


def _eaten(db, fav, day, hour=13):
    """A meal on ``day`` holding the ⭐'s dish."""
    meal = _row(db, _utc(day, hour))
    db.add(
        MealItem(
            meal_id=meal.id,
            origin_meal_id=meal.id,
            personal_food_id=fav.personal_food_id,
            name=fav.name,
        )
    )
    db.commit()
    return meal


def _tap(db, data, now, message_id=None):
    return reminders.handle_callback(db, 42, data, now=now, message_id=message_id)


def _favorite_meals(db):
    return (
        db.query(Meal)
        .filter(Meal.resolution_source == fv.FAVORITE_SOURCE)
        .order_by(Meal.id)
        .all()
    )


# --- when it is due (§5.3) ---------------------------------------------------------


def test_due_with_nothing_in_the_way(db):
    _usual(db)
    assert reminders.due(db, _user(db), AT_21) == reminders.Slot("21:00", "evening")


def test_absent_config_is_on_at_21_silent_for_everyone(db):
    """D1: nobody is asked; an invited person with nothing stored gets the evening."""
    _usual(db)
    user = _user(db)
    assert reminders.KEY not in user.settings
    assert 42 not in settings.admin_ids
    cfg = reminders.config(user)
    assert (cfg.mode, cfg.evening, cfg.sound) == ("evening", "21:00", False)
    assert reminders.due(db, user, AT_21) is not None


@pytest.mark.parametrize(
    "hour, minute, reason",
    [(20, 59, "not its hour"), (21, 0, None), (21, 59, None), (22, 0, "not its hour")],
)
def test_the_slot_goes_within_its_hour(db, hour, minute, reason):
    _usual(db)
    assert _reason(db, _utc(DAY, hour, minute)) == reason


def test_a_chosen_hour_moves_the_slot(db):
    _usual(db)
    _set(db, mode="evening", evening="22:00")
    assert reminders.due(db, _user(db), AT_21) is None
    assert reminders.due(db, _user(db), _utc(DAY, 22, 5)).at == "22:00"


def test_off_sends_nothing(db):
    _usual(db)
    _set(db, mode="off")
    assert _reason(db) == "off"


def test_turned_on_after_the_slot_waits_for_tomorrow(db):
    _usual(db)
    _set(db, since=_utc(DAY, 21, 2).isoformat())
    assert _reason(db) == "turned on after the slot"
    _set(db, since=_utc(DAY, 20, 50).isoformat())
    assert _reason(db) is None


def test_off_day_skips_that_day_only(db):
    _usual(db)
    _set(db, off_day=DAY.isoformat())
    assert _reason(db) == "off today"
    _set(db, off_day=(DAY - timedelta(days=1)).isoformat())
    assert _reason(db) is None


def test_sunday_with_the_week_summary_gives_way_to_it(db):
    _row(db, _utc(SUNDAY, 10))
    sunday = _utc(SUNDAY, 21, 5)
    assert _reason(db, sunday) == "the week's summary instead"
    _setting(db, WEEK_PUSH, False)
    assert _reason(db, sunday) is None


def test_may_not_receive(db, monkeypatch):
    _usual(db)
    monkeypatch.setattr(
        crud_subscription, "is_active_subscription", lambda _db, _id: False
    )
    assert _reason(db) == "may not receive"


def test_no_live_record_ever_sends_nothing(db):
    _row(db, _utc(DAY, 9), draft=True)
    _row(db, _utc(DAY, 10), deleted=True)
    assert _reason(db) == "no record ever"
    _row(db, _utc(DAY - timedelta(days=2), 10))
    assert _reason(db) is None


@pytest.mark.parametrize("days, reason", [(8, "no record for a week"), (6, None)])
def test_a_week_without_records_stops_them(db, days, reason):
    _row(db, _utc(DAY - timedelta(days=days), 10))
    assert _reason(db) == reason


def test_any_row_counts_as_a_recent_record(db):
    _row(db, _utc(DAY - timedelta(days=9), 10))
    _row(db, _utc(DAY - timedelta(days=1), 10), draft=True)
    assert _reason(db) is None


def test_a_claimed_slot_is_not_sent_again(db):
    _usual(db)
    _send(db, DAY)
    assert _reason(db) == "already claimed"


def test_three_a_day_at_most(db):
    _usual(db)
    for slot in ("09:00", "13:00", "17:00"):
        _send(db, DAY, slot=slot, answer="ok")
    assert _reason(db) == "three today"


def test_one_evening_a_day_even_after_the_hour_moved(db):
    _usual(db)
    _send(db, DAY, slot="20:00", answer="ok")
    assert _reason(db) == "the evening one went out"


@pytest.mark.parametrize(
    "hour, minute, kind, reason",
    [
        (18, 30, "live", "fresh record"),
        (19, 0, "draft", "fresh record"),
        (20, 0, "deleted", "fresh record"),
        (17, 55, "live", None),
    ],
)
def test_a_fresh_record_within_three_hours_skips(db, hour, minute, kind, reason):
    _usual(db)
    _row(db, _utc(DAY, hour, minute), draft=kind == "draft", deleted=kind == "deleted")
    assert _reason(db) == reason


def test_records_through_the_day_do_not_stop_it(db):
    """No rule on how many meals the day holds."""
    for hour, minute in ((8, 40), (11, 0), (13, 10)):
        _row(db, _utc(DAY, hour, minute))
    assert _reason(db) is None


def test_an_open_question_waits_for_the_next_sweep(db):
    _usual(db)
    _setting(db, meal_card.ASK, {"kind": "time", "meal_id": 1})
    assert _reason(db) == "a question is open"


def test_kolkata_is_due_at_its_own_half_hour(db):
    kolkata = ZoneInfo("Asia/Kolkata")
    _setting(db, TIMEZONE, "Asia/Kolkata")
    _row(db, _utc(DAY, 10, zone=kolkata))
    assert _utc(DAY, 21, zone=kolkata) == datetime(2026, 11, 6, 15, 30, tzinfo=UTC)
    user = _user(db)
    assert reminders.due(db, user, datetime(2026, 11, 6, 15, 15, tzinfo=UTC)) is None
    assert reminders.due(db, user, datetime(2026, 11, 6, 15, 30, tzinfo=UTC))


# --- unanswered → silent (§5.6) -----------------------------------------------------


def _days_before(*offsets):
    return [DAY - timedelta(days=n) for n in offsets]


def _unanswered(db, n):
    """``n`` evenings in a row before DAY, each left without an answer."""
    for day in _days_before(*range(n, 0, -1)):
        _send(db, day)


def test_three_unanswered_make_them_rarer(db):
    """D7: from the third in a row, a regular one only every third evening."""
    _usual(db)
    _unanswered(db, 3)
    assert _reason(db) == "rarer"
    # Dated by the 3rd unanswered one, not by the sweep that found it (B2).
    on = (DAY - timedelta(days=1)).isoformat()
    assert reminders.config(_user(db)).quiet == {"level": "sparse", "on": on}
    # The last regular one went out on DAY-1: DAY is rarer, DAY+2 goes.
    _setting(db, WEEK_PUSH, False)  # DAY+2 is a Sunday
    assert _reason(db, _utc(DAY + timedelta(days=1), 21, 5)) == "rarer"
    assert _reason(db, _utc(DAY + timedelta(days=2), 21, 5)) is None


def test_rarer_counts_from_the_last_regular_send(db):
    """D7: «the last regular send + 3», whatever came of it; a re-entry is not one."""
    _usual(db)
    _set(db, quiet={"level": "sparse", "on": (DAY - timedelta(days=9)).isoformat()})
    for n in (9, 6, 3):  # sparse already: one every third evening
        _send(db, DAY - timedelta(days=n))
    assert _reason(db) is None  # DAY-3 + 3
    _reentry_send(db, DAY - timedelta(days=1), reminders.REENTRY_FIRST)
    assert _reason(db) is None  # the re-entry neither counts nor spaces
    _send(db, DAY - timedelta(days=2), answer="ok")  # neutral, yet a regular send
    assert _reason(db) == "rarer"


def test_six_unanswered_stop_them(db):
    _usual(db)
    _unanswered(db, 6)
    assert _reason(db) == "quiet"
    on = (DAY - timedelta(days=1)).isoformat()
    assert reminders.config(_user(db)).quiet == {"level": "off", "on": on}


def test_five_unanswered_while_rarer_still_send_on_the_third_day(db):
    _usual(db)
    _set(db, quiet={"level": "sparse", "on": (DAY - timedelta(days=9)).isoformat()})
    for n in (12, 11, 10, 6, 3):
        _send(db, DAY - timedelta(days=n))
    assert _reason(db) is None
    assert reminders.config(_user(db)).quiet["level"] == "sparse"


def test_an_answer_ends_rarer(db):
    """D7: an answered send breaks the streak: daily again at the next ladder write."""
    _usual(db)
    _set(db, quiet={"level": "sparse", "on": (DAY - timedelta(days=6)).isoformat()})
    for n in (8, 7, 6):
        _send(db, DAY - timedelta(days=n))
    _send(db, DAY - timedelta(days=3), answer="words")
    _send(db, DAY - timedelta(days=1))  # unanswered, after the answer
    assert _reason(db) is None
    assert reminders.config(_user(db)).quiet is None
    assert _reason(db, _utc(DAY + timedelta(days=1), 21, 5)) is None  # daily


def test_after_a_return_one_unanswered_stops_them_not_rarer(db):
    """D7: fragile, streak ≥ 1 → off, not sparse."""
    _usual(db)
    _set(db, fragile=True, count_from=_utc(DAY - timedelta(days=2), 0).isoformat())
    _send(db, DAY - timedelta(days=1))
    assert _reason(db) == "quiet"
    assert reminders.config(_user(db)).quiet["level"] == "off"


def test_two_unanswered_still_send(db):
    _usual(db)
    for day in _days_before(2, 1):
        _send(db, day)
    assert _reason(db) is None


@pytest.mark.parametrize("neutral", [{"answer": "ok"}, {"answer": "off"}])
def test_ok_and_off_are_neutral(db, neutral):
    _usual(db)
    for day in _days_before(3, 2, 1):
        _send(db, day, **neutral)
    assert _reason(db) is None
    # Stepped over, not a break: the unanswered ones around them still add up.
    db.query(ReminderSend).delete()
    db.commit()
    for day in _days_before(5, 3, 1):
        _send(db, day)
    for day in _days_before(4, 2):
        _send(db, day, **neutral)
    assert _reason(db) == "rarer"


def test_maybe_is_neutral_and_blocked_is_unanswered(db):
    _usual(db)
    for day in _days_before(3, 2, 1):
        _send(db, day, status="maybe")
    assert _reason(db) is None
    _set(db)
    db.query(ReminderSend).delete()
    db.commit()
    for day in _days_before(3, 2, 1):
        _send(db, day, status="blocked")
    assert _reason(db) == "rarer"


def test_an_answer_breaks_the_streak(db):
    _usual(db)
    _send(db, DAY - timedelta(days=4))
    _send(db, DAY - timedelta(days=3), answer="fav")
    for day in _days_before(2, 1):
        _send(db, day)
    assert _reason(db) is None


def test_the_day_still_open_does_not_count(db):
    """Today's own send can still be answered: only the days before it count."""
    _usual(db)
    for day in _days_before(2, 1):
        _send(db, day)
    _send(db, DAY, slot="09:00")
    assert _reason(db) is None


def test_a_breakfast_the_next_morning_neither_answers_nor_resumes(db):
    _set(db, quiet={"level": "off", "on": (DAY - timedelta(days=3)).isoformat()})
    for day in _days_before(2, 1, 0):
        _row(db, _utc(day, 8))
    assert _reason(db) == "quiet"


@BACKENDS
def test_resume_needs_a_gap_day_then_two_record_days(db):
    paused = DAY - timedelta(days=3)
    _set(db, quiet={"level": "off", "on": paused.isoformat()})
    # DAY-2: no row at all. DAY-1 and DAY: records.
    _row(db, _utc(DAY - timedelta(days=1), 8))
    assert _reason(db, _utc(DAY - timedelta(days=1), 21, 5)) == "quiet"  # the 1st
    _row(db, _utc(DAY, 8))
    assert _reason(db) is None  # the 2nd
    cfg = reminders.config(_user(db))
    assert cfg.quiet is None and cfg.fragile is True
    assert cfg.count_from == AT_21


def test_weights_do_not_resume(db):
    _set(db, quiet={"level": "off", "on": (DAY - timedelta(days=3)).isoformat()})
    for day in _days_before(1, 0):
        db.add(BodyMetric(user_id=1, timestamp=_utc(day, 8), weight=60))
    _row(db, _utc(DAY, 8))  # one record day only
    db.commit()
    assert _reason(db) == "quiet"


@BACKENDS
def test_after_a_return_one_unanswered_quiets_again(db):
    _usual(db)
    _set(db, fragile=True, count_from=_utc(DAY - timedelta(days=2), 0).isoformat())
    _send(db, DAY - timedelta(days=3))  # before the return: not counted
    _send(db, DAY - timedelta(days=1))
    assert _reason(db) == "quiet"


def test_an_answer_clears_fragile(db):
    _usual(db)
    _set(db, fragile=True, count_from=_utc(DAY - timedelta(days=2), 0).isoformat())
    _send(db, DAY - timedelta(days=1), answer="fav")
    assert _reason(db) is None
    assert reminders.config(_user(db)).fragile is False


def test_a_tap_in_settings_resumes(db):
    _usual(db)
    _unanswered(db, 6)
    assert _reason(db) == "quiet"
    screen = _tap(db, "rm:m:evening", _utc(DAY, 20))
    assert "✓ Напоминания приходят" in screen.text
    cfg = reminders.config(_user(db))
    assert cfg.quiet is None and cfg.fragile is False
    assert _reason(db) is None


def test_a_tap_in_settings_ends_rarer(db):
    """D7: ✓ 🕘 Вечером in ⚙️ clears sparse too, and that evening goes."""
    _usual(db)
    _unanswered(db, 3)
    assert _reason(db) == "rarer"
    screen = _tap(db, "rm:m:evening", _utc(DAY, 20))
    assert "✓ Напоминания приходят" in screen.text
    assert reminders.SPARSE_LINE not in screen.text
    cfg = reminders.config(_user(db))
    assert cfg.quiet is None and cfg.fragile is False
    assert _reason(db) is None


def test_the_purge_keeps_the_pause(db, monkeypatch):
    from celery_app.tasks import periodic

    quiet = {"level": "off", "on": "2026-08-01"}
    _set(db, quiet=quiet)
    old = _send(db, date(2026, 7, 1))
    kept = _send(db, datetime.now(UTC).date() - timedelta(days=59))
    old_id, kept_id = old.id, kept.id
    monkeypatch.setattr(periodic, "SessionLocal", lambda: db)
    assert periodic.purge_reminder_sends() == 1
    assert db.get(ReminderSend, old_id) is None
    assert db.get(ReminderSend, kept_id) is not None
    assert reminders.config(_user(db)).quiet == quiet


# --- re-entry after a lapse (D6) ----------------------------------------------------

RE7 = reminders.Slot("21:00", reminders.REENTRY, None, reminders.REENTRY_FIRST)
RE14 = reminders.Slot("21:00", reminders.REENTRY, None, reminders.REENTRY_SECOND)


def _last_record(db, gap, day=DAY):
    """The last meal row ``gap`` local days before ``day``."""
    return _row(db, _utc(day - timedelta(days=gap), 10))


@pytest.mark.parametrize(
    "gap, slot",
    [(6, None), (7, RE7), (13, RE7), (14, RE14), (29, RE14), (30, None)],
)
def test_reentry_is_due_at_7_and_14_days_and_never_from_30(db, gap, slot):
    _last_record(db, gap)
    due = reminders.due(db, _user(db), AT_21)
    if slot is None:
        # Gap 6 is an ordinary evening; gap 30 gets nothing at all.
        assert due == (reminders.Slot("21:00", "evening") if gap == 6 else None)
    else:
        assert due == slot


def test_reentry_names_its_own_slot_short_enough_for_the_column():
    """reminder_sends.slot is String(5) on Postgres: «reentry7» would not fit."""
    assert RE7.key == "re7" and RE14.key == "re14"
    assert all(len(tag) <= 5 for tag in reminders.REENTRY_DAYS)
    assert reminders.Slot("21:00", reminders.EVENING).key == "21:00"


def test_reentry_goes_within_the_evening_hour_only(db):
    _last_record(db, 8)
    assert reminders.due(db, _user(db), _utc(DAY, 20, 59)) is None
    assert reminders.due(db, _user(db), _utc(DAY, 22, 0)) is None
    _set(db, evening="22:00")
    assert reminders.due(db, _user(db), _utc(DAY, 22, 5)) == reminders.Slot(
        "22:00", reminders.REENTRY, None, reminders.REENTRY_FIRST
    )


def test_each_reentry_goes_once_a_lapse(db):
    _last_record(db, 8)
    _reentry_send(db, DAY - timedelta(days=1), reminders.REENTRY_FIRST)
    slot, reason = reminders.reentry_check(db, _user(db), AT_21)
    assert (slot, reason) == (None, "no re-entry due")
    later = DAY + timedelta(days=6)  # gap 14
    assert reminders.due(db, _user(db), _utc(later, 21, 5)) == RE14
    _reentry_send(db, later, reminders.REENTRY_SECOND)
    for n in range(7, 22):  # gap 15 .. 29
        assert (
            reminders.due(db, _user(db), _utc(later + timedelta(days=n - 6), 21, 5))
            is None
        )


def test_a_new_record_starts_a_new_lapse(db):
    _last_record(db, 30)
    _reentry_send(db, DAY - timedelta(days=23), reminders.REENTRY_FIRST)
    _reentry_send(db, DAY - timedelta(days=16), reminders.REENTRY_SECOND)
    _last_record(db, 7)  # a record, after both
    assert reminders.due(db, _user(db), AT_21) == RE7
    later = DAY + timedelta(days=7)  # gap 14
    assert reminders.due(db, _user(db), _utc(later, 21, 5)) == RE14


def test_any_row_is_the_last_record_drafts_and_deleted_too(db):
    _last_record(db, 20)
    _row(db, _utc(DAY - timedelta(days=8), 10), draft=True)
    assert reminders.due(db, _user(db), AT_21) == RE7
    _row(db, _utc(DAY - timedelta(days=3), 10), deleted=True)
    assert reminders.due(db, _user(db), AT_21) == reminders.Slot("21:00", "evening")


@pytest.mark.parametrize("level", ["off", "sparse"])
def test_reentry_goes_whatever_the_ladder_says(db, level):
    _last_record(db, 7)
    _set(db, quiet={"level": level, "on": (DAY - timedelta(days=8)).isoformat()})
    _send(db, DAY - timedelta(days=8))
    assert reminders.due(db, _user(db), AT_21) == RE7


def test_after_x_there_is_no_reentry(db):
    _last_record(db, 7)
    _set(db, mode="off")
    assert reminders.reentry_check(db, _user(db), AT_21) == (None, "off")
    assert reminders.due(db, _user(db), AT_21) is None


def test_no_reentry_for_who_may_not_receive_or_never_recorded(db, monkeypatch):
    _row(db, _utc(DAY - timedelta(days=8), 10), draft=True)
    assert reminders.reentry_check(db, _user(db), AT_21) == (None, "no record ever")
    _last_record(db, 8)
    monkeypatch.setattr(
        crud_subscription, "is_active_subscription", lambda _db, _id: False
    )
    assert reminders.reentry_check(db, _user(db), AT_21) == (None, "may not receive")


def test_reentry_keeps_the_other_day_checks(db):
    _last_record(db, 8)
    _set(db, off_day=DAY.isoformat())
    assert reminders.reentry_check(db, _user(db), AT_21) == (None, "off today")
    _set(db, since=_utc(DAY, 21, 2).isoformat())
    assert reminders.reentry_check(db, _user(db), AT_21) == (
        None,
        "turned on after the slot",
    )
    _set(db)
    _setting(db, meal_card.ASK, {"kind": "time", "meal_id": 1})
    assert reminders.reentry_check(db, _user(db), AT_21) == (None, "a question is open")


def test_on_a_week_sunday_reentry_moves_to_monday(db):
    monday = SUNDAY + timedelta(days=1)
    _last_record(db, 7, day=SUNDAY)
    sunday = _utc(SUNDAY, 21, 5)
    assert reminders.reentry_check(db, _user(db), sunday) == (
        None,
        "the week's summary instead",
    )
    assert reminders.due(db, _user(db), sunday) is None
    assert reminders.due(db, _user(db), _utc(monday, 21, 5)) == RE7
    # With the week card off, Sunday is an evening like any.
    _setting(db, WEEK_PUSH, False)
    assert reminders.due(db, _user(db), sunday) == RE7


def test_a_first_held_back_on_the_13th_day_goes_on_the_14th(db):
    """The Sunday shift wins over the window: #1 on Monday, #2 the evening after."""
    monday = SUNDAY + timedelta(days=1)
    _last_record(db, 13, day=SUNDAY)
    assert reminders.due(db, _user(db), _utc(SUNDAY, 21, 5)) is None
    assert reminders.due(db, _user(db), _utc(monday, 21, 5)) == RE7
    _reentry_send(db, monday, reminders.REENTRY_FIRST)
    tuesday = monday + timedelta(days=1)
    assert reminders.due(db, _user(db), _utc(tuesday, 21, 5)) == RE14


def test_the_day_of_a_reentry_gets_no_regular_one(db):
    _last_record(db, 7)
    _reentry_send(db, DAY, reminders.REENTRY_FIRST)
    assert _reason(db, _utc(DAY, 21, 20)) == "a re-entry went out today"
    assert reminders.due(db, _user(db), _utc(DAY, 21, 20)) is None


def test_a_regular_one_today_holds_the_reentry(db):
    _last_record(db, 7)
    _send(db, DAY, slot="20:00", answer="ok")
    assert reminders.reentry_check(db, _user(db), AT_21) == (None, "one went out today")


def test_reentry_is_neutral_for_the_ladder(db):
    _usual(db)
    _send(db, DAY - timedelta(days=3))
    _reentry_send(db, DAY - timedelta(days=2), reminders.REENTRY_FIRST)
    _send(db, DAY - timedelta(days=1))
    assert _reason(db) is None  # two in a row, the re-entry stepped over
    reentry = db.query(ReminderSend).filter_by(kind=reminders.REENTRY).one()
    assert reminders.outcome(reentry) == reminders.NEUTRAL
    reentry.answer = "words"  # whatever is stored on it
    db.commit()
    assert reminders.outcome(reentry) == reminders.NEUTRAL


# --- the claim and the send (§5.4) -------------------------------------------------


@BACKENDS
def test_two_sweeps_send_once(db):
    _usual(db)
    bot = _bot()
    assert _run(db, bot)["sent"] == 1
    assert _run(db, bot, AT_21 + timedelta(minutes=15))["sent"] == 0
    bot.send_message.assert_awaited_once()
    send = db.query(ReminderSend).one()
    assert (send.status, send.message_id, send.local_day) == ("sent", 900, DAY)
    assert reminders.config(_user(db)).noted == send.id


@BACKENDS
def test_a_lost_race_is_skipped(db):
    _usual(db)
    user, slot = _user(db), reminders.Slot("21:00", "evening")
    bot = _bot()
    assert asyncio.run(reminder_push._send(db, bot, user, slot, AT_21)) == "sent"
    assert asyncio.run(reminder_push._send(db, bot, user, slot, AT_21)) == "skipped"
    bot.send_message.assert_awaited_once()


@BACKENDS
def test_silent_by_default_and_with_sound_when_chosen(db):
    """D3."""
    _usual(db)
    bot = _bot()
    _run(db, bot)
    assert bot.send_message.await_args.kwargs["disable_notification"] is True
    assert bot.send_message.await_args.kwargs["parse_mode"] == "HTML"
    _row(db, _utc(DAY + timedelta(days=1), 10))
    _set(db, sound=True, noted=1)
    _run(db, bot, _utc(DAY + timedelta(days=1), 21, 5))
    assert bot.send_message.await_args.kwargs["disable_notification"] is False


@BACKENDS
def test_blocked_is_kept_as_blocked(db):
    _usual(db)
    counts = _run(db, _bot(error=Forbidden("bot was blocked by the user")))
    assert counts["blocked"] == 1
    assert db.query(ReminderSend).one().status == "blocked"


@BACKENDS
def test_maybe_arrived_is_not_sent_again(db):
    _usual(db)
    bot = _bot(error=TimedOut())
    assert _run(db, bot)["failed"] == 1
    send = db.query(ReminderSend).one()
    assert send.status == "maybe"
    assert reminders.config(_user(db)).noted is None
    _run(db, bot, AT_21 + timedelta(minutes=15))
    bot.send_message.assert_awaited_once()


@BACKENDS
def test_never_made_is_retried_by_the_next_sweep(db):
    _usual(db)
    bot = _bot(error=_never_made())
    assert _run(db, bot)["failed"] == 1
    assert db.query(ReminderSend).count() == 0
    bot.send_message.side_effect = None
    assert _run(db, bot, AT_21 + timedelta(minutes=15))["sent"] == 1
    assert db.query(ReminderSend).one().status == "sent"


def test_one_persons_bad_data_does_not_stop_the_others(db, monkeypatch):
    _usual(db)
    db.add(User(id=2, telegram_id=43, settings={}))
    db.commit()
    _row(db, _utc(DAY, 10), user_id=2)
    real = reminders.due

    def due(db_, user, now):
        if user.id == 1:
            raise ValueError("bad row")
        return real(db_, user, now)

    monkeypatch.setattr(reminders, "due", due)
    assert [u.id for u, _ in reminder_push.users_due(db, AT_21)] == [2]


@BACKENDS
def test_one_persons_failed_claim_does_not_stop_the_sweep(db, monkeypatch):
    from sqlalchemy.exc import OperationalError

    _usual(db)
    db.add(User(id=2, telegram_id=43, settings={}))
    db.commit()
    _row(db, _utc(DAY, 10), user_id=2)
    real = crud_reminder_send.claim

    def claim(db_, **kw):
        if kw["user_id"] == 1:
            raise OperationalError("INSERT", {}, Exception("connection reset"))
        return real(db_, **kw)

    monkeypatch.setattr(crud_reminder_send, "claim", claim)
    bot = _bot()
    counts = _run(db, bot)
    assert (counts["failed"], counts["sent"]) == (1, 1)
    assert bot.send_message.await_args.kwargs["chat_id"] == 43
    assert db.query(ReminderSend).one().user_id == 2


# --- the previous one leaves (R4) ---------------------------------------------------


def _next_evening(db, bot):
    tomorrow = DAY + timedelta(days=1)
    _row(db, _utc(tomorrow, 10))
    return _run(db, bot, _utc(tomorrow, 21, 5))


@BACKENDS
def test_the_next_send_deletes_the_previous_one(db):
    _usual(db)
    bot = _bot()
    _run(db, bot)
    _next_evening(db, _bot(message_id=901))
    first = db.query(ReminderSend).order_by(ReminderSend.id).first()
    assert first.closed_at is not None


@BACKENDS
def test_ok_strips_now_and_the_next_send_deletes_it(db):
    _usual(db)
    _run(db, _bot())
    send = db.query(ReminderSend).one()
    screen = _tap(db, f"rm:k:{send.id}", AT_21 + timedelta(minutes=5))
    assert (screen.text, screen.view, screen.toast) == ("", reminders.STRIP, "Понятно")
    db.refresh(send)
    assert send.answer == "ok"
    assert send.closed_at is not None  # dismissed: R1b's window no longer binds
    bot = _bot(message_id=901)
    _next_evening(db, bot)
    bot.delete_message.assert_awaited_once_with(42, 900)
    bot.edit_message_reply_markup.assert_not_awaited()


@BACKENDS
@pytest.mark.parametrize("then_nothing", [False, True])
def test_a_reminder_with_a_logged_dish_only_loses_its_buttons(db, then_nothing):
    """A «ничего» after the ⭐ (R16, FAV_NONE) still keeps the message (R4)."""
    _usual(db)
    fav = _fav(db, "Суп")
    _run(db, _bot())
    send = db.query(ReminderSend).one()
    _tap(db, f"rm:f:{send.id}:{fav.id}", AT_21 + timedelta(minutes=5))
    if then_nothing:
        send.answer = reminders.FAV_NONE
        db.commit()
    bot = _bot(message_id=901)
    _next_evening(db, bot)
    bot.delete_message.assert_not_awaited()
    bot.edit_message_reply_markup.assert_awaited_once_with(
        chat_id=42, message_id=900, reply_markup=None
    )


@BACKENDS
def test_too_old_to_delete_loses_its_buttons_and_a_failed_delete_too(db):
    _send(db, DAY - timedelta(days=3))  # unanswered, three days old
    _usual(db)
    bot = _bot()
    _run(db, bot)
    bot.delete_message.assert_not_awaited()
    bot.edit_message_reply_markup.assert_awaited_once()
    bot = _bot(message_id=902, delete_error=BadRequest("message can't be deleted"))
    _next_evening(db, bot)
    bot.delete_message.assert_awaited_once()
    bot.edit_message_reply_markup.assert_awaited_once()


@BACKENDS
def test_a_message_behind_a_killed_claim_still_loses_its_buttons(db):
    """R4: a claim with no message (a killed worker, a maybe, a block) is walked past,
    so the reminder before it that really is in the chat leaves at the next send."""
    _usual(db)
    _run(db, _bot())  # Friday's, message 900
    saturday = DAY + timedelta(days=1)
    _row(db, _utc(saturday, 10))
    crud_reminder_send.claim(
        db,
        user_id=1,
        local_day=saturday,
        slot="21:00",
        kind=reminders.EVENING,
        chat_id=42,
        now=_utc(saturday, 21, 5),
    )  # the worker died before anything else
    sunday = DAY + timedelta(days=2)
    _setting(db, WEEK_PUSH, False)
    _row(db, _utc(sunday, 10))
    bot = _bot(message_id=902)
    assert _run(db, bot, _utc(sunday, 21, 5))["sent"] == 1
    # Exactly 48 h old: too old to delete, so only its buttons go.
    bot.edit_message_reply_markup.assert_awaited_once_with(
        chat_id=42, message_id=900, reply_markup=None
    )


def _counting_bot():
    ids = iter(range(900, 1000))

    async def send_message(**_kw):
        return SimpleNamespace(message_id=next(ids))

    return SimpleNamespace(
        send_message=AsyncMock(side_effect=send_message),
        delete_message=AsyncMock(),
        edit_message_reply_markup=AsyncMock(),
    )


@BACKENDS
def test_after_a_return_through_the_sweep_one_unanswered_quiets_again(db):
    """R9 end to end: the resume writes count_from at the sweep's own moment and the
    same sweep sends then; that send counts, so leaving it unanswered quiets."""
    paused = DAY - timedelta(days=3)
    _set(db, quiet={"level": "off", "on": paused.isoformat()})
    # DAY-2: no row at all (the gap). DAY-1 and DAY: records.
    _row(db, _utc(DAY - timedelta(days=1), 10))
    _usual(db)
    assert _run(db, _counting_bot())["sent"] == 1
    cfg = reminders.config(_user(db))
    assert cfg.quiet is None and cfg.fragile is True
    send = db.query(ReminderSend).one()
    assert send.status == "sent" and send.answer is None
    saturday = DAY + timedelta(days=1)
    _row(db, _utc(saturday, 10))
    assert _reason(db, _utc(saturday, 21, 5)) == "quiet"
    assert reminders.config(_user(db)).quiet == {"level": "off", "on": DAY.isoformat()}


@BACKENDS
def test_b2_a_hard_week_then_a_return_through_the_sweep(db):
    """B1 + B2: last record Monday; sweeps every evening. Tue, Wed, Thu go out; rarer
    from Thursday (D7), so Friday and Saturday (a photo at 12:00) get nothing, Sunday
    is the week's, and Monday, the third day after Thursday, is sent."""
    monday = date(2026, 11, 2)
    _row(db, _utc(monday, 10))
    bot = _counting_bot()
    sent_on = []
    for offset in range(1, 8):  # Tue 3 .. Mon 9
        day = monday + timedelta(days=offset)
        if day == date(2026, 11, 7):
            _row(db, _utc(day, 12))  # the photo, Saturday 12:00
        if day == date(2026, 11, 9):
            _row(db, _utc(day, 9))  # Monday 9:00
        if _run(db, bot, _utc(day, 21, 5))["sent"]:
            sent_on.append(day)
        if day == date(2026, 11, 6):
            quiet = reminders.config(_user(db)).quiet
            assert quiet == {"level": "sparse", "on": "2026-11-05"}  # D7
    assert sent_on == [
        date(2026, 11, 3),
        date(2026, 11, 4),
        date(2026, 11, 5),
        date(2026, 11, 9),
    ]
    cfg = reminders.config(_user(db))
    # Monday's went unanswered too: still rarer (D7 needs no resume).
    assert cfg.quiet == {"level": "sparse", "on": "2026-11-05"} and not cfg.fragile
    assert reminders.hub_line(_user(db)) == "🔔 Напоминания: вечером, 21:00"
    # Thursday's untouched one leaves at Monday's send: four days old, so Telegram
    # no longer deletes it and only its buttons go (R4, §5.4's 48 h).
    thursday = db.query(ReminderSend).filter_by(local_day=date(2026, 11, 5)).one()
    assert thursday.closed_at is not None
    bot.edit_message_reply_markup.assert_awaited_with(
        chat_id=42, message_id=thursday.message_id, reply_markup=None
    )


def _texts(bot) -> list[str]:
    return [call.kwargs["text"] for call in bot.send_message.await_args_list]


def _sweep(db, bot, days, before=None) -> list[date]:
    """One sweep at 21:05 on each day; the days that got a message. ``before(day)``
    runs first on each (a record, a tap)."""
    sent_on = []
    for day in days:
        if before is not None:
            before(day)
        if _run(db, bot, _utc(day, 21, 5))["sent"]:
            sent_on.append(day)
    return sent_on


def _span(first, last):
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


REENTRY_1 = (
    "🔔 <b>Еда за сегодня</b>\nМожно начать с сегодняшнего. Прошлые дни "
    "восстанавливать не нужно."
)
REENTRY_2 = (
    "🔔 <b>Еда за сегодня</b>\nМогу больше не напоминать. Если захочется — можно "
    "просто отправить еду в любой момент."
)


def test_b1_a_hard_week_then_two_reentries_and_silence(db):
    """§9 B1 (D6, D7): last record Monday; Tue, Wed, Thu; rarer after Thursday;
    Sunday is the week card's; Monday (gap 7) re-entry #1, the Monday after (gap 14)
    re-entry #2; nothing after."""
    monday = date(2026, 11, 2)
    _row(db, _utc(monday, 10))
    bot = _counting_bot()
    seen = {}

    def look(day):
        if day == date(2026, 11, 7):  # Saturday: after Thursday, rarer
            seen["quiet"] = reminders.config(_user(db)).quiet
            seen["screen"] = _tap(db, "rm:o", _utc(day, 12)).text

    sent_on = _sweep(
        db, bot, _span(monday + timedelta(days=1), date(2026, 12, 6)), look
    )
    assert sent_on == [
        date(2026, 11, 3),
        date(2026, 11, 4),
        date(2026, 11, 5),
        date(2026, 11, 9),
        date(2026, 11, 16),
    ]
    assert seen["quiet"] == {"level": "sparse", "on": "2026-11-05"}
    assert reminders.SPARSE_LINE in seen["screen"]
    texts = _texts(bot)
    assert texts[3:] == [REENTRY_1, REENTRY_2]
    assert all(t.startswith(reminders.TITLE) for t in texts)
    assert _banned(texts) == []
    kinds = [s.kind for s in db.query(ReminderSend).order_by(ReminderSend.id)]
    assert kinds == ["evening"] * 3 + [reminders.REENTRY] * 2
    assert reminders.hub_line(_user(db)) == "🔔 Напоминания: вечером, 21:00"


def test_b3_breakfasts_daily_evenings_ignored(db):
    """§9 B3 (D7): rarer after the 3rd evening, one every 3rd evening, the pause
    after the 6th reminder; breakfasts never resume it; ⚙️ does, the same evening."""
    _setting(db, WEEK_PUSH, False)  # every evening is an evening
    first = date(2026, 11, 9)
    bot = _counting_bot()
    days = _span(first, first + timedelta(days=24))
    sent_on = _sweep(db, bot, days, lambda day: _row(db, _utc(day, 8)))
    assert sent_on == [first + timedelta(days=n) for n in (0, 1, 2, 5, 8, 11)]
    quiet = reminders.config(_user(db)).quiet
    assert quiet == {"level": "off", "on": (first + timedelta(days=11)).isoformat()}
    # ⚙️ ✓ 🕘 Вечером at 20:00: that evening goes.
    tap_day = days[-1] + timedelta(days=1)

    def resume(day):
        _row(db, _utc(day, 8))
        _tap(db, "rm:m:evening", _utc(day, 20))

    assert _sweep(db, bot, [tap_day], resume) == [tap_day]


def test_b4_logs_three_days_a_week_never_answers(db):
    """§9 B4 (D7): ≤ 3 in week 1, then about one every three days while unanswered,
    then the pause. Records at 19:30 Mon/Thu/Sat keep those evenings fresh (R8)."""
    first = date(2026, 11, 9)  # a Monday
    days = _span(first, first + timedelta(days=27))

    def log(day):
        if day.weekday() in (0, 3, 5):
            _row(db, _utc(day, 19, 30))

    bot = _counting_bot()
    sent_on = _sweep(db, bot, days, log)
    # Tue, Wed, Fri; then every third day or more; the 6th (Tue 24) pauses; a gap
    # day and two record days resume it, fragile, and one unanswered pauses again.
    assert sent_on == [
        date(2026, 11, 10),
        date(2026, 11, 11),
        date(2026, 11, 13),
        date(2026, 11, 17),
        date(2026, 11, 20),
        date(2026, 11, 24),
        date(2026, 12, 1),
    ]
    weeks = [sum(1 for d in sent_on if (d - first).days // 7 == w) for w in range(4)]
    assert weeks[0] <= 3
    assert all(n <= 3 for n in weeks[1:])
    # From the 3rd on, at least three days apart while unanswered.
    gaps = [(b - a).days for a, b in zip(sent_on[2:], sent_on[3:])]
    assert gaps and all(g >= 3 for g in gaps)
    # The sixth unanswered one is followed by a pause of more than a day or two;
    # the last Saturday's return resumed it again, fragile.
    sixth = sent_on[5]
    cfg = reminders.config(_user(db))
    assert cfg.quiet is None and cfg.fragile is True
    later = [d for d in sent_on if d > sixth]
    assert not later or (later[0] - sixth).days > 3


@pytest.mark.parametrize("gap, expected", [(20, [0]), (40, [])])
def test_c2_long_gone_at_launch(db, gap, expected):
    """§9 C2 (D6): last record 20 days ago → re-entry #2 only at launch, nothing
    after; 40 days → nothing. A new record makes rule 4 pass again."""
    _last_record(db, gap)
    bot = _counting_bot()
    sent_on = _sweep(db, bot, _span(DAY, DAY + timedelta(days=20)))
    assert sent_on == [DAY + timedelta(days=n) for n in expected]
    assert _texts(bot) == ([REENTRY_2] if expected else [])
    back = DAY + timedelta(days=21)
    _row(db, _utc(back, 10))
    assert _sweep(db, bot, [back]) == [back]
    # A re-entry is not the first reminder: the footnote is still to come.
    assert "Приходит в 21:00" in _texts(bot)[-1]


def test_l1_reentry_goes_while_paused_but_not_after_x(db):
    """§9 L1 (D6): gap 7 with the ladder at off → re-entry #1 goes. After ✖ →
    nothing."""
    _last_record(db, 7)
    _set(db, quiet={"level": "off", "on": (DAY - timedelta(days=9)).isoformat()})
    bot = _counting_bot()
    assert _sweep(db, bot, [DAY]) == [DAY]
    assert _texts(bot) == [REENTRY_1]
    send = db.query(ReminderSend).one()
    assert (send.kind, send.slot, send.status) == (reminders.REENTRY, "re7", "sent")
    _tap(db, f"rm:x:{send.id}", _utc(DAY, 21, 30))
    assert reminders.config(_user(db)).mode == "off"
    assert (
        _sweep(db, bot, _span(DAY + timedelta(days=1), DAY + timedelta(days=30))) == []
    )


def test_reentry_through_the_sweep(db):
    """D6: the owner's words, [✅ ОК] [✖ Выключить], no footnote, sound per ⚙️;
    reminders.noted is not set; R4 both ways."""
    _usual(db)
    old = _send(db, DAY - timedelta(days=1))  # untouched, still in the chat
    db.query(Meal).delete()
    db.commit()
    _last_record(db, 7)
    bot = _bot(message_id=901)
    assert _run(db, bot)["sent"] == 1
    kw = bot.send_message.await_args.kwargs
    assert kw["text"] == REENTRY_1
    assert _labels(kw["reply_markup"]) == ["✅ ОК", "✖ Выключить"]
    send = db.query(ReminderSend).filter_by(kind=reminders.REENTRY).one()
    assert _callbacks(kw["reply_markup"]) == [f"rm:k:{send.id}", f"rm:x:{send.id}"]
    assert kw["disable_notification"] is True
    assert reminders.config(_user(db)).noted is None
    bot.delete_message.assert_awaited_once_with(42, old.message_id)  # R4
    # And the next send deletes the re-entry.
    _row(db, _utc(DAY + timedelta(days=1), 10))
    bot = _bot(message_id=902)
    assert _run(db, bot, _utc(DAY + timedelta(days=1), 21, 5))["sent"] == 1
    bot.delete_message.assert_awaited_once_with(42, 901)


def test_reentry_views_hold_the_owners_words(db):
    for tag, expected in (
        (reminders.REENTRY_FIRST, REENTRY_1),
        (reminders.REENTRY_SECOND, REENTRY_2),
    ):
        send = _reentry_send(db, DAY - timedelta(days=len(tag)), tag)
        screen = reminders.reminder_view(db, _user(db), send, AT_21)
        assert screen.text == expected
        assert _labels(screen.markup) == ["✅ ОК", "✖ Выключить"]
        assert "<i>" not in screen.text and "⭐" not in screen.text


def test_reentry_shows_no_star_even_with_stars_saved(db):
    for name in ("Суп", "Хумус"):
        _fav(db, name)
    _last_record(db, 7)
    bot = _bot()
    _run(db, bot)
    kw = bot.send_message.await_args.kwargs
    assert kw["text"] == REENTRY_1
    assert len(kw["reply_markup"].inline_keyboard) == 1


def test_a_crafted_star_on_a_reentry_logs_nothing(db):
    fav = _fav(db, "Суп")
    send = _reentry_send(db, DAY, reminders.REENTRY_FIRST)
    _tap(db, f"rm:f:{send.id}:{fav.id}", AT_21)
    assert _favorite_meals(db) == []


def test_ok_on_a_reentry_is_neutral(db):
    send = _reentry_send(db, DAY, reminders.REENTRY_FIRST)
    screen = _tap(db, f"rm:k:{send.id}", AT_21)
    assert screen.toast == reminders.OK_TOAST
    db.refresh(send)
    assert send.answer == "ok" and reminders.outcome(send) == reminders.NEUTRAL


# --- the message (§3.1) ------------------------------------------------------------


def _first_view(db, now=AT_21):
    send = crud_reminder_send.claim(
        db,
        user_id=1,
        local_day=DAY,
        slot="21:00",
        kind="evening",
        chat_id=42,
        now=now,
    )
    return send, reminders.reminder_view(db, _user(db), send, now)


def test_the_first_names_the_zone_once_and_offers_it(db):
    """D1: the footnote names the zone; the zone row while none was chosen."""
    send, screen = _first_view(db)
    assert screen.text.startswith("🔔 <b>Еда за сегодня</b>\nМожно не отвечать.")
    assert "в 21:00 по времени Тбилиси (UTC+4), без звука" in screen.text
    assert "⭐" not in screen.text  # no ⭐ saved: no word of them
    assert screen.markup.inline_keyboard[-1][0].text == "🕐 Часовой пояс"
    assert screen.markup.inline_keyboard[-1][0].callback_data == "set:tz"
    assert _callbacks(screen.markup)[:2] == [f"rm:k:{send.id}", f"rm:x:{send.id}"]
    assert screen.view == meal_card.NO_MEAL
    # A chosen zone: named, not offered.
    _setting(db, TIMEZONE, "Asia/Tbilisi")
    screen = reminders.reminder_view(db, _user(db), send, AT_21)
    assert "Тбилиси (UTC+4)" in screen.text
    assert "set:tz" not in _callbacks(screen.markup)


def test_the_first_says_with_sound_when_on(db):
    _set(db, sound=True)
    _send_, screen = _first_view(db)
    assert "со звуком" in screen.text


def test_later_ones_are_short_with_no_zone_row(db):
    send, _screen = _first_view(db)
    _set(db, noted=send.id)
    later = _send(db, DAY + timedelta(days=1))
    screen = reminders.reminder_view(db, _user(db), later, AT_21)
    assert screen.text == f"{reminders.TITLE}\n{reminders._LATER}"
    assert "set:tz" not in _callbacks(screen.markup)
    assert len(screen.markup.inline_keyboard) == 1
    # The first one redrawn is still the first.
    again = reminders.reminder_view(db, _user(db), send, AT_21)
    assert reminders._FIRST in again.text


def test_no_times_in_any_reminder(db):
    for hour in (8, 11, 13, 19):
        _row(db, _utc(DAY, hour, 40))
    send, first = _first_view(db)
    body = first.text.replace("в 21:00 по времени", "")
    assert not re.search(r"\d:\d\d", body)
    _set(db, noted=send.id)
    later = reminders.reminder_view(db, _user(db), _send(db, DAY, slot="22:00"), AT_21)
    assert not re.search(r"\d", later.text)


def test_chips_ranked_by_days_eaten_with_todays_last(db):
    favs = {name: _fav(db, name) for name in ("Аа", "Бб", "Вв", "Гг", "Дд")}
    for back in (1, 2, 3):
        _eaten(db, favs["Бб"], DAY - timedelta(days=back))
    _eaten(db, favs["Вв"], DAY - timedelta(days=5))
    for back in (0, 1, 2, 3, 4):
        _eaten(db, favs["Аа"], DAY - timedelta(days=back))
    # 40 days ago is outside the four weeks.
    for back in (40, 41, 42, 43):
        _eaten(db, favs["Гг"], DAY - timedelta(days=back))
    picks = reminders.pick_favorites(db, _user(db), DAY, ZONE)
    # Then the list's own order: the newest ⭐ first.
    assert [name for _fav_, name in picks] == ["Бб", "Вв", "Дд", "Гг"]


def test_chips_two_a_row_and_cut_to_fit(db):
    for name in ("Овсянка", "Суп", "Гречка с грибами и луком по-домашнему", "Чай"):
        _fav(db, name)
    send, screen = _first_view(db)
    assert reminders._FIRST_STARS in screen.text
    rows = screen.markup.inline_keyboard
    assert [len(row) for row in rows[:2]] == [2, 2]
    assert all(c.callback_data.startswith(f"rm:f:{send.id}:") for c in rows[0])
    assert all(len(label) <= fv.PAIR_LABEL_MAX for label in _labels(screen.markup))
    _set(db, noted=send.id)
    tomorrow = _send(db, DAY + timedelta(days=1))
    later = reminders.reminder_view(db, _user(db), tomorrow, AT_21)
    assert len(later.markup.inline_keyboard) == 3
    assert later.text.endswith(reminders._LATER_STARS)


# --- a ⭐ from the reminder (§3.3) --------------------------------------------------


def _sent(db):
    _usual(db)
    fav = _fav(db, "Суп")
    _run(db, _bot())
    return db.query(ReminderSend).one(), fav


def test_a_star_logs_on_the_reminders_day_without_a_clock(db):
    send, fav = _sent(db)
    now = AT_21 + timedelta(minutes=5)
    screen = _tap(db, f"rm:f:{send.id}:{fav.id}", now)
    (meal,) = _favorite_meals(db)
    assert meal.time_precision == DAY_ONLY
    assert meal.time_source == "reminder"
    assert meal.resolution_signals["send_id"] == send.id
    moment = meal_card.local_time(meal.meal_time, ZONE)
    assert (moment.date(), moment.hour) == (DAY, 12)
    db.refresh(send)
    assert send.answer == "fav"
    assert screen.toast == "Записано"
    assert screen.meal_id == meal.id
    assert "✓ Записано на сегодня, без времени: Суп." in screen.text
    rows = screen.markup.inline_keyboard
    assert rows[0][0].text == "✅ ОК"
    assert _callbacks(screen.markup)[1:3] == [
        f"rm:u:{send.id}:{meal.id}",
        f"ml:t:{meal.id}:r",
    ]
    assert f"rm:f:{send.id}:{fav.id}" in _callbacks(screen.markup)  # chips stay


def test_a_star_tapped_twice_within_three_seconds_logs_once(db, factory):
    _usual(db)
    fav = _fav(db, "Суп")
    now = datetime.now(UTC)  # the guard reads the row's own created_at
    send = _send_now(db, now)
    data = f"rm:f:{send.id}:{fav.id}"
    screen = _tap(db, data, now, message_id=7)
    capture.tapped(db, 42, 7, screen)
    again = _tap(db, data, now + timedelta(seconds=1), message_id=7)
    assert (again.text, again.toast) == ("", fv.LOG_AGAIN_TOAST)
    assert len(_favorite_meals(db)) == 1
    _tap(db, data, now + timedelta(seconds=5), message_id=7)
    assert len(_favorite_meals(db)) == 2


def test_undo_takes_the_dish_back(db):
    send, fav = _sent(db)
    now = AT_21 + timedelta(minutes=5)
    _tap(db, f"rm:f:{send.id}:{fav.id}", now)
    (meal,) = _favorite_meals(db)
    screen = _tap(db, f"rm:u:{send.id}:{meal.id}", now)
    db.refresh(meal)
    assert meal.deleted_at is not None
    assert screen.toast == "Отменено"
    assert "↩️ Отменено: Суп." in screen.text
    assert "✅ ОК" not in _labels(screen.markup)
    assert screen.view == meal_card.NO_MEAL
    assert _tap(db, f"rm:u:{send.id}:{meal.id}", now).toast == reminders.UNDONE_TOAST
    db.refresh(send)
    assert send.answer == "ok"  # nothing logged from it: neutral, not answered
    bot = _bot(message_id=901)
    _next_evening(db, bot)
    bot.delete_message.assert_awaited_once_with(42, 900)  # R4: deleted, not stripped
    bot.edit_message_reply_markup.assert_not_awaited()


def test_undo_of_one_of_two_stars_keeps_the_answer(db):
    send, fav = _sent(db)
    now = AT_21 + timedelta(minutes=5)
    _tap(db, f"rm:f:{send.id}:{fav.id}", now)
    _tap(db, f"rm:f:{send.id}:{fav.id}", now + timedelta(seconds=10))
    first, latest = _favorite_meals(db)
    screen = _tap(db, f"rm:u:{send.id}:{latest.id}", now + timedelta(seconds=20))
    db.refresh(send)
    assert send.answer == "fav"
    # A3: the one still logged keeps ✅ ОК first, its ↩️ and its 🕐.
    assert "↩️ Отменено: Суп." in screen.text
    assert screen.markup.inline_keyboard[0][0].text == "✅ ОК"
    assert _callbacks(screen.markup)[1:3] == [
        f"rm:u:{send.id}:{first.id}",
        f"ml:t:{first.id}:r",
    ]
    assert screen.meal_id == first.id
    assert screen.view is None
    assert f"rm:f:{send.id}:{fav.id}" in _callbacks(screen.markup)  # chips stay


def _chips(markup) -> list[str]:
    return [data for data in _callbacks(markup) if data.startswith("rm:f:")]


@BACKENDS
def test_a_tap_does_not_move_the_chips(db):
    """§3.3 «Tapped chips stay»: in place, so a second tap at the same spot logs
    the dish the person sees there."""
    _usual(db)
    for name in ("Овсянка", "Хумус с хлебом", "Суп", "Чай"):
        _fav(db, name)
    bot = _bot()
    _run(db, bot)
    send = db.query(ReminderSend).one()
    sent = _chips(bot.send_message.await_args.kwargs["reply_markup"])
    assert len(sent) == 4
    now = AT_21 + timedelta(minutes=5)
    screen = _tap(db, sent[-1], now)
    assert _chips(screen.markup) == sent
    screen = _tap(db, sent[-1], now + timedelta(seconds=10))
    assert _chips(screen.markup) == sent
    screen = _tap(db, sent[1], now + timedelta(seconds=20))
    assert _chips(screen.markup) == sent
    assert _chips(reminders.reminder_view(db, _user(db), send, now).markup) == sent


def test_a_removed_star_says_so(db):
    send, fav = _sent(db)
    fav.removed_at = AT_21
    db.commit()
    screen = _tap(db, f"rm:f:{send.id}:{fav.id}", AT_21 + timedelta(minutes=5))
    assert screen.toast == reminders.STALE_TOAST
    assert _favorite_meals(db) == []


def test_an_old_reminder_opens_the_star_list(db):
    send, fav = _sent(db)
    screen = _tap(db, f"rm:f:{send.id}:{fav.id}", _utc(DAY + timedelta(days=3), 9))
    assert "Это напоминание за прошлые дни" in screen.text
    assert _favorite_meals(db) == []


def test_foreign_ids_get_todays_card(db):
    send, fav = _sent(db)
    db.add(User(id=2, telegram_id=43, settings={}))
    db.commit()
    theirs = _send(db, DAY, user_id=2)
    their_fav = _fav(db, "Чужое", user_id=2)
    now = AT_21 + timedelta(minutes=5)
    for data in (
        f"rm:f:{theirs.id}:{fav.id}",
        f"rm:f:{send.id}:{their_fav.id}",
        f"rm:k:{theirs.id}",
        f"rm:f:{send.id}:99999999999",
        "rm:zz",
    ):
        screen = _tap(db, data, now)
        assert reminders.TITLE not in screen.text, data
        assert screen.view == meal_card.NO_MEAL
    assert _favorite_meals(db) == []
    db.refresh(theirs)
    assert theirs.answer is None


def test_a_tap_after_midnight_names_the_reminders_day(db):
    send, fav = _sent(db)
    now = _utc(DAY + timedelta(days=1), 0, 20)
    screen = _tap(db, f"rm:f:{send.id}:{fav.id}", now)
    assert "✓ Записано на сегодня" not in screen.text
    assert "6 ноября" in screen.text
    (meal,) = _favorite_meals(db)
    time_screen = meal_card.handle_callback(db, 42, f"ml:t:{meal.id}:r", now)
    assert "Сегодня, 0:20" in _labels(time_screen.markup)


# --- ✖, ↩️, ⚙️ ----------------------------------------------------------------------


def test_off_at_once_and_back(db):
    _usual(db)
    _set(db, evening="22:00")
    send = _send(db, DAY, slot="22:00")
    screen = _tap(db, f"rm:x:{send.id}", AT_21)
    assert reminders._TURNED_OFF in screen.text
    assert _callbacks(screen.markup)[1:] == [f"rm:b:{send.id}", "rm:o"]
    assert reminders.config(_user(db)).mode == "off"
    assert reminders.hub_line(_user(db)) == "🔔 Напоминания: выкл"
    db.refresh(send)
    assert send.answer == "off"
    assert send.closed_at is not None
    screen = _tap(db, f"rm:b:{send.id}", AT_21)
    assert "✓ Напоминания включены: вечером, в 22:00." in screen.text
    cfg = reminders.config(_user(db))
    assert (cfg.mode, cfg.evening) == ("evening", "22:00")


def test_a_stale_back_leaves_the_settings_alone(db):
    _usual(db)
    send = _send(db, DAY)
    _tap(db, f"rm:x:{send.id}", AT_21)
    _tap(db, f"rm:b:{send.id}", AT_21)
    _tap(db, "rm:m:off", AT_21)  # then off in ⚙️
    screen = _tap(db, f"rm:b:{send.id}", AT_21)  # the old message's ↩️ again
    assert "✓ Напоминания выключены." in screen.text
    assert reminders.config(_user(db)).mode == "off"
    # ✖, then on again in ⚙️ at another hour: the old ↩️ does not undo that.
    _tap(db, "rm:m:evening", AT_21)
    _tap(db, f"rm:x:{send.id}", AT_21)
    _tap(db, "rm:m:evening", AT_21)
    _tap(db, "rm:e:2300", AT_21)
    _tap(db, f"rm:b:{send.id}", AT_21)
    cfg = reminders.config(_user(db))
    assert (cfg.mode, cfg.evening) == ("evening", "23:00")


def test_a_stale_hour_while_off_keeps_them_off(db):
    _usual(db)
    _unanswered(db, 6)
    assert _reason(db) == "quiet"
    _tap(db, "rm:m:off", AT_21)
    screen = _tap(db, "rm:e:2200", AT_21)
    assert "✓ Время: 22:00" in screen.text
    assert "Сейчас: выключены." in screen.text
    cfg = reminders.config(_user(db))
    assert (cfg.mode, cfg.evening) == ("off", "22:00")
    assert cfg.quiet is not None  # turning on again is what clears the pause


def test_resuming_inside_the_hour_waits_for_the_next_slot(db):
    _usual(db)
    _unanswered(db, 6)
    assert _reason(db) == "quiet"
    _tap(db, "rm:m:evening", _utc(DAY, 21, 10))
    assert _reason(db, now=_utc(DAY, 21, 15)) == "turned on after the slot"
    tomorrow = DAY + timedelta(days=1)
    _row(db, _utc(tomorrow, 10))
    assert _reason(db, now=_utc(tomorrow, 21, 5)) is None


def test_the_settings_screen(db):
    screen = _tap(db, "rm:o", AT_21)
    assert "Сейчас: вечером, в 21:00 по времени Тбилиси (UTC+4)." in screen.text
    assert "без звука" in screen.text
    rows = [[b.callback_data for b in row] for row in screen.markup.inline_keyboard]
    assert rows == [
        ["rm:m:off", "rm:m:evening"],
        ["rm:e:2000", "rm:e:2100", "rm:e:2200", "rm:e:2300"],
        ["rm:week", "set:tz"],
        ["rm:snd"],
        ["set:open"],
    ]
    assert "✓ 21:00" in _labels(screen.markup)
    assert "🔔 Звук: выкл" in _labels(screen.markup)


def test_sound_toggle(db):
    """D3: «✓ Со звуком» / «✓ Без звука»."""
    screen = _tap(db, "rm:snd", AT_21)
    assert "✓ Со звуком" in screen.text
    assert "🔔 Звук: вкл" in _labels(screen.markup)
    assert reminders.config(_user(db)).sound is True
    screen = _tap(db, "rm:snd", AT_21)
    assert "✓ Без звука" in screen.text
    assert reminders.config(_user(db)).sound is False


def test_hour_and_mode(db):
    screen = _tap(db, "rm:e:2300", AT_21)
    assert "✓ Время: 23:00" in screen.text
    cfg = reminders.config(_user(db))
    assert cfg.evening == "23:00" and cfg.since == AT_21
    assert reminders.TITLE not in _tap(db, "rm:e:2130", AT_21).text  # not a choice
    screen = _tap(db, "rm:m:off", AT_21)
    assert "Сейчас: выключены." in screen.text
    assert len(screen.markup.inline_keyboard) == 4  # no hours while off
    screen = _tap(db, "rm:m:evening", AT_21)
    assert "✓ Напоминания: вечером, в 23:00" in screen.text


def test_the_pause_shows_in_the_sub_screen_never_in_the_hub(db):
    _set(db, quiet={"level": "off", "on": DAY.isoformat()})
    assert "Сейчас не приходят." in _tap(db, "rm:o", AT_21).text
    assert reminders.hub_line(_user(db)) == "🔔 Напоминания: вечером, 21:00"
    hub = settings_screen.open_view(db, 42, now=AT_21)
    assert "🔔 Напоминания: вечером, 21:00" in hub.text
    assert "не приходят" not in hub.text
    assert len(hub.markup.inline_keyboard) == 5
    assert len(_labels(hub.markup)) == 11
    assert "rm:o" in _callbacks(hub.markup)


def test_week_toggle_here_and_the_legacy_button(db):
    screen = _tap(db, "rm:week", AT_21)
    assert "✓ Итог недели выключен" in screen.text
    assert settings_screen.week_push_on(_user(db)) is False
    settings_screen.handle_callback(db, 42, "set:week", AT_21)
    assert settings_screen.week_push_on(_user(db)) is True


# --- the bot side ------------------------------------------------------------------


def _query(data, telegram_id=42):
    query = SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        message=None,
        edit_message_text=AsyncMock(),
        edit_message_reply_markup=AsyncMock(),
    )
    update = SimpleNamespace(
        callback_query=query, effective_user=SimpleNamespace(id=telegram_id)
    )
    return update, query


def test_handler_ok_takes_the_buttons_and_keeps_the_text(factory):
    with factory() as db:
        send = _send(db, DAY)
        sid = send.id
    update, query = _query(f"rm:k:{sid}")
    asyncio.run(tg.reminders_callback(update, SimpleNamespace()))
    query.answer.assert_awaited_once_with("Понятно")
    query.edit_message_text.assert_not_awaited()
    query.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)


def test_handler_star_edits_in_place(factory):
    with factory() as db:
        _usual(db)
        fav = _fav(db, "Суп")
        send = _send_now(db, datetime.now(UTC))
        data = f"rm:f:{send.id}:{fav.id}"
    update, query = _query(data)
    asyncio.run(tg.reminders_callback(update, SimpleNamespace()))
    query.answer.assert_awaited_once_with("Записано")
    (text,), _kw = query.edit_message_text.await_args
    assert "✓ Записано на сегодня, без времени: Суп." in text
    query.edit_message_reply_markup.assert_not_awaited()


def test_handler_is_registered_before_the_dishes(factory):
    source = (ROOT / "app/services/telegram.py").read_text()
    assert source.index("reminders_callback, pattern") < source.index(
        "dishes_callback, pattern"
    )


# --- no model, no network (R1) -----------------------------------------------------


def test_no_model_call_anywhere(db, monkeypatch):
    calls = []

    def refuse(*args, **kwargs):
        calls.append(args)
        raise AssertionError("a model call")

    monkeypatch.setattr(openai_service.OpenAIService, "__init__", refuse)
    for name in ("analyze_food_entry", "analyze_food_image"):
        if hasattr(openai_service.OpenAIService, name):
            monkeypatch.setattr(openai_service.OpenAIService, name, refuse)
    monkeypatch.setattr(httpx.Client, "send", refuse)
    monkeypatch.setattr(httpx.AsyncClient, "send", refuse)

    send, fav = _sent(db)  # a sweep
    now = AT_21 + timedelta(minutes=5)
    _tap(db, f"rm:f:{send.id}:{fav.id}", now)  # a ⭐
    (meal,) = _favorite_meals(db)
    for data in (f"rm:u:{send.id}:{meal.id}", f"rm:k:{send.id}", "rm:o", "rm:snd"):
        _tap(db, data, now)
    _tap(db, f"rm:x:{send.id}", now)
    assert calls == []
    # The control: the same guards do catch a call.
    with pytest.raises(AssertionError, match="a model call"):
        openai_service.OpenAIService()
    with pytest.raises(AssertionError, match="a model call"):
        httpx.Client().get("http://127.0.0.1:9/")
    assert len(calls) == 2


# --- what the texts may say (R10) ---------------------------------------------------

_BANNED = re.compile(
    r"пропуск|пропущен|забыл|забыв|не записано|ещё не|еще не|пусто|снова|опять"
    r"|если было что-то ещё|\d+\s*(?:дн|день|дня)",
    re.IGNORECASE,
)


def _cyrillic_literals(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and re.search("[а-яё]", node.value, re.IGNORECASE)
    ]


def _banned(texts) -> list[str]:
    return [text for text in texts if _BANNED.search(text)]


def test_r10_words_never_said(db):
    texts = _cyrillic_literals(ROOT / "app/services/reminders.py")
    topic = explain_screen.TOPICS["reminders"]
    texts += [topic.label, topic.what, topic.how, topic.why]
    _send_, screen = _first_view(db)
    texts.append(screen.text)
    # D6, D7, D8: every new text, as shown.
    for tag in reminders.REENTRY_DAYS:
        send = _reentry_send(db, DAY - timedelta(days=len(tag)), tag)
        texts.append(reminders.reminder_view(db, _user(db), send, AT_21).text)
    for level in ("sparse", "off"):
        _set(db, quiet={"level": level, "on": DAY.isoformat()})
        texts.append(_tap(db, "rm:o", AT_21).text)
    texts += [reminders._FIRST, reminders._LATER_STARS, reminders._LATER]
    for new in (*reminders.REENTRY_TEXT.values(), reminders.SPARSE_LINE):
        assert new in texts  # the scan reads the new literals too
    assert len(texts) > 20  # the scan did read the module
    assert _banned(texts) == []
    # The control: the same check catches a banned word.
    assert _banned(["Сегодня пропущено"]) == ["Сегодня пропущено"]
    assert _banned(["Уже 3 дня"]) == ["Уже 3 дня"]


def test_d8_the_reminder_says_roughly_and_not_all():
    """D8, the owner's words."""
    assert reminders._FIRST == (
        "Можно не отвечать. Если что-то вспоминается, можно рассказать голосовым или "
        "написать — примерно и не всё. Время можно назвать: «в 13 суп». Без времени "
        "запишется на этот день, без часов."
    )
    assert reminders._LATER_STARS == (
        "Можно не отвечать. Если что-то вспоминается — голосовым, текстом или блюдом "
        "из ⭐, можно примерно и не всё:"
    )
    assert reminders._LATER == (
        "Можно не отвечать. Если что-то вспоминается — голосовым или текстом, можно "
        "примерно и не всё."
    )


def test_d8_the_shown_reminders_carry_the_new_words(db):
    send, screen = _first_view(db)
    assert screen.text.split("\n")[1] == reminders._FIRST
    _set(db, noted=send.id)
    later = _send(db, DAY + timedelta(days=1))
    assert reminders.reminder_view(db, _user(db), later, AT_21).text.endswith(
        "можно примерно и не всё."
    )
    _fav(db, "Суп")
    starred = _send(db, DAY + timedelta(days=2))
    text = reminders.reminder_view(db, _user(db), starred, AT_21).text
    assert text.endswith("блюдом из ⭐, можно примерно и не всё:")


def test_the_explanation_holds_the_new_sentences():
    """§10 as D8 and the ladder/re-entry sentence have it."""
    topic = explain_screen.TOPICS["reminders"]
    assert topic.what == (
        "Короткое сообщение вечером: если что-то из еды вспоминается, можно рассказать "
        "голосовым, написать или нажать на блюдо из ⭐, примерно и не всё. Отвечать не "
        "обязательно."
    )
    assert topic.how.endswith(
        "Если напоминания подряд остаются без ответа, они приходят реже, а потом "
        "перестают и возвращаются после перерыва в записях или кнопкой в настройках. "
        "Если записей долго нет, придут одно-два коротких сообщения о том, что можно "
        "начать с любого дня, и дальше бот молчит, пока не придёт еда."
    )
    assert "Если три" not in topic.how


def test_the_sub_screen_says_rarer_or_paused(db):
    _set(db, quiet={"level": "sparse", "on": DAY.isoformat()})
    text = _tap(db, "rm:o", AT_21).text
    assert (
        "Сейчас приходят реже, потому что остаются без ответа. Как раньше — кнопкой "
        "✓ 🕘 Вечером." in text
    )
    assert "Сейчас:" not in text and "не приходят" not in text
    _set(db, quiet={"level": "off", "on": DAY.isoformat()})
    text = _tap(db, "rm:o", AT_21).text
    assert "Сейчас не приходят. Включить — кнопкой ✓ 🕘 Вечером." in text
    assert reminders.SPARSE_LINE not in text
    _set(db)
    assert "Сейчас: вечером" in _tap(db, "rm:o", AT_21).text


def test_the_explanation_says_what_the_time_screen_offers():
    """§10, §3.5: every ⭐ from a reminder is day_only, so the 🕐 options and R0's
    one-message-one-meal rule are what the entry has to say."""
    how = explain_screen.TOPICS["reminders"].how
    for phrase in ("«утром», «днём», «вечером»", "когда ответ был прислан"):
        assert phrase in how
    assert "на день приёма" in how
    assert "несколько времён, оно записывается без часов" in how


# --- the schedule --------------------------------------------------------------------


def test_beat_entry_and_include():
    from celery_app.celery_app import app

    assert "celery_app.tasks.reminders" in app.conf.include
    entry = app.conf.beat_schedule["meal-reminders"]
    assert entry["task"] == "celery_app.tasks.reminders.send_reminders"
    assert entry["options"] == {"expires": 840}
    assert entry["schedule"].minute == {0, 15, 30, 45}
    purge = app.conf.beat_schedule["purge-reminder-sends"]
    assert purge["task"] == "celery_app.tasks.periodic.purge_reminder_sends"


def test_task_builds_no_bot_when_nobody_is_due(db, monkeypatch):
    from celery_app.tasks import reminders as task

    monkeypatch.setattr(task, "SessionLocal", lambda: db)

    def no_bot(*_args, **_kwargs):
        raise AssertionError("a bot was built")

    monkeypatch.setattr(task.custom_emoji, "build_bot", no_bot)
    assert task.send_reminders() == task.NOTHING


def test_task_builds_a_bot_and_sends_when_someone_is_due(db, monkeypatch):
    """The pair of the one above: the same task does build a bot and send."""
    from celery_app.tasks import reminders as task

    _usual(db)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return AT_21

    bot = _bot()
    bot.emoji = SimpleNamespace(load=AsyncMock(), problem=None)

    class Bot:
        async def __aenter__(self):
            return bot

        async def __aexit__(self, *_exc):
            return False

        def __getattr__(self, name):
            return getattr(bot, name)

    built = []

    def build_bot(*args, **kwargs):
        built.append(args)
        return Bot()

    monkeypatch.setattr(task, "SessionLocal", lambda: db)
    monkeypatch.setattr(task, "datetime", Clock)
    monkeypatch.setattr(task.custom_emoji, "build_bot", build_bot)
    counts = task.send_reminders()
    assert len(built) == 1
    assert counts["sent"] == 1
    bot.send_message.assert_awaited_once()
