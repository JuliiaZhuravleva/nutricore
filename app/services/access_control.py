"""Bot access-control policy: mode gate (open/whitelist/closed) + admin gate.

Extracted from telegram.py so the "who may use the bot" policy lives in one place,
mirroring the API-side auth in app/core/deps.py. The subscription gate stays in
telegram.py (it is coupled to conversation state and keyboards).
"""

import logging
from typing import Optional

from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from app.core.config import settings

logger = logging.getLogger(__name__)


def is_user_allowed(telegram_id: Optional[int]) -> bool:
    """Access-control check for the current bot mode (open | whitelist | closed).

    Admins (the owner) are always allowed. In whitelist mode, only admins and
    ALLOWED_TELEGRAM_IDS pass; in closed mode, only admins; in open mode, everyone.
    Updates with no user (e.g. channel posts) pass only in open mode.
    """
    mode = settings.access_mode
    if telegram_id is None:
        return mode == "open"
    if telegram_id in settings.admin_ids:
        return True
    if mode == "open":
        return True
    if mode == "closed":
        return False
    # whitelist (and any unknown mode, normalized to whitelist by access_mode)
    return telegram_id in settings.allowed_ids


async def access_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global gate: silently drop updates from users not allowed by the mode.

    Registered in an early handler group so it runs before every other handler;
    raising ApplicationHandlerStop stops processing without sending any reply.
    """
    user = update.effective_user
    telegram_id = user.id if user else None
    if not is_user_allowed(telegram_id):
        logger.info(
            "Dropping update from non-allowed user %s (mode=%s)",
            telegram_id,
            settings.access_mode,
        )
        raise ApplicationHandlerStop


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
