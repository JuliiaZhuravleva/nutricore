"""The week's summary on Sunday evening (stage 5, slice E, G5-R15).

Sent silently at 22:00 on the user's own clock, for the Monday–Sunday just ending,
and only when that week has records: nobody gets a message about an empty week.
⚙️ turns it off. The week is marked as sent before the send: a summary sent twice
is worse than one missed, so a failure that certainly did not arrive clears the
mark and the next run tries again, and one that may have arrived keeps it. A worker
killed between the mark and the send loses that week: the price of never sending twice.
"""

import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from telegram.error import Forbidden

from app.core.config import settings
from app.crud.crud_meal import crud_meal
from app.crud.crud_subscription import crud_subscription
from app.models.user import User
from app.services.access_control import is_user_allowed
from app.services.card_delivery import may_have_arrived
from app.services.day_card import local_time
from app.services.settings_screen import WEEK_PUSH_SENT, user_zone, week_push_on
from app.services.stats_screen import summary_view, week_bounds_utc

logger = logging.getLogger(__name__)

SUNDAY = 6
HOUR = 22


def due_monday(moment: datetime) -> date | None:
    """The Monday of the week to send at this local time, or None outside the window."""
    if moment.weekday() != SUNDAY or moment.hour < HOUR:
        return None
    return moment.date() - timedelta(days=SUNDAY)


def _may_receive(db, user: User) -> bool:
    """As for the bot's own screens: its button would answer 🔒 to anyone else."""
    telegram_id = user.telegram_id
    if not is_user_allowed(telegram_id):
        return False
    return telegram_id in settings.admin_ids or (
        crud_subscription.is_active_subscription(db, telegram_id)
    )


def _due_week(db, user: User, now: datetime) -> date | None:
    if not week_push_on(user) or not _may_receive(db, user):
        return None
    zone = user_zone(user)
    monday = due_monday(local_time(now, zone))
    if (
        monday is None
        or (user.settings or {}).get(WEEK_PUSH_SENT) == monday.isoformat()
    ):
        return None
    start, end = week_bounds_utc(monday, zone)
    return monday if crud_meal.get_user_meals_between(db, user.id, start, end) else None


def users_due(db, now: datetime) -> list:
    """(user, Monday) for everyone whose summary goes out now. One user's bad data
    must not cost everyone else theirs."""
    due = []
    for user in db.query(User).order_by(User.id).all():
        telegram_id = user.telegram_id
        try:
            monday = _due_week(db, user, now)
        except Exception:
            logger.exception("week push: could not check %s", telegram_id)
            db.rollback()
            continue
        if monday is not None:
            due.append((user, monday))
    return due


def _mark(db, user: User, value: str | None) -> None:
    # A new dict: SQLAlchemy does not see a JSON value changed in place.
    values = {**(user.settings or {}), WEEK_PUSH_SENT: value}
    if value is None:
        del values[WEEK_PUSH_SENT]
    user.settings = values
    db.commit()


def _claim(db, user: User, monday: date) -> tuple[bool, str | None]:
    """Mark the week under a row lock, reading the mark afresh: runs left queued by a
    restart overlap, and the second must find the first one's mark."""
    db.execute(
        select(User)
        .where(User.id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    previous = (user.settings or {}).get(WEEK_PUSH_SENT)
    if previous == monday.isoformat():
        db.rollback()
        return False, previous
    _mark(db, user, monday.isoformat())
    return True, previous


async def _send(db, bot, delivery, user: User, monday: date, now: datetime) -> str:
    telegram_id = user.telegram_id
    previous = None
    claimed = False
    try:
        claimed, previous = _claim(db, user, monday)
        if not claimed:
            return "skipped"
        screen = summary_view(db, user, monday, now, first=previous is None)
        await delivery.push(bot, telegram_id, screen)
    except Forbidden:
        logger.info("week push: %s blocked the bot", telegram_id)
        return "blocked"
    except Exception as error:
        if may_have_arrived(error):
            logger.warning("week push: no answer for %s, may have arrived", telegram_id)
            return "failed"
        logger.exception("week push: failed for %s, next run retries", telegram_id)
        try:
            db.rollback()
            if claimed:
                _mark(db, user, previous)
        except Exception:
            logger.exception("week push: mark not cleared for %s", telegram_id)
            db.rollback()
        return "failed"
    return "sent"


async def run(db, bot, delivery, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    counts = {"sent": 0, "failed": 0, "blocked": 0}
    for user, monday in users_due(db, now):
        outcome = await _send(db, bot, delivery, user, monday, now)
        if outcome in counts:
            counts[outcome] += 1
    log = logger.error if counts["failed"] else logger.info
    log("week push: %s", counts)
    return counts
