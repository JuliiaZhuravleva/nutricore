"""The action journal: what a person did, and never what about.

Real updates through the real application at the wire. The owner's rule is that no food
text ever reaches ``action_events``: the marker below is in every name a tap or a message
could carry, and is looked for in every row.
"""

from __future__ import annotations

import datetime
import importlib.util
import logging
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import select
from telegram.ext import CallbackQueryHandler, CommandHandler, ConversationHandler

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.core.config import settings
from app.crud.crud_action_event import crud_action_event
from app.crud.crud_record_event import crud_record_event
from app.db.base import Base
from app.models import ActionEvent, Meal, PersonalFood, UsageLedger
from app.models import action_event as model
from app.schemas.meal import MealCreate
from app.services import action_journal, meal_items
from app.services import telegram as tg
from app.services import voice
from celery_app.celery_app import app as celery
from celery_app.tasks import periodic
from tests.test_capture_failures import _a_clean_run, tap  # noqa: F401 (fixtures)
from tests.test_capture_router import (  # noqa: F401 (fixtures)
    NOW_TS,
    NUTRITION,
    OWNER,
    PHOTO,
    Session,
    _message,
    _voice,
    db,
    owner,
    reader,
    send,
    text,
)

ROOT = Path(__file__).resolve().parent.parent
MARK = "зюзя"
UTC = datetime.timezone.utc


@pytest.fixture(autouse=True)
def _journal(Session, monkeypatch):  # noqa: F811
    monkeypatch.setattr(action_journal, "SessionLocal", Session)


def _rows(db) -> list:  # noqa: F811
    db.expire_all()
    return [
        (row.kind, row.label)
        for row in db.scalars(select(ActionEvent).order_by(ActionEvent.id))
    ]


def _assert_no_marker(db) -> None:  # noqa: F811
    db.expire_all()
    rows = db.execute(select(ActionEvent)).scalars().all()
    assert rows  # control: there is something to look through
    for row in rows:
        assert MARK not in repr((row.kind, row.label)).lower()


# -- what is written ------------------------------------------------------------------


def test_a_photo_a_text_a_voice_and_what_came_of_them(db, owner, reader):  # noqa: F811
    send(_message(photo=PHOTO), text("суп"))
    rows = _rows(db)
    assert ("photo", None) in rows
    assert ("text", None) in rows
    assert ("saved", "image") in rows
    assert ("saved", "text") in rows


def test_a_read_that_failed_is_a_failure_of_its_kind(db, owner, reader):  # noqa: F811
    reader.error = RuntimeError("the model is down")
    send(text("суп"))
    assert ("failed", "text") in _rows(db)
    assert ("saved", "text") not in _rows(db)


def test_a_voice_nobody_could_hear_is_a_failure(db, owner, reader):  # noqa: F811
    send(_voice())
    rows = _rows(db)
    assert ("voice", None) in rows
    assert ("failed", "voice") in rows  # no paid door in a test: the listening fails


def test_a_voice_with_no_words_is_no_failure(
    db, owner, reader, monkeypatch  # noqa: F811
):
    """Silence is nothing to read, and the journal's other side of the same guard."""

    async def silent(_bot, _job):
        raise voice.NoSpeech()

    monkeypatch.setattr(tg, "_voice_words", silent)
    send(_voice())
    rows = _rows(db)
    assert ("voice", None) in rows
    assert ("failed", "voice") not in rows
    assert ("saved", "voice") not in rows


def test_a_menu_tap_stores_the_exact_label(db, owner, reader):  # noqa: F811
    send(text("📊 Статистика"))
    assert _rows(db)[0] == ("button", "📊 Статистика")


def test_the_old_spelling_on_someones_keyboard_is_the_same_label(
    db, owner, reader  # noqa: F811
):
    send(text("🍽 Добавить прием пищи"))
    assert _rows(db)[0] == ("button", "🍽 Добавить приём пищи")


def test_a_command_stores_its_name_and_no_argument(db, owner, reader):  # noqa: F811
    length = len("/start")
    send(
        text(
            f"/start {MARK}",
            entities=[{"type": "bot_command", "offset": 0, "length": length}],
        )
    )
    assert _rows(db)[0] == ("command", "start")
    _assert_no_marker(db)


def test_an_inline_tap_stores_its_verb(db, owner, reader):  # noqa: F811
    tap("st:day")
    assert _rows(db)[0] == ("button", "st")


def test_a_stranger_leaves_nothing(db, owner, reader, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "BOT_ACCESS_MODE", "whitelist")
    monkeypatch.setattr(settings, "ALLOWED_TELEGRAM_IDS", "")
    stranger = {"id": 7007, "is_bot": False, "first_name": "x", "username": "x"}
    send(
        _message(
            text="суп",
            chat={"id": 7007, "type": "private"},
            **{"from": stranger},
        )
    )
    assert _rows(db) == []


