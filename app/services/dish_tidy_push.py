"""Sending the weekly 🍲 tidy-up: who is due, the claim, the merges, the send.

A sweep every 15 minutes. A person is due on Sunday from 12:00 to 20:59 on their own
clock, with the switch on (``dish_tidy_screen.tidy_on``), when the bot may write to
them and the week has no batch yet. The wide window lets a sweep lost to a restart at
noon be made up later the same afternoon.

1. The claim: the batch row (UNIQUE user + week), committed before anything else.
2. The pass, in one transaction under the person's lock: the finder's rows, the
   near-identical dishes merged under the batch's group (``dish_tidy_screen.prepare``).
   Nothing found → ``empty``, nothing sent.
3. The send, always silent. What a failure means:

   delivered          → ``sent``; an older open batch is closed («есть новая»)
   blocked            → the merges come apart again, ``blocked``
   may have arrived   → ``maybe``: the merges stay, nothing is resent (🍲 Блюда shows it)
   certainly not sent → ``retry``: the stored batch is resent next sweep, never
                        recomputed; after a day the merges come apart, ``failed``

A batch stuck ``claimed`` for half an hour (a killed worker; nothing was written with
it) is deleted, and a later sweep the same afternoon redoes it; one stuck ``ready`` is
sent as a retry. No model is called here.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

from telegram.constants import ParseMode
from telegram.error import Forbidden, TelegramError

from app.crud.crud_dish_tidy import crud_dish_tidy
from app.models.dish_tidy import DishTidyBatch
from app.models.user import User
from app.services import dish_ops, dish_tidy_screen, settings_screen
from app.services.access_control import may_receive
from app.services.card_delivery import may_have_arrived
from app.services.day_card import local_time

logger = logging.getLogger(__name__)

SUNDAY = 6
FIRST_HOUR, LAST_HOUR = 12, 20  # 12:00–20:59 local
STUCK = timedelta(minutes=30)
GIVE_UP = timedelta(hours=24)
COUNTS = ("sent", "empty", "skipped", "failed", "blocked")
RESENDABLE = ("ready", "retry")


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)  # SQLite drops it


def week_due(db, user: User, now: datetime) -> date | None:
    """The local Sunday to tidy up for now, or None."""
    if not dish_tidy_screen.tidy_on(user):
        return None
    local = local_time(now, settings_screen.user_zone(user))
    if local.weekday() != SUNDAY or not FIRST_HOUR <= local.hour <= LAST_HOUR:
        return None
    if crud_dish_tidy.for_week(db, user.id, local.date()) is not None:
        return None
    if not may_receive(db, user.telegram_id):
        return None
    return local.date()


def users_due(db, now: datetime) -> list:
    """(user, week) for everyone due now. One person's bad data must not cost everyone
    else theirs."""
    due = []
    for user in db.query(User).order_by(User.id).all():
        telegram_id = user.telegram_id
        try:
            week = week_due(db, user, now)
        except Exception:
            logger.exception("dish tidy: could not check %s", telegram_id)
            db.rollback()
            continue
        if week is not None:
            due.append((user, week))
    return due


def clear_stuck(db, now: datetime) -> int:
    """Deletes the batches stuck ``claimed`` (a killed worker): nothing was written with
    them, so a later sweep may claim the week again."""
    gone = 0
    for batch in crud_dish_tidy.in_state(db, "claimed"):
        if now - _aware(batch.claimed_at) > STUCK:
            logger.warning("dish tidy: batch %s stuck claimed, cleared", batch.id)
            db.delete(batch)
            gone += 1
    if gone:
        db.commit()
    return gone


def to_resend(db, now: datetime) -> list:
    """The stored batches to send again: ``retry``, and ``ready`` left by a killed
    worker."""
    batches = crud_dish_tidy.in_state(db, "retry", "ready")
    return [
        batch
        for batch in batches
        if batch.state == "retry" or now - _aware(batch.claimed_at) > STUCK
    ]


async def _retire_older(db, bot, user: User, batch: DishTidyBatch) -> None:
    """At most one open batch: an older one closes, its message says so and keeps
    only what can still be taken back."""
    for old in crud_dish_tidy.open_batches(db, user.id, but=batch.id):
        old.state = "retired"
        db.commit()
        if old.chat_id is None or old.message_id is None:
            continue
        screen = dish_tidy_screen.ended_view(db, old, user, dish_tidy_screen.RETIRED)
        try:
            await bot.edit_message_text(
                chat_id=old.chat_id,
                message_id=old.message_id,
                text=screen.text,
                parse_mode=ParseMode.HTML,
                reply_markup=screen.markup,
            )
        except TelegramError as error:
            logger.info("dish tidy: batch %s not edited closed: %s", old.id, error)


async def deliver(db, bot, user: User, batch: DishTidyBatch, now: datetime) -> str:
    """Sends a prepared batch and settles its state; the outcome for the counts.

    Under the person's lock, the batch read again: one they opened from the list and
    settled since the sweep listed it is neither sent again nor taken back. The lock
    holds over the send, so a tap waits for it rather than racing it."""
    telegram_id = user.telegram_id
    dish_ops.lock_user(db, user)
    db.refresh(batch)
    if batch.state not in RESENDABLE:
        db.rollback()
        return "skipped"
    items = crud_dish_tidy.items(db, batch.id)
    screen = dish_tidy_screen.open_view(
        db, batch, user, dish_tidy_screen.default_mask(items)
    )
    try:
        message = await bot.send_message(
            chat_id=telegram_id,
            text=screen.text,
            parse_mode=ParseMode.HTML,
            reply_markup=screen.markup,
            disable_notification=True,
        )
    except Forbidden:
        logger.info("dish tidy: %s blocked the bot, merges taken back", telegram_id)
        db.rollback()
        dish_tidy_screen.roll_back(db, user, batch, "blocked")
        return "blocked"
    except Exception as error:
        db.rollback()
        if may_have_arrived(error):
            logger.warning("dish tidy: no answer for %s, may have arrived", telegram_id)
            batch.state = "maybe"
            batch.chat_id = telegram_id
            db.commit()
            return "failed"
        batch.tries = (batch.tries or 0) + 1
        if now - _aware(batch.claimed_at) > GIVE_UP:
            logger.error(
                "dish tidy: batch %s not sent for a day, merges taken back", batch.id
            )
            dish_tidy_screen.roll_back(db, user, batch, "failed")
            return "failed"
        logger.exception("dish tidy: failed for %s, next sweep retries", telegram_id)
        batch.state = "retry"
        db.commit()
        return "failed"
    batch.chat_id = telegram_id
    batch.message_id = message.message_id
    batch.state = "sent"
    db.commit()
    await _retire_older(db, bot, user, batch)
    return "sent"


async def _tidy(db, bot, user: User, week: date, now: datetime) -> str:
    telegram_id = user.telegram_id  # read before a failure can expire it
    batch = crud_dish_tidy.claim(
        db, user_id=user.id, week=week, group_id=dish_ops.new_group(), now=now
    )
    if batch is None:
        return "skipped"
    try:
        found = dish_tidy_screen.prepare(db, user, batch, now)
    except Exception:
        # Rolled back whole: no merge was kept. The week is not tried again.
        logger.exception("dish tidy: the pass failed for %s", telegram_id)
        db.rollback()
        batch.state = "failed"
        db.commit()
        return "failed"
    if not found:
        return "empty"
    return await deliver(db, bot, user, batch, now)


async def run(db, bot, now: datetime | None = None) -> dict:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    counts = dict.fromkeys(COUNTS, 0)
    clear_stuck(db, now)
    for batch in to_resend(db, now):
        batch_id = batch.id
        user = db.get(User, batch.user_id)
        try:
            outcome = await deliver(db, bot, user, batch, now)
        except Exception:
            logger.exception("dish tidy: could not resend batch %s", batch_id)
            db.rollback()
            _give_up(db, user, batch_id, now)
            outcome = "failed"
        counts[outcome] += 1
    for user, week in users_due(db, now):
        telegram_id = user.telegram_id
        try:
            outcome = await _tidy(db, bot, user, week, now)
        except Exception:
            # One person's failed claim or commit must not cost everyone after them.
            logger.exception("dish tidy: could not tidy up for %s", telegram_id)
            db.rollback()
            outcome = "failed"
        counts[outcome] += 1
    log = logger.error if counts["failed"] else logger.info
    log("dish tidy: %s", counts)
    return counts


def _give_up(db, user: User, batch_id: int, now: datetime) -> None:
    """A stored batch that fails before the send itself (not the send) would fail the
    same way every sweep: after a day its merges come apart, as for a send that never
    went. A failure here only logs: the next sweep tries again."""
    try:
        dish_ops.lock_user(db, user)
        batch = db.get(DishTidyBatch, batch_id, populate_existing=True)
        if batch is None or batch.state not in RESENDABLE:
            db.rollback()
            return  # settled meanwhile: what the person accepted stays
        if now - _aware(batch.claimed_at) <= GIVE_UP:
            db.rollback()
            return
        logger.error(
            "dish tidy: batch %s failing for a day, merges taken back", batch_id
        )
        dish_tidy_screen.roll_back(db, user, batch, "failed")
    except Exception:
        logger.exception("dish tidy: batch %s could not be taken back", batch_id)
        db.rollback()


def has_work(db, now: datetime) -> bool:
    """Whether a sweep has anything to do (so the bot is built only then)."""
    clear_stuck(db, now)
    return bool(to_resend(db, now)) or bool(users_due(db, now))
