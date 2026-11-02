"""Message the bot's admins (the owner) from anywhere — handlers or Celery.

Used by the model watch (ADR-0004): a shutdown date announced, a model gone.
Builds its own ``telegram.Bot`` per call, so it works in a Celery worker that has
no running bot Application, and binds to the caller's event loop.
"""

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Optional

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import BadRequest

from app.core.config import settings

logger = logging.getLogger(__name__)

# httpx/httpcore log every request URL at INFO, and a Bot API URL carries the bot
# token (api.telegram.org/bot<TOKEN>/sendMessage). bot.py clamps these for the
# bot process; a Celery worker never runs that code, so clamp here too.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Telegram rejects longer texts.
_MAX_MESSAGE_CHARS = 4096


FORWARD_FAILED = "⚠️ Само сообщение не переслалось."


@dataclass(frozen=True)
class Part:
    """One message of a delivery. ``plain`` is what goes if Telegram refuses the HTML:
    untrusted words must not be lost to a parse error."""

    html: str
    plain: str
    markup: Any = None


@dataclass(frozen=True)
class File:
    """A file Telegram already holds, sent again by its id."""

    kind: str  # voice, photo or document: Telegram refuses an id under another kind
    file_id: str


@asynccontextmanager
async def _client(bot: Optional[Bot]):
    """The caller's bot if it has one running; a worker has none, and gets its own."""
    if bot is not None:
        yield bot
        return
    async with Bot(settings.TELEGRAM_BOT_TOKEN) as own:
        yield own


async def notify_admins(text: str, bot: Optional[Bot] = None) -> bool:
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
        async with _client(bot) as client:
            for admin_id in admins:
                try:
                    await client.send_message(chat_id=admin_id, text=text)
                    delivered += 1
                except Exception as exc:
                    logger.warning(
                        "notify_admins: send to %s failed: %s", admin_id, exc
                    )
    except Exception as exc:  # bad token, network down at initialize()
        logger.warning("notify_admins: could not start the bot client: %s", exc)
    return delivered > 0


async def _send_part(client, admin_id: int, part: Part) -> None:
    try:
        await client.send_message(
            chat_id=admin_id,
            text=part.html,
            parse_mode=ParseMode.HTML,
            reply_markup=part.markup,
        )
    except BadRequest as exc:
        logger.warning("deliver_to_admins: HTML refused: %s", type(exc).__name__)
        await client.send_message(
            chat_id=admin_id, text=part.plain, reply_markup=part.markup
        )


async def _send_file(client, admin_id: int, file: File) -> None:
    send = {
        "voice": client.send_voice,
        "photo": client.send_photo,
        "document": client.send_document,
    }[file.kind]
    await send(admin_id, file.file_id)


async def _deliver_one(client, admin_id: int, parts, forward, file=None) -> bool:
    """True only for a whole copy: the file, if there is one, and every part."""
    whole = True
    if file is not None:
        try:
            await _send_file(client, admin_id, file)
        except Exception as exc:
            logger.warning("deliver_to_admins: file: %s", type(exc).__name__)
            whole = False
    if forward is not None:
        try:
            await client.forward_message(
                chat_id=admin_id, from_chat_id=forward[0], message_id=forward[1]
            )
        except Exception as exc:
            logger.warning("deliver_to_admins: forward: %s", type(exc).__name__)
            whole = False
    try:
        if not whole:
            await client.send_message(chat_id=admin_id, text=FORWARD_FAILED)
        for part in parts:
            await _send_part(client, admin_id, part)
    except Exception as exc:
        logger.warning("deliver_to_admins: send: %s", type(exc).__name__)
        return False
    return whole


async def deliver_to_admins(
    parts: list[Part],
    *,
    forward: Optional[tuple[int, int]] = None,
    bot: Optional[Bot] = None,
) -> bool:
    """To every admin: the (chat, message) ``forward`` names, then ``parts`` in order.
    True if at least one admin got a whole copy. Never raises, and logs no text."""
    admins = settings.admin_ids
    if not admins:
        logger.warning("deliver_to_admins: TELEGRAM_ADMIN_IDS is empty; not sent")
        return False
    delivered = 0
    try:
        async with _client(bot) as client:
            for admin_id in admins:
                if await _deliver_one(client, admin_id, parts, forward):
                    delivered += 1
    except Exception as exc:  # bad token, network down at initialize()
        logger.warning("deliver_to_admins: no client: %s", type(exc).__name__)
    return delivered > 0


async def deliver_to_one(
    admin_id: int, parts: list[Part], *, file: Optional[File] = None, bot: Optional[Bot]
) -> bool:
    """To the one admin who asked: the file, then ``parts``. True for a whole copy. Never
    raises, and logs no text."""
    try:
        async with _client(bot) as client:
            return await _deliver_one(client, admin_id, parts, None, file)
    except Exception as exc:
        logger.warning("deliver_to_one: no client: %s", type(exc).__name__)
        return False