def test_someone_let_in_is_written_not_wrongly_refused(
    db, owner, reader, monkeypatch  # noqa: F811
):
    monkeypatch.setattr(settings, "BOT_ACCESS_MODE", "whitelist")
    monkeypatch.setattr(settings, "ALLOWED_TELEGRAM_IDS", "7007")
    invited = {"id": 7007, "is_bot": False, "first_name": "x", "username": "x"}
    send(
        _message(
            text="суп",
            chat={"id": 7007, "type": "private"},
            **{"from": invited},
        )
    )
    assert _rows(db) == [("text", None)]


# -- the privacy rule -----------------------------------------------------------------


def test_the_marker_reaches_no_row_whatever_carries_it(db, owner, reader):  # noqa: F811
    """Everything a person can do with a meal and a dish named with the marker."""
    reader.answer = {**NUTRITION, "foods": [f"{MARK} с рисом"]}
    send(
        text(f"{MARK} и рис"),
        _message(photo=PHOTO, caption=f"{MARK} на фото"),
        # A slash in front of a word is no command this bot has.
        text(f"/{MARK}"),
        # Near a menu label, not equal to it.
        text(f"📊 Статистика {MARK}"),
        text(f"⭐ {MARK}"),
        # A reply to a message whose keyboard held the name.
        _message(
            text="да",
            reply_to_message={
                "message_id": 1,
                "date": NOW_TS,
                "chat": {"id": OWNER, "type": "private"},
                "text": f"{MARK} 200 г",
                "reply_markup": {
                    "inline_keyboard": [[{"text": MARK, "callback_data": "dh:1"}]]
                },
            },
        ),
    )
    meal = db.scalars(select(Meal)).first()
    dish = PersonalFood(user_id=owner.id, canonical_name=f"{MARK} дня")
    db.add(dish)
    db.commit()
    # Real verbs on a real meal and dish, and data no handler would ever send.
    for data in (
        f"dh:{dish.id}",
        f"ml:{meal.id}",
        f"meal:{meal.id}",
        f"fav:{meal.id}",
        f"{MARK}:1",
        MARK,
        f"st:{MARK}",
    ):
        tap(data)
    rows = _rows(db)
    assert ("text", None) in rows and ("photo", None) in rows  # control
    assert ("button", "dh") in rows
    assert ("button", None) in rows  # the verb nobody knows: a tap, and no word of it
    _assert_no_marker(db)


def test_a_label_the_lists_do_not_hold_is_refused_for_every_writer(
    db, owner, caplog  # noqa: F811
):
    """The last gate, at the one place all rows go through, and it logs."""
    with caplog.at_level(logging.WARNING, logger="app.crud.crud_action_event"):
        crud_action_event.add(db, telegram_id=1, kind="button", label=MARK)
        crud_action_event.add(db, telegram_id=1, kind="text", label=MARK)
        crud_action_event.add(db, telegram_id=1, kind="corrected", label=f"🍲 {MARK}")
    db.commit()
    assert _rows(db) == [("button", None), ("text", None), ("corrected", None)]
    assert "Action journal refused a label on a button row" in caplog.text
    assert MARK not in caplog.text


def test_a_label_on_the_list_is_not_wrongly_refused(db, owner, caplog):  # noqa: F811
    with caplog.at_level(logging.WARNING, logger="app.crud.crud_action_event"):
        crud_action_event.add(db, telegram_id=1, kind="button", label="st")
        crud_action_event.add(db, telegram_id=1, kind="button", label="📊 Статистика")
        crud_action_event.add(db, telegram_id=1, kind="command", label="start")
        crud_action_event.add(db, telegram_id=1, kind="corrected", label="grams")
        crud_action_event.add(db, telegram_id=1, kind="saved", label="voice")
    db.commit()
    assert all(label for _, label in _rows(db))
    assert "refused" not in caplog.text


def test_a_label_of_one_kind_does_not_pass_for_another(db, owner):  # noqa: F811
    crud_action_event.add(db, telegram_id=1, kind="command", label="st")
    db.commit()
    assert _rows(db) == [("command", None)]


def test_an_unknown_kind_is_refused(db, owner):  # noqa: F811
    with pytest.raises(ValueError, match="unknown action kind: gossip"):
        crud_action_event.add(db, telegram_id=1, kind="gossip")


# -- the closed lists stay true to the bot ---------------------------------------------


def _handlers(application) -> list:
    found = []

    def walk(handler):
        if isinstance(handler, ConversationHandler):
            for inner in handler.entry_points + handler.fallbacks:
                walk(inner)
            for state in handler.states.values():
                for inner in state:
                    walk(inner)
        else:
            found.append(handler)

    for group in application.handlers.values():
        for handler in group:
            walk(handler)
    return found


