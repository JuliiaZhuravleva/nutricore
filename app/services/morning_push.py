"""Sending the ☀️ morning question: who is due, the claim, the send (stage 6, D10).

A sweep every 15 minutes asks ``morning.due`` for each person; the question goes out
in the first sweep inside the 10:00 hour of their own clock, never later that day. The
day asked about is claimed by its row in ``morning_answers`` before anything is sent,
so two overlapping sweeps send it once. Then the previous question leaves the chat (at
most one is live): an unanswered young one is deleted, any other loses its buttons and
keeps its text.

What a failure means for the row, as for the evening reminder: blocked → kept as
blocked; may have arrived → kept as maybe; certainly not sent → the row goes and the
next sweep inside the hour tries again. Always silent. No model is called here.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from telegram.constants import ParseMode
from telegram.error import Forbidden, TelegramError

from app.crud.crud_morning_answer import crud_morning_answer
from app.models.morning_answer import MorningAnswer
from app.models.user import User
from app.services import morning
from app.services.card_delivery import may_have_arrived

logger = logging.getLogger(__name__)


def users_due(db, now: datetime) -> list:
    """(user, day asked about) for everyone with a question due now. One person's bad
    data must not cost everyone else theirs."""
    due = []
    for user in db.query(User).order_by(User.id).all():
        telegram_id = user.telegram_id
        try:
            day = morning.due(db, user, now)
        except Exception:
            logger.exception("morning: could not check %s", telegram_id)
            db.rollback()
            continue
        if day is not None:
            due.append((user, day))
    return due


async def _close_previous(db, bot, row: MorningAnswer, now: datetime) -> None:
    """At most one live question in the chat. An unanswered one that Telegram still
    deletes is deleted; any other (answered, stopped, too old) keeps its text and
    loses only its buttons."""
    previous = crud_morning_answer.previous(db, row.user_id, row.id)
    if previous is None:
        return
    sent_at = previous.sent_at
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=UTC)  # SQLite drops it
    if previous.answer is None and now - sent_at < morning.DELETE_WITHIN:
        try:
            await bot.delete_message(previous.chat_id, previous.message_id)
            return
        except TelegramError as error:
            logger.info("morning: previous one not deleted: %s", error)
    try:
        await bot.edit_message_reply_markup(
            chat_id=previous.chat_id, message_id=previous.message_id, reply_markup=None
        )
    except TelegramError as error:
        logger.info("morning: previous one kept its buttons: %s", error)


async def _send(db, bot, user: User, day: date, now: datetime) -> str:
    telegram_id = user.telegram_id
    row = crud_morning_answer.claim(
        db, user_id=user.id, day=day, chat_id=telegram_id, now=now
    )
    if row is None:
        return "skipped"
    try:
        await _close_previous(db, bot, row, now)
        screen = morning.question_view(row)
        message = await bot.send_message(
            chat_id=telegram_id,
            text=screen.text,
            parse_mode=ParseMode.HTML,
            reply_markup=screen.markup,
            disable_notification=True,  # D10: always silent
        )
    except Forbidden:
        logger.info("morning: %s blocked the bot", telegram_id)
        _settle(db, row, "blocked")
        return "blocked"
    except Exception as error:
        if may_have_arrived(error):
            logger.warning("morning: no answer for %s, may have arrived", telegram_id)
            _settle(db, row, "maybe")
            return "failed"
        logger.exception("morning: failed for %s, next sweep retries", telegram_id)
        try:
            db.rollback()
            db.delete(row)
            db.commit()
        except Exception:
            logger.exception("morning: claim not cleared for %s", telegram_id)
            db.rollback()
        return "failed"
    row.message_id = message.message_id
    row.status = "sent"
    db.commit()
    return "sent"


def _settle(db, row: MorningAnswer, status: str) -> None:
    try:
        db.rollback()
        row.status = status
        db.commit()
    except Exception:
        logger.exception("morning: status %s not kept for %s", status, row.id)
        db.rollback()


async def run(db, bot, now: datetime | None = None) -> dict:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    counts = {"sent": 0, "skipped": 0, "failed": 0, "blocked": 0}
    for user, day in users_due(db, now):
        telegram_id = user.telegram_id
        try:
            outcome = await _send(db, bot, user, day, now)
        except Exception:
            # One person's failed claim or commit must not cost everyone after
            # them their question until the next sweep.
            logger.exception("morning: could not send to %s", telegram_id)
            db.rollback()
            outcome = "failed"
        counts[outcome] += 1
    log = logger.error if counts["failed"] else logger.info
    log("morning: %s", counts)
    return counts
