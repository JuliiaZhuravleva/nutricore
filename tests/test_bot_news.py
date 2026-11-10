"""✨ Что нового: quiet, once, in daytime, on by default, off in 🔔 (owner 2026-11-10)."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker
from telegram.error import Forbidden, NetworkError

from app.core.config import settings
from app.crud.crud_subscription import crud_subscription
from app.db.base_class import Base
from app.models.user import User
from app.services import bot_news, reminders
from app.services.bot_news import NEWS_ON, NEWS_SEEN, News

ITEM = News(id="2026-11-11-test", published=date(2026, 11, 11), text="Новое: <b>x</b>")
# Wed 11 Nov 2026, 12:00 in Tbilisi (UTC+4).
NOON = datetime(2026, 11, 11, 8, 0, tzinfo=UTC)


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(
        crud_subscription, "is_active_subscription", lambda _db, _id: True
    )
    monkeypatch.setattr(settings, "DEFAULT_TIMEZONE", "Asia/Tbilisi")
    monkeypatch.setattr(settings, "BOT_ACCESS_MODE", "open", raising=False)
    monkeypatch.setattr(bot_news, "NEWS", (ITEM,))
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine)() as session:
        session.add(
            User(id=1, telegram_id=42, settings={}, created_at=datetime(2026, 10, 1))
        )
        session.commit()
        yield session
    engine.dispose()


def _bot():
    return SimpleNamespace(send_message=AsyncMock())


def _run(db, bot, now=NOON):
    return asyncio.run(bot_news.run(db, bot, now))


def _user(db) -> User:
    db.expire_all()
    return db.get(User, 1)


def _set(db, **values) -> None:
    user = _user(db)
    user.settings = {**(user.settings or {}), **values}
    db.commit()


def test_the_news_goes_once_silently_with_a_way_to_turn_it_off(db):
    bot = _bot()
    assert _run(db, bot)["sent"] == 1
    assert _run(db, bot, NOON + timedelta(minutes=15))["sent"] == 0
    assert bot.send_message.await_count == 1
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == 42
    assert kwargs["disable_notification"] is True
    assert kwargs["text"].startswith("<b>✨ Что нового</b>\n\nНовое: <b>x</b>")
    assert "без звука" in kwargs["text"]
    buttons = [b for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert [b.callback_data for b in buttons] == ["ok:close", reminders.OPEN]
    assert _user(db).settings[NEWS_SEEN] == ITEM.id


def test_on_by_default_and_off_when_turned_off(db):
    assert bot_news.news_on(_user(db)) is True
    assert bot_news.news_on(None) is True
    _set(db, **{NEWS_ON: False})
    bot = _bot()
    assert _run(db, bot)["sent"] == 0
    bot.send_message.assert_not_awaited()


@pytest.mark.parametrize(
    "local_hour, sent",
    [(9, 0), (10, 1), (20, 1), (21, 0), (3, 0)],
)
def test_only_in_the_persons_daytime(db, local_hour, sent):
    now = datetime(2026, 11, 11, local_hour, 30, tzinfo=UTC) - timedelta(hours=4)
    assert _run(db, _bot(), now)["sent"] == sent


def test_the_daytime_is_the_persons_own_clock(db):
    _set(db, timezone="Europe/London")  # 08:00 there at NOON in Tbilisi
    assert _run(db, _bot())["sent"] == 0
    assert _run(db, _bot(), NOON + timedelta(hours=3))["sent"] == 1


def test_an_old_news_is_not_sent_to_someone_back_after_a_month(db):
    later = NOON + timedelta(days=bot_news.FRESH_DAYS + 1)
    assert bot_news.latest(later.date()) is None
    assert _run(db, _bot(), later)["sent"] == 0
    assert _run(db, _bot(), NOON + timedelta(days=bot_news.FRESH_DAYS))["sent"] == 1


def test_a_news_published_later_waits_for_its_day(db):
    assert _run(db, _bot(), NOON - timedelta(days=1))["sent"] == 0


def test_someone_who_came_after_the_news_is_not_told_of_it(db):
    db.execute(update(User).values(created_at=datetime(2026, 11, 12, 9)))
    db.commit()
    assert _run(db, _bot(), NOON + timedelta(days=2))["sent"] == 0


def test_only_the_newest_goes_and_a_new_one_goes_after_the_old(db, monkeypatch):
    newer = News(id="2026-11-12-test", published=date(2026, 11, 12), text="Ещё")
    monkeypatch.setattr(bot_news, "NEWS", (ITEM, newer))
    bot = _bot()
    assert _run(db, bot, NOON + timedelta(days=1))["sent"] == 1
    assert "Ещё" in bot.send_message.await_args.kwargs["text"]
    assert _user(db).settings[NEWS_SEEN] == newer.id
    assert _run(db, bot, NOON + timedelta(days=1, hours=1))["sent"] == 0


def test_someone_who_may_not_receive_gets_nothing(db, monkeypatch):
    monkeypatch.setattr(
        crud_subscription, "is_active_subscription", lambda _db, _id: False
    )
    assert _run(db, _bot())["sent"] == 0


def test_a_run_left_queued_finds_the_first_runs_mark(db, monkeypatch):
    stale = bot_news.users_due(db, NOON)
    assert len(stale) == 1
    monkeypatch.setattr(bot_news, "users_due", lambda _db, _now: stale)
    db.execute(update(User).values(settings={NEWS_SEEN: ITEM.id}))
    db.commit()
    bot = _bot()
    assert _run(db, bot) == {"sent": 0, "failed": 0, "blocked": 0}
    bot.send_message.assert_not_awaited()


def test_a_send_that_never_left_is_tried_again(db):
    error = NetworkError("httpx.ConnectError: no route")
    error.__cause__ = httpx.ConnectError("no route")
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=error))
    assert _run(db, bot)["failed"] == 1
    assert NEWS_SEEN not in (_user(db).settings or {})
    bot.send_message = AsyncMock()
    assert _run(db, bot, NOON + timedelta(minutes=15))["sent"] == 1


def test_a_send_that_may_have_arrived_is_not_repeated(db):
    error = NetworkError("read timeout")
    error.__cause__ = httpx.ReadTimeout("slow")
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=error))
    assert _run(db, bot)["failed"] == 1
    assert _user(db).settings[NEWS_SEEN] == ITEM.id


def test_a_blocked_bot_is_counted_and_not_retried(db):
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=Forbidden("blocked")))
    assert _run(db, bot)["blocked"] == 1
    assert _user(db).settings[NEWS_SEEN] == ITEM.id


def test_the_switch_lives_in_reminders_and_says_so(db):
    user = _user(db)
    view = reminders.settings_view(db, user, NOON)
    assert "✨ Новости бота: после больших обновлений, без звука." in view.text
    labels = [b.text for row in view.markup.inline_keyboard for b in row]
    assert "✨ Новости: вкл" in labels
    off = reminders.handle_callback(db, 42, "rm:news", NOON)
    assert off.text.startswith("<i>✓ Новости бота выключены</i>")
    assert "✨ Новости бота: не присылаю." in off.text
    assert _user(db).settings[NEWS_ON] is False
    on = reminders.handle_callback(db, 42, "rm:news", NOON)
    assert on.text.startswith("<i>✓ Новости бота включены</i>")
    assert _user(db).settings[NEWS_ON] is True


def test_the_shipped_news_are_well_formed():
    ids = [news.id for news in bot_news.NEWS]
    assert len(ids) == len(set(ids))
    dates = [news.published for news in bot_news.NEWS]
    assert dates == sorted(dates)
    for news in bot_news.NEWS:
        assert news.id.startswith(news.published.isoformat())
        view = bot_news.screen(news)
        assert len(view.text) <= 4096
        # Gender-neutral (G5-R19): no past tense that says who the person is.
        for word in ("сделала", "сделал ", "записала", "сказала"):
            assert word not in view.text


def test_beat_runs_the_send_often_enough_for_every_zone():
    from celery_app.celery_app import app as celery

    entry = celery.conf.beat_schedule["bot-news"]
    assert entry["task"] == "celery_app.tasks.bot_news.send_news"
    assert entry["schedule"].minute == {0, 15, 30, 45}
    assert entry["options"]["expires"] < 15 * 60
    assert "celery_app.tasks.bot_news" in celery.conf.include
    from celery_app.tasks import bot_news as task

    assert task.send_news.name == "celery_app.tasks.bot_news.send_news"