def test_the_menu_labels_are_the_real_keyboards():
    real = {button.text for row in tg.main_keyboard.keyboard for button in row}
    assert real == set(model.MENU_LABELS)


def test_the_commands_are_the_real_commands(Session):  # noqa: F811
    real = set()
    for handler in _handlers(tg.create_bot_application()):
        if isinstance(handler, CommandHandler):
            real |= set(handler.commands)
    assert real  # control
    assert real == set(model.COMMANDS)


def test_the_verbs_are_the_real_prefixes(Session):  # noqa: F811
    real = set()
    for handler in _handlers(tg.create_bot_application()):
        if isinstance(handler, CallbackQueryHandler) and handler.pattern is not None:
            pattern = handler.pattern.pattern
            if pattern.startswith("^(?!"):
                continue  # «anything but»: names no verb of its own
            real.add(pattern.lstrip("^(").partition(":")[0])
    assert {"st", "dh"} <= real  # control
    assert real == set(model.BUTTON_VERBS)


def test_every_name_can_be_shown():
    assert set(action_journal.BUTTON_NAMES) == set(model.BUTTON_VERBS)
    assert set(action_journal.CORRECTION_NAMES) == set(model.CORRECTION_KINDS)


def test_a_correction_is_a_kind_record_events_really_has():
    from app.models.record_event import EVENT_KINDS

    assert set(model.CORRECTION_KINDS) <= set(EVENT_KINDS)


# -- corrections and saves written beside the change ----------------------------------


def _a_meal(db, owner) -> Meal:  # noqa: F811
    meal = Meal(user_id=owner.id, description="суп", nutrients={"foods": ["суп"]})
    db.add(meal)
    db.commit()
    return meal


def _event(db, meal, kind, **more):  # noqa: F811
    return crud_record_event.add(
        db,
        user_id=meal.user_id,
        meal_id=meal.id,
        kind=kind,
        before={"was": 1},
        **more,
    )


def test_a_correction_goes_in_with_the_change(db, owner):  # noqa: F811
    meal = _a_meal(db, owner)
    _event(db, meal, "grams")
    db.commit()
    assert _rows(db) == [("corrected", "grams")]


def test_an_undo_and_what_is_no_correction_are_not_counted(db, owner):  # noqa: F811
    meal = _a_meal(db, owner)
    first = _event(db, meal, "write")  # not a person setting a record right
    assert _rows(db) == []
    _event(db, meal, "grams", undoes_event_id=first.id)
    assert _rows(db) == []


def test_a_journal_that_cannot_write_leaves_the_correction_standing(
    db, owner, monkeypatch, caplog  # noqa: F811
):
    meal = _a_meal(db, owner)

    def broken(*_args, **_kwargs):
        raise RuntimeError("no such table")

    monkeypatch.setattr(crud_action_event, "add", broken)
    with caplog.at_level(logging.WARNING, logger="app.crud.crud_action_event"):
        event = _event(db, meal, "grams")
    db.commit()
    assert event.id is not None
    assert "Action journal: a corrected not written: RuntimeError" in caplog.text


def test_a_broken_table_rolls_back_only_the_journal(db, owner, caplog):  # noqa: F811
    """A real failure in the database: the savepoint, not just the try."""
    meal = _a_meal(db, owner)
    db.execute(sa.text("DROP TABLE action_events"))
    with caplog.at_level(logging.WARNING, logger="app.crud.crud_action_event"):
        event = _event(db, meal, "grams")
    db.commit()
    db.expire_all()
    assert event.id is not None
    assert "Action journal: a corrected not written" in caplog.text


def test_a_dish_logged_with_no_read_is_saved(db, owner):  # noqa: F811
    meal_in = MealCreate(
        description="суп", meal_time=datetime.datetime(2026, 11, 2, tzinfo=UTC)
    )
    meal_items.save_meal(db, meal_in, owner.id, names=["суп"])
    assert ("saved", "dish") in _rows(db)


def test_an_update_when_the_journal_is_down_goes_on_as_usual(
    db, owner, reader, monkeypatch, caplog  # noqa: F811
):
    def broken():
        raise RuntimeError("the database is away")

    monkeypatch.setattr(action_journal, "SessionLocal", broken)
    with caplog.at_level(logging.WARNING, logger="app.services.action_journal"):
        send(text("суп"))
    assert db.scalars(select(Meal)).first() is not None
    assert "Action journal: a text not written: RuntimeError" in caplog.text


# -- what slices 4 and 5 read ----------------------------------------------------------


def _at(db, person, when, kind="text", label=None):  # noqa: F811
    crud_action_event.add(db, telegram_id=person, kind=kind, label=label, at=when)
    db.commit()


