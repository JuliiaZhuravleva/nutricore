"""☀️ The morning question about yesterday (stage 6, D10-D14): when it goes out, what it
says, every tap, the 🔔 switch, and what «почти нет» does to the 7-day numbers.

On an in-memory SQLite DB with a fake bot: no model and no network anywhere. Wednesday
11 November 2026 in Tbilisi (UTC+4) asks about Tuesday the 10th. The claim and send tests
also run on Postgres when ``TWO_PEOPLE_DB_URL`` names a migrated one, whose tables they
empty: never point it at anything but a throwaway database.
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
from telegram.error import BadRequest, Forbidden, NetworkError

from app.core.config import settings
from app.crud.crud_morning_answer import crud_morning_answer
from app.crud.crud_subscription import crud_subscription
from app.db.base import Base
from app.models.meal import Meal
from app.models.morning_answer import MorningAnswer
from app.models.user import User
from app.services import close_row, morning, morning_push, reminders, settings_screen
from app.services import stats_screen as st
from app.services import telegram as tg
from app.services.settings_screen import WEEK_PUSH

ZONE = ZoneInfo("Asia/Tbilisi")
TODAY = date(2026, 11, 11)  # a Wednesday
YESTERDAY = date(2026, 11, 10)  # a Tuesday
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


def _utc(day, hour, minute=0, zone=ZONE):
    return datetime.combine(day, time(hour, minute), tzinfo=zone).astimezone(UTC)


AT_10 = _utc(TODAY, 10, 5)  # the first sweep inside the 10:00 hour


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
    engine = _engine(getattr(request, "param", "sqlite"))
    make = sessionmaker(bind=engine)
    with make() as session:
        session.add(User(id=1, telegram_id=42, settings={}))
        session.add(User(id=2, telegram_id=43, settings={}))
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


def _setting(db, key, value, user_id=1):
    user = _user(db, user_id)
    user.settings = {**(user.settings or {}), key: value}
    db.commit()


def _meal(db, day, hour=13, user_id=1, calories=600, **kw):
    when = _utc(day, hour)
    meal = Meal(
        user_id=user_id,
        description="суп",
        meal_time=when,
        sent_at=when,
        calories=calories,
        proteins=20,
        fats=10,
        carbohydrates=60,
        **kw,
    )
    db.add(meal)
    db.commit()
    return meal


def _usual(db, user_id=1):
    """A record yesterday: the question is due at 10:05 today."""
    return _meal(db, YESTERDAY, user_id=user_id)


def _bot(**kw):
    return SimpleNamespace(
        send_message=AsyncMock(
            side_effect=kw.get("error"),
            return_value=SimpleNamespace(message_id=kw.get("message_id", 900)),
        ),
        delete_message=AsyncMock(side_effect=kw.get("delete_error")),
        edit_message_reply_markup=AsyncMock(side_effect=kw.get("edit_error")),
    )


def _run(db, bot, now=AT_10):
    return asyncio.run(morning_push.run(db, bot, now))


def _labels(markup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]


def _callbacks(markup) -> list[str]:
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def _tap(db, data, now=AT_10, telegram_id=42):
    return morning.handle_callback(db, telegram_id, data, now=now)


def _asked(db, day=YESTERDAY, user_id=1, message_id=700, **kw):
    """A question as the sweep left it."""
    row = crud_morning_answer.claim(
        db, user_id=user_id, day=day, chat_id=42, now=_utc(day + timedelta(days=1), 10)
    )
    row.status = "sent"
    row.message_id = message_id
    for key, value in kw.items():
        setattr(row, key, value)
    db.commit()
    return row


# --- when it is due ---------------------------------------------------------------


def test_due_in_the_10_hour_about_yesterday_when_it_has_a_record(db):
    _usual(db)
    assert morning.due(db, _user(db), AT_10) == YESTERDAY
    assert morning.due(db, _user(db), _utc(TODAY, 10, 59)) == YESTERDAY


@pytest.mark.parametrize("hour, minute", [(9, 59), (11, 0), (21, 0), (0, 5)])
def test_not_due_outside_the_10_hour(db, hour, minute):
    _usual(db)
    assert morning.due(db, _user(db), _utc(TODAY, hour, minute)) is None


def test_the_hour_is_the_persons_own_zone(db):
    _usual(db)
    _setting(db, settings_screen.TIMEZONE, "Europe/London")
    # 10:05 in Tbilisi is 06:05 in London: not yet; and the day is London's date - 1.
    assert morning.due(db, _user(db), AT_10) is None
    london = ZoneInfo("Europe/London")
    day = YESTERDAY  # the meal at 13:00 Tbilisi is 09:00 London on the same date
    assert morning.due(db, _user(db), _utc(TODAY, 10, 5, london)) == day


def test_yesterday_ends_at_local_midnight(db):
    # Only a record at 00:30 today: that is today's, so yesterday has none.
    _meal(db, TODAY, hour=0)
    assert morning.due(db, _user(db), AT_10) is None
    _meal(db, YESTERDAY, hour=23)
    assert morning.due(db, _user(db), AT_10) == YESTERDAY


def test_yesterday_needs_a_record_a_real_one(db):
    assert morning.due(db, _user(db), AT_10) is None
    _meal(db, YESTERDAY, is_draft=True)
    _meal(db, YESTERDAY, deleted_at=_utc(YESTERDAY, 14))
    _meal(db, YESTERDAY - timedelta(days=1))  # the day before is not yesterday
    assert morning.due(db, _user(db), AT_10) is None
    _usual(db)
    assert morning.due(db, _user(db), AT_10) == YESTERDAY


def test_the_off_switch_and_the_evening_off_are_separate(db):
    _usual(db)
    _setting(db, morning.MORNING_ON, False)
    assert morning.due(db, _user(db), AT_10) is None
    _setting(db, morning.MORNING_ON, True)
    assert morning.due(db, _user(db), AT_10) == YESTERDAY
    # D12: the evening «✖ Выкл» does not stop it.
    _setting(db, reminders.KEY, {"mode": "off"})
    assert morning.due(db, _user(db), AT_10) == YESTERDAY
    assert morning.morning_on(None) is True


def test_someone_who_may_not_receive_gets_nothing(db, monkeypatch):
    _usual(db)
    monkeypatch.setattr(
        crud_subscription, "is_active_subscription", lambda _db, _id: False
    )
    assert morning.due(db, _user(db), AT_10) is None
    assert _run(db, _bot())["sent"] == 0


# --- the send ---------------------------------------------------------------------


@BACKENDS
def test_it_goes_once_silently_with_the_exact_text(db):
    _usual(db)
    bot = _bot()
    assert _run(db, bot)["sent"] == 1
    assert _run(db, bot, AT_10 + timedelta(minutes=15))["sent"] == 0
    assert bot.send_message.await_count == 1
    kw = bot.send_message.await_args.kwargs
    assert kw["chat_id"] == 42
    assert kw["disable_notification"] is True
    assert kw["text"] == (
        "☀️ <b>Записи за вчера, вт 10 ноября</b>\n"
        "Вчерашняя еда вся в записях?\n"
        "Можно не отвечать — тогда день считается как обычно. Да или частично — тоже.\n"
        "Почти нет — вторник не войдёт в средние за 7 дней, а в итогах будет с пометкой."
    )
    row = crud_morning_answer.for_day(db, 1, YESTERDAY)
    assert (row.status, row.message_id, row.answer) == ("sent", 900, None)
    rows = kw["reply_markup"].inline_keyboard
    assert [[b.text for b in r] for r in rows] == [
        ["👍 Да", "✍️ Частично", "✋ Почти нет"],
        ["✖ Не спрашивать утром"],
    ]
    assert _callbacks(kw["reply_markup"]) == [
        f"mq:a:{row.id}:all",
        f"mq:a:{row.id}:part",
        f"mq:a:{row.id}:little",
        f"mq:x:{row.id}",
    ]
    assert all(len(c.encode()) <= 64 for c in _callbacks(kw["reply_markup"]))


def test_never_later_that_day(db):
    _usual(db)
    bot = _bot()
    assert _run(db, bot, _utc(TODAY, 11, 5))["sent"] == 0
    bot.send_message.assert_not_awaited()


def test_the_next_day_asks_about_the_next_day(db):
    _usual(db)
    _meal(db, TODAY)
    bot = _bot()
    _run(db, bot)
    _run(db, bot, _utc(TODAY + timedelta(days=1), 10, 5))
    days = [r.day for r in db.query(MorningAnswer).order_by(MorningAnswer.id)]
    assert days == [YESTERDAY, TODAY]


def test_a_person_with_nothing_yesterday_is_not_asked(db):
    bot = _bot()
    assert _run(db, bot)["sent"] == 0
    assert db.query(MorningAnswer).count() == 0


@BACKENDS
def test_blocked_is_kept_as_blocked_and_not_retried(db):
    _usual(db)
    bot = _bot(error=Forbidden("blocked by the user"))
    assert _run(db, bot)["blocked"] == 1
    assert crud_morning_answer.for_day(db, 1, YESTERDAY).status == "blocked"
    assert _run(db, _bot(), AT_10 + timedelta(minutes=15))["sent"] == 0


@BACKENDS
def test_may_have_arrived_is_kept_as_maybe(db):
    _usual(db)
    error = NetworkError("httpx.ReadTimeout: no answer")
    error.__cause__ = httpx.ReadTimeout("no answer")
    assert _run(db, _bot(error=error))["failed"] == 1
    assert crud_morning_answer.for_day(db, 1, YESTERDAY).status == "maybe"
    assert _run(db, _bot(), AT_10 + timedelta(minutes=15))["sent"] == 0


@BACKENDS
def test_certainly_not_sent_clears_the_claim_and_the_next_sweep_retries(db):
    _usual(db)
    error = NetworkError("httpx.ConnectError: no route")
    error.__cause__ = httpx.ConnectError("no route")
    assert _run(db, _bot(error=error))["failed"] == 1
    assert crud_morning_answer.for_day(db, 1, YESTERDAY) is None
    bot = _bot()
    assert _run(db, bot, AT_10 + timedelta(minutes=15))["sent"] == 1


@BACKENDS
def test_a_claimed_day_is_not_sent_twice(db):
    _usual(db)
    crud_morning_answer.claim(db, user_id=1, day=YESTERDAY, chat_id=42, now=AT_10)
    bot = _bot()
    assert _run(db, bot)["sent"] == 0
    bot.send_message.assert_not_awaited()
    assert (
        crud_morning_answer.claim(db, user_id=1, day=YESTERDAY, chat_id=42, now=AT_10)
        is None
    )


def test_one_persons_bad_row_does_not_cost_the_others(db, monkeypatch):
    _usual(db)
    _usual(db, user_id=2)
    real = morning.due

    def due(db_, user, now):
        if user.id == 1:
            raise RuntimeError("bad data")
        return real(db_, user, now)

    monkeypatch.setattr(morning, "due", due)
    bot = _bot()
    assert _run(db, bot)["sent"] == 1
    assert bot.send_message.await_args.kwargs["chat_id"] == 43


# --- at most one live question ----------------------------------------------------


def test_a_young_unanswered_one_is_deleted_when_a_new_one_goes_out(db):
    old = _asked(db, YESTERDAY - timedelta(days=1), message_id=700)
    old.sent_at = AT_10 - timedelta(hours=24)
    db.commit()
    _usual(db)
    bot = _bot()
    _run(db, bot)
    bot.delete_message.assert_awaited_once_with(42, 700)
    bot.edit_message_reply_markup.assert_not_awaited()


def test_an_old_unanswered_one_loses_its_buttons(db):
    old = _asked(db, YESTERDAY - timedelta(days=1), message_id=700)
    old.sent_at = AT_10 - timedelta(days=5)
    db.commit()
    _usual(db)
    bot = _bot()
    _run(db, bot)
    bot.delete_message.assert_not_awaited()
    kw = bot.edit_message_reply_markup.await_args.kwargs
    assert (kw["message_id"], kw["reply_markup"]) == (700, None)


def test_an_answered_one_keeps_its_text_and_loses_its_buttons(db):
    old = _asked(
        db, YESTERDAY - timedelta(days=1), message_id=700, answer=morning.LITTLE
    )
    old.sent_at = AT_10 - timedelta(hours=24)
    db.commit()
    _usual(db)
    bot = _bot()
    _run(db, bot)
    bot.delete_message.assert_not_awaited()
    assert bot.edit_message_reply_markup.await_args.kwargs["message_id"] == 700


def test_a_message_that_cannot_be_deleted_loses_its_buttons_instead(db):
    old = _asked(db, YESTERDAY - timedelta(days=1), message_id=700)
    old.sent_at = AT_10 - timedelta(hours=24)
    db.commit()
    _usual(db)
    bot = _bot(delete_error=BadRequest("Message can't be deleted"))
    assert _run(db, bot)["sent"] == 1
    assert bot.edit_message_reply_markup.await_args.kwargs["message_id"] == 700


def test_a_row_without_a_message_is_walked_past(db):
    behind = _asked(db, YESTERDAY - timedelta(days=2), message_id=650)
    behind.sent_at = AT_10 - timedelta(hours=40)
    _asked(db, YESTERDAY - timedelta(days=1), message_id=None, status="maybe")
    db.commit()
    _usual(db)
    bot = _bot()
    _run(db, bot)
    bot.delete_message.assert_awaited_once_with(42, 650)


# --- the taps ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "code, said, usual",
    [("all", "да", True), ("part", "частично", True), ("little", "почти нет", False)],
)
def test_an_answer_edits_the_message_with_the_d14_text(db, code, said, usual):
    row = _asked(db)
    screen = _tap(db, f"mq:a:{row.id}:{code}")
    if usual:
        assert screen.text == f"✓ Ответ сохранён: {said}. Вторник считается как обычно."
    else:
        assert screen.text == (
            "✓ Ответ сохранён: почти нет. Вторник не войдёт в средние за 7 дней, "
            "а в итогах будет с пометкой. Записи за него остаются в дневнике."
        )
    assert not screen.toast  # an empty answer(): no «Принято» toast
    assert _labels(screen.markup) == ["✅ ОК", "✏️ Изменить ответ"]
    assert _callbacks(screen.markup)[0] == close_row.CALLBACK
    db.refresh(row)
    assert row.answer == code and row.answered_at is not None


def test_change_brings_the_three_answers_back_and_a_new_answer_replaces(db):
    row = _asked(db)
    _tap(db, f"mq:a:{row.id}:little")
    screen = _tap(db, f"mq:c:{row.id}")
    assert _labels(screen.markup)[:3] == ["👍 Да", "✍️ Частично", "✋ Почти нет"]
    db.refresh(row)
    assert row.answer == "little"  # still held until a new one is tapped
    assert morning.little_days(db, _user(db), YESTERDAY, YESTERDAY) == {YESTERDAY}
    _tap(db, f"mq:a:{row.id}:all")
    assert morning.little_days(db, _user(db), YESTERDAY, YESTERDAY) == set()


def test_change_on_an_answered_question_says_what_is_kept(db):
    row = _asked(db)
    first = _tap(db, f"mq:c:{row.id}")  # never answered: the question as sent
    assert "Можно не отвечать" in first.text
    _tap(db, f"mq:a:{row.id}:little")
    again = _tap(db, f"mq:c:{row.id}").text
    assert "Можно не отвечать" not in again
    assert "Сейчас в ответе: почти нет. Он остаётся, пока не выбран другой." in again
    assert "Да или частично — день считается как обычно." in again
    assert "Почти нет — вторник не войдёт в средние за 7 дней" in again


def test_stop_with_the_evening_off_does_not_promise_the_evening(db):
    _setting(db, reminders.KEY, {"mode": "off"})
    row = _asked(db)
    text = _tap(db, f"mq:x:{row.id}").text
    assert "Вечерние напоминания" not in text
    assert text == morning.STOPPED_NO_EVENING
    assert text.startswith("✓ Утром больше не спрашиваю") and "☀️ Вопрос утром." in text


def test_return_before_10_says_today(db):
    row = _asked(db)
    _tap(db, f"mq:x:{row.id}")
    early = _tap(db, f"mq:r:{row.id}", now=_utc(TODAY, 9, 30)).text
    assert early == (
        "✓ Вопрос утром включён: сегодня в 10:00, если за вчера есть записи."
    )
    late = _tap(db, f"mq:r:{row.id}", now=_utc(TODAY, 11)).text
    assert late == morning.RETURNED


def test_ok_is_the_shared_close_row(db):
    row = _asked(db)
    for data in (
        _tap(db, f"mq:a:{row.id}:all").markup,
        _tap(db, f"mq:x:{row.id}").markup,
    ):
        assert data.inline_keyboard[0][0].callback_data == "ok:close"
        assert re.fullmatch(close_row.PATTERN, "ok:close")


def test_stop_turns_it_off_and_return_turns_it_on(db):
    _usual(db)
    row = _asked(db)
    screen = _tap(db, f"mq:x:{row.id}")
    assert screen.text == (
        "✓ Утром больше не спрашиваю, сколько записано за вчера. Вечерние напоминания "
        "остаются. Включить потом — ⚙️ Настройки → 🔔 Напоминания → ☀️ Вопрос утром."
    )
    assert _labels(screen.markup) == ["✅ ОК", "☀️ Вернуть вопрос"]
    assert morning.morning_on(_user(db)) is False
    assert morning.due(db, _user(db), AT_10 + timedelta(days=1)) is None
    # the evening reminder is untouched
    assert reminders.config(_user(db)).mode == reminders.EVENING
    back = _tap(db, f"mq:r:{row.id}")
    assert back.text == (
        "✓ Вопрос утром включён: завтра в 10:00, если за сегодня будут записи."
    )
    assert _labels(back.markup) == ["✅ ОК"]
    assert morning.morning_on(_user(db)) is True


def test_stop_after_an_answer_keeps_the_answer(db):
    row = _asked(db)
    _tap(db, f"mq:a:{row.id}:little")
    _tap(db, f"mq:c:{row.id}")
    _tap(db, f"mq:x:{row.id}")
    db.refresh(row)
    assert row.answer == "little"
    # and a stop before any answer is not an answer the averages read
    other = _asked(db, YESTERDAY - timedelta(days=3), message_id=701)
    _tap(db, f"mq:x:{other.id}")
    db.refresh(other)
    assert other.answer == morning.STOP
    assert morning.little_days(db, _user(db), YESTERDAY - timedelta(days=9), TODAY) == {
        YESTERDAY
    }


@pytest.mark.parametrize(
    "data",
    [
        "mq:",
        "mq:a",
        "mq:a:x:all",
        "mq:a:1",
        "mq:a:1:most",
        "mq:a:99999999999999999999:all",
        "mq:z:1",
        "mq:c:-1",
        "mq:x:1:2",
        "mq:r:",
    ],
)
def test_malformed_and_unknown_go_to_the_stale_path(db, data):
    row = _asked(db)
    screen = _tap(db, data.replace(":1", f":{row.id}", 1) if data == "mq:z:1" else data)
    assert screen.text  # today's card, not a crash
    assert "Ответ сохранён" not in screen.text
    db.refresh(row)
    assert row.answer is None and morning.morning_on(_user(db)) is True


def test_a_foreign_row_is_refused_and_changes_nothing(db):
    mine = _asked(db)
    theirs = _asked(db, user_id=2, message_id=701)
    for verb in ("a:{}:little", "c:{}", "x:{}", "r:{}"):
        screen = _tap(db, "mq:" + verb.format(theirs.id))
        assert "Ответ сохранён" not in screen.text
    db.refresh(theirs)
    assert theirs.answer is None
    assert morning.morning_on(_user(db, 2)) is True and morning.morning_on(_user(db))
    db.refresh(mine)
    assert mine.answer is None
    # a row that does not exist, and a tapper who is nobody
    assert "Ответ сохранён" not in _tap(db, "mq:a:99999:all").text
    assert _tap(db, f"mq:x:{mine.id}", telegram_id=999).text


def test_the_callback_answers_empty_and_edits_the_message(db, factory):
    row = _asked(db)
    query = SimpleNamespace(
        data=f"mq:a:{row.id}:part",
        message=SimpleNamespace(message_id=700, chat_id=42),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    update = SimpleNamespace(
        callback_query=query, effective_user=SimpleNamespace(id=42)
    )
    asyncio.run(tg.morning_callback(update, SimpleNamespace()))
    query.answer.assert_awaited_once()
    assert not query.answer.await_args.args[0]  # empty, no toast
    text = query.edit_message_text.await_args.args[0]
    assert text.startswith("✓ Ответ сохранён: частично.")


def test_the_prefix_is_new_and_routed():
    assert morning.CALLBACK_PREFIX == "mq:"
    source = (ROOT / "app/services/telegram.py").read_text()
    assert 'morning_callback, pattern=f"^{morning.CALLBACK_PREFIX}"' in source
    prefixes = []
    for path in (ROOT / "app/services").glob("*.py"):
        prefixes += re.findall(
            r'^CALLBACK_PREFIX\s*=\s*"([^"]+)"', path.read_text(), re.M
        )
    assert prefixes.count("mq:") == 1
    assert len(prefixes) > 8  # the scan did read the modules


# --- the 🔔 screen ----------------------------------------------------------------


def _settings(db):
    return reminders.handle_callback(db, 42, "rm:o", now=AT_10)


def test_the_settings_screen_has_the_line_the_sentence_and_the_button(db):
    screen = _settings(db)
    assert "«✖ Выкл» выключает только напоминание.\n" in screen.text
    assert "приходит итог недели. «✖ Выкл» выключает только напоминание." in screen.text
    _setting(db, WEEK_PUSH, False)
    assert (
        "оно не приходит. «✖ Выкл» выключает только напоминание." in _settings(db).text
    )
    assert "☀️ Утром, в 10:00 — вопрос про вчерашние записи: присылаю." in screen.text
    assert "☀️ Вопрос утром: вкл" in _labels(screen.markup)
    assert "rm:morning" in _callbacks(screen.markup)
    # the button itself, tapped, flips the switch (not only the verb by its name)
    (button,) = [
        b for r in screen.markup.inline_keyboard for b in r if "утром" in b.text
    ]
    reminders.handle_callback(db, 42, button.callback_data, now=AT_10)
    assert morning.morning_on(_user(db)) is False
    reminders.handle_callback(db, 42, button.callback_data, now=AT_10)
    # five action rows: the count the owner accepted (TD-262 reviews the screen)
    assert len(screen.markup.inline_keyboard) == 6  # 5 action rows + «⬅️ Настройки»


def test_the_switch_toggles_and_the_screen_says_so(db):
    screen = reminders.handle_callback(db, 42, "rm:morning", now=AT_10)
    assert "не присылаю." in screen.text.split("вчерашние записи:")[1].split("\n")[0]
    assert "☀️ Вопрос утром: выкл" in _labels(screen.markup)
    assert morning.morning_on(_user(db)) is False
    assert "✓ Вопрос утром выключен" in screen.text
    screen = reminders.handle_callback(db, 42, "rm:morning", now=AT_10)
    assert "☀️ Вопрос утром: вкл" in _labels(screen.markup)
    assert morning.morning_on(_user(db)) is True


def test_the_switch_does_not_touch_the_evening_or_the_other_switches(db):
    reminders.handle_callback(db, 42, "rm:morning", now=AT_10)
    user = _user(db)
    assert reminders.config(user).mode == reminders.EVENING
    assert settings_screen.week_push_on(user) is True


def test_the_screen_with_evening_off_still_has_the_morning_switch(db):
    reminders.handle_callback(db, 42, "rm:m:off", now=AT_10)
    screen = _settings(db)
    assert "☀️ Вопрос утром: вкл" in _labels(screen.markup)
    assert len(screen.markup.inline_keyboard) == 5


# --- the 7-day numbers ------------------------------------------------------------

MONDAY = date(2026, 11, 2)
SUNDAY = _utc(date(2026, 11, 8), 22, 5)


def _week(db):
    _meal(db, date(2026, 11, 3), calories=300)  # Tuesday: a small part of the day
    _meal(db, date(2026, 11, 4), calories=2000)  # Wednesday
    _meal(db, date(2026, 11, 5), calories=1800)  # Thursday


def _answer(db, day, code="little"):
    _asked(db, day, message_id=800 + day.day, answer=code)


def test_no_answers_leaves_the_numbers_as_they_were(db):
    _week(db)
    before = st.summary_view(db, _user(db), MONDAY, SUNDAY, opened=True)
    _answer(db, date(2026, 11, 3), "all")
    _answer(db, date(2026, 11, 4), "part")
    after = st.summary_view(db, _user(db), MONDAY, SUNDAY, opened=True)
    assert after.text == before.text
    assert "отдельно" not in after.text
    assert "вт " in before.text and "ср " in before.text


def test_an_excluded_day_is_out_of_the_average_and_has_no_bar(db):
    _week(db)
    plain = st.summary_view(db, _user(db), MONDAY, SUNDAY, opened=True).text
    _answer(db, date(2026, 11, 3))
    text = st.summary_view(db, _user(db), MONDAY, SUNDAY, opened=True).text
    # D14: with a day left out, the heading names the days averaged
    assert "🔢 В среднем за день (ср, чт): " in text
    assert "с записями" not in text and "с записями" in plain
    # (300 + 2000 + 1800) / 3 before; (2000 + 1800) / 2 = 1900 after
    assert "1 900" in text or "1900" in text
    assert "1 367" in plain or "1367" in plain
    bars = [line for line in text.splitlines() if re.match(r"(пн|вт|ср|чт|пт) ", line)]
    assert [line[:2] for line in bars] == ["ср", "чт"]
    assert text.rstrip().endswith("Вторник отдельно: в записях только часть дня.")


def test_two_days_and_the_names(db):
    _week(db)
    _answer(db, date(2026, 11, 3))
    _answer(db, date(2026, 11, 5))
    text = st.summary_view(db, _user(db), MONDAY, SUNDAY, opened=True).text
    assert "Вторник и четверг отдельно: в записях только часть дня." in text
    assert st.little_note([date(2026, 11, d) for d in (3, 4, 5)]) == (
        "Вторник, среда и четверг отдельно: в записях только часть дня."
    )
    assert st.little_note([date(2026, 11, 8)]).startswith("Воскресенье отдельно")


def test_all_days_excluded_still_works(db):
    _week(db)
    for d in (3, 4, 5):
        _answer(db, date(2026, 11, d))
    text = st.summary_view(db, _user(db), MONDAY, SUNDAY, opened=True).text
    assert "В среднем за день с записями: не посчитано" in text
    assert "Вторник, среда и четверг отдельно" in text


def test_the_mark_is_in_the_summary_with_the_numbers_folded_or_hidden(db):
    _week(db)
    _answer(db, date(2026, 11, 3))
    note = "Вторник отдельно: в записях только часть дня."
    hidden = st.summary_view(db, _user(db), MONDAY, SUNDAY)  # opened=False
    assert "🔢" not in hidden.text and note in hidden.text
    folded = st.summary_view(db, _user(db), MONDAY, SUNDAY, opened=True)
    assert folded.text.count(note) == 1
    # outside the expandable block, which Telegram folds to its first lines
    assert folded.text.index(note) > folded.text.index("</blockquote>")
    # the picture card is unchanged
    _answer_free = st.summary_view(db, _user(db), MONDAY, SUNDAY).picture
    assert "отдельно" not in repr(_answer_free)


def test_the_seven_day_tab_reads_the_same_answers(db):
    _meal(db, TODAY - timedelta(days=3), calories=300)
    _meal(db, TODAY - timedelta(days=2), calories=2000)
    _answer(db, TODAY - timedelta(days=3))
    screen = st.open_view(db, 42, st.WEEK, AT_10, True)
    # TODAY - 3 is Sunday 8 November; Monday alone is averaged
    assert "Воскресенье отдельно: в записях только часть дня." in screen.text
    assert "🔢 В среднем за день (пн): " in screen.text
    assert "300" not in screen.text


def test_answers_of_other_people_and_other_weeks_do_not_leak(db):
    _week(db)
    _asked(db, date(2026, 11, 3), user_id=2, message_id=702, answer="little")
    _answer(db, date(2026, 10, 27))
    text = st.summary_view(db, _user(db), MONDAY, SUNDAY, opened=True).text
    assert "отдельно" not in text


# --- the migration, the task, the words -------------------------------------------


def test_the_task_and_the_beat_entry_are_registered():
    from celery_app.celery_app import app

    entry = app.conf.beat_schedule["morning-question"]
    assert entry["task"] == "celery_app.tasks.morning.send_morning_questions"
    assert "celery_app.tasks.morning" in app.conf.include


def test_one_alembic_head_and_it_is_ours():
    versions = ROOT / "alembic/versions"
    revisions, downs = {}, set()
    for path in versions.glob("*.py"):
        text = path.read_text()
        match = re.search(r"^revision(?:: str)?\s*=\s*[\"']([^\"']+)", text, re.M)
        down = re.search(r"^down_revision[^=]*=\s*(.+)$", text, re.M)
        revisions[match.group(1)] = path
        downs |= set(re.findall(r"[\"']([^\"']+)[\"']", down.group(1)))
    heads = [rev for rev in revisions if rev not in downs]
    assert heads == ["a1b2c3d4e5f6"]


_BANNED = re.compile(
    r"\bнадо\b|нужно|пора\b|хорошо|плохо|осталось|забыл|пропуск|пропущен"
    r"|не записано|ещё не|еще не|пусто|снова|опять|\bвы\b|\bвам\b|\bваш",
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
    texts = _literals(ROOT / "app/services/morning.py")
    row = _asked(db)
    texts.append(morning.question_view(row).text)
    for code in ("all", "part", "little"):
        texts.append(_tap(db, f"mq:a:{row.id}:{code}").text)
    texts += [st.little_note([YESTERDAY]), _settings(db).text]
    assert len(texts) > 15
    assert [t for t in texts if _BANNED.search(t)] == []
    assert [t for t in texts if _GENDERED.search(t) and "ла" in t] == []
    # the control: the same checks catch a banned word and a gendered form
    assert _BANNED.search("Тебе нужно записать")
    assert _GENDERED.search("Ты забыла")
