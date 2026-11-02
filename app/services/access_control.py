"""Bot access-control policy: mode gate (open/whitelist/closed) + admin gate.

Extracted from telegram.py so the "who may use the bot" policy lives in one place,
mirroring the API-side auth in app/core/deps.py. The subscription gate stays in
telegram.py (it is coupled to conversation state and keyboards).
"""

import logging
from typing import Optional

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ApplicationHandlerStop, ContextTypes

from app.core.config import settings
from app.crud.crud_access_grant import crud_access_grant
from app.crud.crud_subscription import crud_subscription
from app.db.session import SessionLocal

logger = logging.getLogger(__name__)

TERM_ENDED = (
    "Срок доступа закончился. Записи на месте. Продлить может тот, кто пригласил."
)
DOOR_CLOSED = "Доступ закрыт владельцем бота. Записи сохранены."
# Only the open mode brings someone here who never had a term: nothing has ended.
NO_ACCESS = "Доступа к дневнику пока нет. Открыть его может владелец бота."


def is_user_allowed(telegram_id: Optional[int]) -> bool:
    """Access-control check for the current bot mode (open | whitelist | closed).

    Admins (the owner) are always allowed. In whitelist mode, admins, ALLOWED_TELEGRAM_IDS
    and active grants pass; in closed mode, only admins, so a grant does not open it; in
    open mode, everyone but those the owner closed by hand. Updates with no user (e.g.
    channel posts) pass only in open mode. The settings are read first: only someone in
    neither list costs a query.
    """
    mode = settings.access_mode
    if telegram_id is None:
        return mode == "open"
    if telegram_id in settings.admin_ids:
        return True
    if mode == "open":
        return telegram_id in settings.allowed_ids or not _was_closed(telegram_id)
    if mode == "closed":
        return False
    # whitelist (and any unknown mode, normalized to whitelist by access_mode)
    return telegram_id in settings.allowed_ids or _has_grant(telegram_id)


def _has_grant(telegram_id: int) -> bool:
    """Fails closed: with the database down, a grant cannot be told from a stranger, and
    those with a grant wait. Admins and the list in the settings never get here."""
    try:
        with SessionLocal() as db:
            return crud_access_grant.is_active(db, telegram_id)
    except Exception as e:
        # The class only: a database error's text holds the query's values.
        logger.error("Access grant of %s not read: %s", telegram_id, type(e).__name__)
        return False


def _was_closed(telegram_id: int) -> bool:
    """Whether the owner closed this person's door. They hear it once, in the gate, as in any
    other mode. Fails open: the mode says everyone, and only a known closing says
    otherwise."""
    try:
        with SessionLocal() as db:
            grant = crud_access_grant.get(db, telegram_id)
            return grant is not None and grant.revoked_at is not None
    except Exception as e:
        logger.error("Access grant of %s not read: %s", telegram_id, type(e).__name__)
        return False


def in_the_settings(telegram_id: int) -> bool:
    """Let in by the server's settings, whatever the mode: a button cannot close that."""
    return telegram_id in settings.admin_ids or telegram_id in settings.allowed_ids


def may_receive(db, telegram_id: int) -> bool:
    """Whether the bot may write or spend on someone unasked. As for the bot's own screens:
    its buttons refuse anyone else."""
    if not is_user_allowed(telegram_id):
        return False
    return telegram_id in settings.admin_ids or (
        crud_subscription.is_active_subscription(db, telegram_id)
    )


async def access_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global gate: drop updates from users not allowed by the mode.

    Registered in an early handler group so it runs before every other handler; raising
    ApplicationHandlerStop stops processing. Strangers hear nothing, and someone whose door
    the owner closed hears that once. Whoever is refused goes through here, so an answer to
    a stranger belongs here, should one come.
    """
    user = update.effective_user
    telegram_id = user.id if user else None
    if not is_user_allowed(telegram_id):
        logger.info(
            "Dropping update from non-allowed user %s (mode=%s)",
            telegram_id,
            settings.access_mode,
        )
        await _tell_closed_once(update, context)
        raise ApplicationHandlerStop


async def _tell_closed_once(update: Update, context) -> None:
    """Only to someone speaking to the bot in private: a status change or a group message
    spends nothing. Marked before it is sent: a lost line is smaller than a repeated one.
    Nothing here may let the update through."""
    chat = getattr(update, "effective_chat", None)
    spoke = getattr(update, "message", None) or getattr(update, "callback_query", None)
    if chat is None or chat.type != "private" or spoke is None:
        return
    try:
        with SessionLocal() as db:
            if crud_access_grant.take_closed_notice(db, update.effective_user.id):
                await context.bot.send_message(chat.id, DOOR_CLOSED)
    except TelegramError as e:
        logger.warning("Closed door not told: %s", type(e).__name__)
    except Exception as e:
        logger.error("Closed door not read: %s", type(e).__name__)


def admin_required(func):
    """Decorator to restrict a handler to admins (the bot owner).

    Consolidates the admin gate so every owner-only command shares one check and
    one denial message. The check runs before the wrapped handler, so no argument
    parsing or outbound call happens for a non-admin. Guards against updates with
    no user/message (e.g. channel posts reaching a command in open mode).
    """

    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if user is None or user.id not in settings.admin_ids:
            logger.warning(
                "Unauthorized access to %s from user %s",
                func.__name__,
                user.id if user else None,
            )
            message = update.effective_message
            if message is not None:
                await message.reply_text("⛔️ Эта команда доступна только владельцу.")
            return
        return await func(update, context)

    return wrapper
