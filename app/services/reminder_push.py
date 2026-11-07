"""Sending the 🔔 reminders: who is due, the claim, the send (stage 6, §5.1, §5.4).

A sweep every 15 minutes asks ``reminders.due`` for each person. A slot is claimed by
its row in ``reminder_sends`` before anything is sent, so two overlapping sweeps send
it once. Then the previous reminder leaves the chat: deleted, unless a ⭐ was logged
from it or it is too old for Telegram to delete, in which case only its buttons go.

What a failure means for the row: blocked → kept as blocked (nothing arrives, the
ladder counts it); may have arrived → kept as maybe and not counted; certainly not
sent → the row goes and the next sweep inside the hour tries again. No model call is
made anywhere here (R1).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from telegram.constants import ParseMode
from telegram.error import Forbidden, TelegramError

from app.crud.crud_reminder_send import crud_reminder_send
from app.models.reminder_send import ReminderSend
from app.models.user import User
from app.services import reminders, settings_screen
from app.services.card_delivery import may_have_arrived

logger = logging.getLogger(__name__)


def users_due(db, now: datetime) -> list:
    """(user, slot) for everyone with a reminder due now. One person's bad data must
    not cost everyone else theirs."""
    due = []
    for user in db.query(User).order_by(User.id).all():
        telegram_id = user.telegram_id
        try:
            slot = reminders.due(db, user, now)
        except Exception:
            logger.exception("reminders: could not check %s", telegram_id)
            db.rollback()
            continue
        if slot is not None:
            due.append((user, slot))
    return due


async def _close_previous(
    db, bot, user: User, send: ReminderSend, now: datetime
) -> None:
    """At most one live reminder in the chat (R4). One closed by ✅ ОК is still in
    the chat, so it leaves too: ``closed_at`` says it was dismissed, not that it is
    gone. The previous one is the latest that got a message, so a message behind a
    maybe, a block or a killed claim is not left with its buttons; when it already
    left at an earlier send, the second delete only fails, as a retried claim's
    does, and that is harmless. One a tap made another screen (✖'s, ⭐ Избранное,
    the zone picker) is that screen now, and stays as it is. One too old to delete
    keeps only its dated title, so it never reads as today's."""
    previous = crud_reminder_send.previous(db, send.user_id, send.id)
    if previous is None:
        return
    if previous.closed_at is None:
        previous.closed_at = now
        db.commit()
    if previous.answer == "off" or reminders.config(user).moved == previous.id:
        logger.info("reminders: previous one is another screen now, left as it is")
        return
    sent_at = previous.sent_at
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=UTC)  # SQLite drops it
    young = now - sent_at < reminders.DELETE_WITHIN
    star = reminders.holds_star(previous)
    try:
        if not star and young:
            await bot.delete_message(previous.chat_id, previous.message_id)
            return
    except TelegramError as error:
        logger.info("reminders: previous one not deleted: %s", error)
    if not star:
        try:
            await bot.edit_message_text(
                chat_id=previous.chat_id,
                message_id=previous.message_id,
                text=reminders.dated_title(previous),
                parse_mode=ParseMode.HTML,
                reply_markup=None,
            )
            return
        except TelegramError as error:
            logger.info("reminders: previous one kept its text: %s", error)
    try:
        await bot.edit_message_reply_markup(
            chat_id=previous.chat_id, message_id=previous.message_id, reply_markup=None
        )
    except TelegramError as error:
        logger.info("reminders: previous one kept its buttons: %s", error)


async def _send(db, bot, user: User, slot: reminders.Slot, now: datetime) -> str:
    telegram_id = user.telegram_id
    local_day = now.astimezone(settings_screen.user_zone(user)).date()
    send = crud_reminder_send.claim(
        db,
        user_id=user.id,
        local_day=local_day,
        slot=slot.key,
        kind=slot.kind,
        chat_id=telegram_id,
        now=now,
        centre=slot.centre,
    )
    if send is None:
        return "skipped"
    try:
        await _close_previous(db, bot, user, send, now)
        cfg = reminders.config(user)
        screen = reminders.reminder_view(db, user, send, now)
        message = await bot.send_message(
            chat_id=telegram_id,
            text=screen.text,
            parse_mode=ParseMode.HTML,
            reply_markup=screen.markup,
            disable_notification=not cfg.sound,  # D3: silent unless switched on
        )
    except Forbidden:
        logger.info("reminders: %s blocked the bot", telegram_id)
        _settle(db, send, "blocked")
        return "blocked"
    except Exception as error:
        if may_have_arrived(error):
            logger.warning("reminders: no answer for %s, may have arrived", telegram_id)
            _settle(db, send, "maybe")
            return "failed"
        logger.exception("reminders: failed for %s, next sweep retries", telegram_id)
        try:
            db.rollback()
            db.delete(send)
            db.commit()
        except Exception:
            logger.exception("reminders: claim not cleared for %s", telegram_id)
            db.rollback()
        return "failed"
    send.message_id = message.message_id
    send.status = "sent"
    db.commit()
    if cfg.noted is None and not reminders.is_reentry(send):
        reminders._write(db, user, noted=send.id)  # D6: a re-entry is not the first
    return "sent"


def _settle(db, send: ReminderSend, status: str) -> None:
    try:
        db.rollback()
        send.status = status
        db.commit()
    except Exception:
        logger.exception("reminders: status %s not kept for %s", status, send.id)
        db.rollback()


async def run(db, bot, now: datetime | None = None) -> dict:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    counts = {"sent": 0, "skipped": 0, "failed": 0, "blocked": 0}
    for user, slot in users_due(db, now):
        telegram_id = user.telegram_id
        try:
            outcome = await _send(db, bot, user, slot, now)
        except Exception:
            # One person's failed claim or commit must not cost everyone after
            # them their reminder until the next sweep.
            logger.exception("reminders: could not send to %s", telegram_id)
            db.rollback()
            outcome = "failed"
        counts[outcome] += 1
    log = logger.error if counts["failed"] else logger.info
    log("reminders: %s", counts)
    return counts
