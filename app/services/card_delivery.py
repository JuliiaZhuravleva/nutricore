"""A screen that may carry a picture, delivered to Telegram (stage 5, slice D).

The caption is the screen's whole text, so without a picture nothing is lost: no
sidecar, a failed card or a caption over the limit all give the same screen in
text. A card drawn fast goes straight in. A slower one shows a loading picture
with the final caption and buttons, then swaps in; it never stays a loader.

A later tap on the same screen wins. A photo cannot be edited into plain text or
back, so crossing between the two sends the new message and deletes the old; a tap
still on its way from the old message then acts on the new one.
"""

import asyncio
import html
import logging
import re
from pathlib import Path
from typing import Optional

import httpx
from telegram import InaccessibleMessage, InputMediaPhoto
from telegram.constants import ParseMode
from telegram.error import BadRequest, NetworkError, TelegramError

from app.services.picture_renderer import PictureRenderer, Ticket

logger = logging.getLogger(__name__)

FAST_WAIT = 1.5  # a warm browser draws in about this; no loader flash under it
SLOW_WAIT = 60.0
CAPTION_LIMIT = 1024
FILE_IDS_MAX = 1000
SCREENS_MAX = 1000
LOADING_DIR = Path(__file__).resolve().parent.parent / "assets"
_TAG = re.compile(r"<[^>]*>")


def fits_caption(text: str) -> bool:
    plain = html.unescape(_TAG.sub("", text))
    return len(plain.encode("utf-16-le")) // 2 <= CAPTION_LIMIT


def _not_modified(error: BadRequest) -> bool:
    # A repeated tap that leads to the same screen: nothing to change.
    return "not modified" in str(error).lower()


class _Live:
    """One live screen: the message showing it now, whichever it crossed to."""

    def __init__(self, message):
        self.message = message
        self.turn = 0  # the tap whose result may land
        self.lock = asyncio.Lock()  # an upload must not land after a newer edit


def may_have_arrived(error: Exception) -> bool:
    """Whether Telegram may have taken a send that failed. A broken connection can
    come after it accepted the request; only one never made is certainly not sent."""
    if not isinstance(error, NetworkError) or isinstance(error, BadRequest):
        return False
    never_made = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)
    return not isinstance(error.__cause__, never_made)