def test_the_last_actions_are_one_persons_oldest_first_and_capped(
    db, owner  # noqa: F811
):
    start = datetime.datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    for minute in range(25):
        _at(db, 1, start + datetime.timedelta(minutes=minute))
    _at(db, 2, start + datetime.timedelta(hours=1))
    last = crud_action_event.last_actions(db, 1)
    assert len(last) == 20
    assert [row.telegram_id for row in last] == [1] * 20
    assert last[0].at.minute == 5 and last[-1].at.minute == 24


def test_counts_last_seen_and_active_days(db, owner):  # noqa: F811
    from zoneinfo import ZoneInfo

    zone = ZoneInfo("Asia/Tbilisi")  # +04
    now = datetime.datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
    # 21:00 UTC on the 31st is already the first of November in the zone.
    _at(db, 1, datetime.datetime(2026, 10, 31, 21, 0, tzinfo=UTC), "photo")
    _at(db, 1, now, "text")
    _at(db, 1, now + datetime.timedelta(hours=1), "saved", "text")
    _at(db, 2, now, "voice")
    since = now - datetime.timedelta(days=30)
    assert crud_action_event.counts_by_kind(db, 1, since=since) == {
        "photo": 1,
        "text": 1,
        "saved": 1,
    }
    # A read that finished later is not the person being there.
    assert crud_action_event.last_seen(db, 1) == now
    assert crud_action_event.last_seen_all(db) == {1: now, 2: now}
    flags = crud_action_event.active_days(db, 1, zone=zone, now=now)
    assert flags == [False, False, False, False, False, True, True]
    assert crud_action_event.last_seen(db, 9) is None


def test_a_row_reads_as_in_the_design(db, owner):  # noqa: F811
    from zoneinfo import ZoneInfo

    zone = ZoneInfo("Asia/Tbilisi")
    when = datetime.datetime(2026, 11, 2, 10, 2, tzinfo=UTC)
    for kind, label, shown in (
        ("photo", None, "14:02 📷 фото"),
        ("saved", "image", "14:02 📷 → записано"),
        ("corrected", "grams", "14:02 ✏️ вес изменён"),
        ("failed", "voice", "14:02 🎙 → ⚠️ чтение не удалось"),
        ("button", "st", "14:02 👆 📊 Статистика"),
        ("button", "📊 Статистика", "14:02 👆 📊 Статистика"),
        ("button", None, "14:02 👆 кнопка"),
        ("command", "start", "14:02 /start"),
    ):
        event = ActionEvent(telegram_id=1, at=when, kind=kind, label=label)
        assert action_journal.line(event, zone) == shown


# -- the purge --------------------------------------------------------------------------


def test_the_purge_takes_what_is_past_60_days_and_leaves_the_rest(
    db, Session, monkeypatch  # noqa: F811
):
    monkeypatch.setattr(periodic, "SessionLocal", Session)
    now = datetime.datetime.now(UTC)
    _at(db, 1, now - datetime.timedelta(days=61), "text")
    _at(db, 1, now - datetime.timedelta(days=59), "photo")
    db.add(UsageLedger(telegram_id=1, day=datetime.date(2020, 1, 1), calls=1))
    db.commit()
    assert settings.ACTION_EVENT_RETENTION_DAYS == 60
    assert periodic.purge_action_events() == 1
    assert _rows(db) == [("photo", None)]
    # The ledger has no purge: it holds no words.
    assert db.scalars(select(UsageLedger)).one().day.year == 2020


def test_the_purge_is_on_the_schedule():
    entries = {e["task"] for e in celery.conf.beat_schedule.values()}
    assert "celery_app.tasks.periodic.purge_action_events" in entries


# -- the migration -----------------------------------------------------------------------


def _migration():
    path = (
        ROOT / "alembic" / "versions" / "d0e1f2a3b4c5_usage_ledger_and_action_events.py"
    )
    spec = importlib.util.spec_from_file_location("slice_ledger", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _step(conn, name: str) -> None:
    with Operations.context(MigrationContext.configure(conn)):
        getattr(_migration(), name)()


def test_the_migration_makes_what_the_models_say_and_takes_it_back():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _step(conn, "upgrade")
        inspector = sa.inspect(conn)
        for table in ("usage_ledger", "action_events"):
            assert {c["name"] for c in inspector.get_columns(table)} == {
                c.name for c in Base.metadata.tables[table].columns
            }
            assert {i["name"] for i in inspector.get_indexes(table)} == {
                i.name for i in Base.metadata.tables[table].indexes
            }
        assert "uq_usage_ledger_person_day" in {
            u["name"] for u in inspector.get_unique_constraints("usage_ledger")
        }
        _step(conn, "downgrade")
        assert not {"usage_ledger", "action_events"} & set(
            sa.inspect(conn).get_table_names()
        )


def test_the_migration_stands_on_the_old_head():
    module = _migration()
    assert module.down_revision == "c9d0e1f2a3b4"
    assert module.revision == "d0e1f2a3b4c5"
