"""Message the bot's admins (the owner) from anywhere — handlers or Celery.

Used by the model watch (ADR-0004): a shutdown date announced, a model gone.
Builds its own ``telegram.Bot`` per call, so it works in a Celery worker that has
no running bot Application, and binds to the caller's event loop.
"""

import logging

from telegram import Bot

from app.core.config import settings

logger = logging.getLogger(__name__)

# httpx/httpcore log every request URL at INFO, and a Bot API URL carries the bot
# token (api.telegram.org/bot<TOKEN>/sendMessage). bot.py clamps these for the
# bot process; a Celery worker never runs that code, so clamp here too.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Telegram rejects longer texts.
_MAX_MESSAGE_CHARS = 4096


async def notify_admins(text: str) -> bool:
    """Send ``text`` to every admin. True if at least one copy was delivered.

    Never raises: a failed alert is logged and reported as False, so the caller
    can keep its "already told them" state unchanged and try again next time.
    """
    admins = settings.admin_ids
    if not admins:
        logger.warning("notify_admins: TELEGRAM_ADMIN_IDS is empty; alert not sent")
        return False
    if len(text) > _MAX_MESSAGE_CHARS:
        text = text[: _MAX_MESSAGE_CHARS - 1] + "…"
    delivered = 0
    try:
        async with Bot(settings.TELEGRAM_BOT_TOKEN) as bot:
            for admin_id in admins:
                try:
                    await bot.send_message(chat_id=admin_id, text=text)
                    delivered += 1
                except Exception as exc:
                    logger.warning("notify_admins: send to %s failed: %s", admin_id, exc)
    except Exception as exc:  # bad token, network down at initialize()
        logger.warning("notify_admins: could not start the bot client: %s", exc)
    return delivered > 0