class CardDelivery:
    def __init__(
        self,
        renderer: PictureRenderer,
        *,
        fast_wait: float = FAST_WAIT,
        slow_wait: float = SLOW_WAIT,
        loading_dir: Path = LOADING_DIR,
    ):
        self._renderer = renderer
        self._fast_wait = fast_wait
        self._slow_wait = slow_wait
        self._loading_dir = loading_dir
        self._screens: dict = {}  # (chat, message) -> _Live, old messages included
        self._turn = 0
        self._file_ids: dict = {}  # per bot, so per process is enough
        self._tasks: set = set()  # a task nobody holds can be collected mid-wait

    async def send(self, message, screen) -> None:
        """A new screen, answering ``message``."""
        ticket = self._ticket(screen)
        png = await ticket.wait(self._fast_wait) if ticket else None
        if ticket is None or ticket.failed:
            await self._reply_text(message, screen)
            return
        try:
            sent = await self._reply_photo(message, screen, ticket, png)
        except (TelegramError, OSError) as e:
            logger.warning("card: the photo did not go, text instead: %s", e)
            await self._reply_text(message, screen)
            return
        if png is None:
            live = self._live(sent)
            self._finish_later(live, screen, ticket, self._claim(live))

    async def push(self, bot, chat_id: int, screen) -> None:
        """A message nobody tapped for: silent, and no loader in a quiet chat, so
        the picture goes only once it is drawn, else the text. A photo that may
        have arrived goes up with no text on top of it."""
        ticket = self._ticket(screen)
        png = await ticket.wait(self._slow_wait) if ticket else None
        if png is not None:
            try:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=png,
                    caption=screen.text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=screen.markup,
                    disable_notification=True,
                )
                return
            except (TelegramError, OSError) as e:
                if may_have_arrived(e):
                    raise
                logger.warning("card: the photo did not go, text instead: %s", e)
        await bot.send_message(
            chat_id=chat_id,
            text=screen.text,
            parse_mode=ParseMode.HTML,
            reply_markup=screen.markup,
            disable_notification=True,
        )

    async def tap(self, query, screen) -> None:
        """The next screen after a tap on ``query.message``."""
        message = query.message
        if isinstance(message, InaccessibleMessage):
            # Too old for Telegram to show it: neither an edit nor a photo reaches it.
            await query.get_bot().send_message(
                message.chat.id,
                screen.text,
                parse_mode=ParseMode.HTML,
                reply_markup=screen.markup,
            )
            return
        if message is None:
            await self._edit_text(query.edit_message_text, screen)
            return
        live = self._live(message)
        turn = self._claim(live)
        ticket = self._ticket(screen)
        png = None
        if ticket is not None:
            png = await ticket.wait(self._fast_wait, lambda: live.turn != turn)
        async with live.lock:
            if live.turn != turn:
                return
            if ticket is None or ticket.failed:
                await self._show_text(live, screen)
                return
            try:
                await self._show_card(live, screen, ticket, png)
            except (TelegramError, OSError) as e:
                logger.warning("card: tap failed, text instead: %s", e)
                await self._show_text(live, screen)
                return
        if png is None:
            self._finish_later(live, screen, ticket, turn)

    # --- what a message is showing ----------------------------------------------

    @staticmethod
    def _key(message) -> tuple:
        return (message.chat.id, message.message_id)

    def _live(self, message) -> _Live:
        key = self._key(message)
        live = self._screens.get(key)
        if live is None:
            if len(self._screens) >= SCREENS_MAX:
                self._screens.clear()
            live = self._screens[key] = _Live(message)
        return live

    def _move(self, live: _Live, message) -> None:
        # The old message stays in the map: a tap may already be on its way from it.
        live.message = message
        self._screens[self._key(message)] = live

    def _claim(self, live: _Live) -> int:
        self._turn += 1
        live.turn = self._turn
        return self._turn

    def _ticket(self, screen) -> Optional[Ticket]:
        report = getattr(screen, "picture", None)
        if not report or not fits_caption(screen.text):
            return None
        return self._renderer.request(report)

    async def _show_card(self, live: _Live, screen, ticket, png) -> None:
        old = live.message
        if old.photo:
            await self._edit_photo(old, screen, ticket, png)
            return
        self._move(live, await self._reply_photo(old, screen, ticket, png))
        await self._delete(old)

    async def _show_text(self, live: _Live, screen) -> None:
        old = live.message
        if not old.photo:
            await self._edit_text(old.edit_text, screen)
            return
        self._move(live, await self._reply_text(old, screen))
        await self._delete(old)

    # --- the slow way -----------------------------------------------------------

    def _finish_later(self, live: _Live, screen, ticket: Ticket, turn: int) -> None:
        task = asyncio.create_task(self._finish(live, screen, ticket, turn))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _finish(self, live: _Live, screen, ticket: Ticket, turn: int) -> None:
        try:
            png = await ticket.wait(self._slow_wait, lambda: live.turn != turn)
            async with live.lock:
                if live.turn != turn:
                    return
                if png is None:
                    if not ticket.failed:
                        logger.warning(
                            "render: %s not drawn in %.0f s, text instead",
                            ticket.name[:12],
                            self._slow_wait,
                        )
                    await self._show_text(live, screen)
                    return
                try:
                    await self._edit_photo(live.message, screen, ticket, png)
                except Exception:
                    logger.exception("card: the swap failed, text instead")
                    # A newer tap is waiting for this lock and has its own screen.
                    if live.turn == turn:
                        await self._show_text(live, screen)
        except Exception:
            # The loader stays, with the final caption and working buttons.
            logger.exception("card: the text did not go either")

    # --- Telegram ---------------------------------------------------------------

    def _source(self, screen, ticket: Ticket, png: Optional[bytes]) -> tuple:
        """(file id or bytes, cache key) for the card, or its loader without one."""
        if png is not None:
            return self._file_ids.get(ticket.name, png), ticket.name
        look = screen.picture.get("theme") or "day"
        key = f"loading-{look}"
        if key in self._file_ids:
            return self._file_ids[key], key
        return (self._loading_dir / f"card_{key}.png").read_bytes(), key

    def _remember(self, key: str, sent) -> None:
        photos = getattr(sent, "photo", None)
        if photos:
            if len(self._file_ids) >= FILE_IDS_MAX:
                self._file_ids.clear()
            self._file_ids[key] = photos[-1].file_id

    async def _reply_photo(self, message, screen, ticket, png):
        source, key = self._source(screen, ticket, png)
        sent = await message.reply_photo(
            source,
            caption=screen.text,
            parse_mode=ParseMode.HTML,
            reply_markup=screen.markup,
        )
        self._remember(key, sent)
        return sent

    async def _edit_photo(self, message, screen, ticket, png) -> None:
        source, key = self._source(screen, ticket, png)
        media = InputMediaPhoto(source, caption=screen.text, parse_mode=ParseMode.HTML)
        try:
            sent = await message.edit_media(media, reply_markup=screen.markup)
        except BadRequest as e:
            if not _not_modified(e):
                raise
            return  # the same screen tapped again; a loader still gets its swap
        self._remember(key, sent)

    async def _reply_text(self, message, screen):
        return await message.reply_text(
            screen.text, parse_mode=ParseMode.HTML, reply_markup=screen.markup
        )

    @staticmethod
    async def _edit_text(edit, screen) -> None:
        try:
            await edit(
                screen.text, parse_mode=ParseMode.HTML, reply_markup=screen.markup
            )
        except BadRequest as e:
            if not _not_modified(e):
                raise

    @staticmethod
    async def _delete(message) -> None:
        try:
            await message.delete()
        except TelegramError as e:
            # Over 48 hours old it stays; the new screen is below it anyway.
            logger.info("card: the old message stays: %s", e)
