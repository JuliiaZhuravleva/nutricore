"""✨ Что нового: one quiet message after a big release (owner 2026-11-10).

    the news → <b>✨ Что нового</b>, a few lines, a quiet note how to turn it off,
      [✅ ОК] [⚙️ Настроить]; ⚙️ opens 🔔 Напоминания, where «✨ Новости» flips it

On for everyone unless turned off (the owner: «по дефолту присылаем»), silent always.
A news item is written into ``NEWS`` in the release branch that ships what it tells;
the release deploys it and the next run of the task sends it. Only the newest item
goes, once per person, between FROM_HOUR and TO_HOUR on their own clock, and only while
it is fresh: someone back after a month hears nothing about an old release. Someone who
came after it was published is not told about it either: to them it is just the bot.

As the week's summary: marked before it is sent under a row lock, so overlapping runs
send it once; a failure that certainly did not arrive clears the mark for the next run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import Forbidden

from app.models.user import User
from app.services.access_control import may_receive
from app.services.card_delivery import may_have_arrived
from app.services.close_row import ok_row
from app.services.day_card import local_time
from app.services.favorites import Screen
from app.services.settings_screen import user_zone

logger = logging.getLogger(__name__)

NEWS_ON = "news"  # False: no news; on unless turned off
NEWS_SEEN = "news_seen"  # the id of the last news item sent
FROM_HOUR = 10  # local hours a news item may go out in: [FROM_HOUR, TO_HOUR)
TO_HOUR = 21
FRESH_DAYS = 14
SETTINGS_OPEN = "rm:o"  # == reminders.OPEN, which imports this module
TITLE = "<b>✨ Что нового</b>"
FOOT = (
    "<i>Такие сообщения приходят без звука и только после больших обновлений. "
    "Выключить: ⚙️ Настроить.</i>"
)


@dataclass(frozen=True)
class News:
    id: str  # unique and never reused: «2026-11-11-diary»
    published: date
    text: str  # HTML; short, gender-neutral, nothing about any one person


# Oldest first. Add one item per big release, in the branch that ships it.
NEWS: tuple[News, ...] = (
    News(
        id="2026-11-11-diary",
        published=date(2026, 11, 11),
        text=(
            "🗒 <b>Дневник дня.</b> Команда /diary показывает еду за день прямо в "
            "сообщении. Нажми на приём, чтобы увидеть его блюда; ‹ › листают дни. "
            "Нужен свежий Telegram: 13.2 и новее на телефоне, Telegram Desktop 7.4 "
            "и новее на компьютере. Если вместо дневника написано, что сообщение не "
            "поддерживается, обнови приложение.\n\n"
            "🍲 <b>Названия блюд.</b> Блюдо теперь называется коротко и одинаково, "
            "например «гречка с курицей», а не всей фразой, как было сказано. "
            "Сказанное остаётся в записи."
        ),
    ),
)


def news_on(user: User | None) -> bool:
    return (user.settings or {}).get(NEWS_ON) is not False if user else True


def latest(today: date) -> News | None:
    """The newest item published by ``today`` and still fresh."""
    for news in reversed(NEWS):
        if news.published <= today:
            return news if today - news.published <= timedelta(FRESH_DAYS) else None
    return None


def screen(news: News) -> Screen:
    text = "\n\n".join((TITLE, news.text, FOOT))
    rows = [
        ok_row() + [InlineKeyboardButton("⚙️ Настроить", callback_data=SETTINGS_OPEN)]
    ]
    return Screen(text, InlineKeyboardMarkup(rows))


def _joined_after(user: User, news: News) -> bool:
    created = user.created_at
    if created is None:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return created.date() > news.published


def _due(db, user: User, now: datetime) -> News | None:
    if not news_on(user) or not may_receive(db, user.telegram_id):
        return None
    local = local_time(now, user_zone(user))
    news = latest(local.date())
    if news is None or (user.settings or {}).get(NEWS_SEEN) == news.id:
        return None
    if _joined_after(user, news) or not FROM_HOUR <= local.hour < TO_HOUR:
        return None
    return news


def users_due(db, now: datetime) -> list:
    """(user, news) for everyone whose news goes out now. One user's bad data must
    not cost everyone else theirs."""
    due = []
    for user in db.query(User).order_by(User.id).all():
        telegram_id = user.telegram_id
        try:
            news = _due(db, user, now)
        except Exception:
            logger.exception("news: could not check %s", telegram_id)
            db.rollback()
            continue
        if news is not None:
            due.append((user, news))
    return due


def _mark(db, user: User, value: str | None) -> None:
    # A new dict: SQLAlchemy does not see a JSON value changed in place.
    values = {**(user.settings or {}), NEWS_SEEN: value}
    if value is None:
        del values[NEWS_SEEN]
    user.settings = values
    db.commit()


def _claim(db, user: User, news: News) -> tuple[bool, str | None]:
    """Mark the item under a row lock, reading the mark afresh: runs left queued by
    a restart overlap, and the second must find the first one's mark."""
    db.execute(
        select(User)
        .where(User.id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    previous = (user.settings or {}).get(NEWS_SEEN)
    if previous == news.id or not news_on(user):
        db.rollback()
        return False, previous
    _mark(db, user, news.id)
    return True, previous


async def _send(db, bot, user: User, news: News) -> str:
    telegram_id = user.telegram_id
    previous = None
    claimed = False
    try:
        claimed, previous = _claim(db, user, news)
        if not claimed:
            return "skipped"
        view = screen(news)
        await bot.send_message(
            chat_id=telegram_id,
            text=view.text,
            parse_mode=ParseMode.HTML,
            reply_markup=view.markup,
            disable_notification=True,
        )
    except Forbidden:
        logger.info("news: %s blocked the bot", telegram_id)
        return "blocked"
    except Exception as error:
        if may_have_arrived(error):
            logger.warning("news: no answer for %s, may have arrived", telegram_id)
            return "failed"
        logger.exception("news: failed for %s, next run retries", telegram_id)
        try:
            db.rollback()
            if claimed:
                _mark(db, user, previous)
        except Exception:
            logger.exception("news: mark not cleared for %s", telegram_id)
            db.rollback()
        return "failed"
    return "sent"


async def run(db, bot, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    counts = {"sent": 0, "failed": 0, "blocked": 0}
    for user, news in users_due(db, now):
        outcome = await _send(db, bot, user, news)
        if outcome in counts:
            counts[outcome] += 1
    log = logger.error if counts["failed"] else logger.info
    log("news: %s", counts)
    return counts
