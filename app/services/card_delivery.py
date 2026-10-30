"""A screen that may carry a picture, delivered to Telegram (stage 5, slice D).

The caption is the screen's whole text, so without a picture nothing is lost: no
sidecar, a failed card or a caption over the limit all give the same screen in
text. A card drawn fast goes straight in. A slower one shows a loading picture
with the final caption and buttons, then swaps in; it never stays a loader.

A later tap on the same message wins. A photo cannot be edited into plain text
or back, so crossing between the two sends the new message and deletes the old.
"""

import asyncio
import html
import logging
import re
from pathlib import Path
from typing import Optional

from telegram import InputMediaPhoto
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError

from app.services.picture_renderer import PictureRenderer, Ticket

logger = logging.getLogger(__name__)

FAST_WAIT = 1.5  # a warm browser draws in about this; no loader flash under it
SLOW_WAIT = 60.0
CAPTION_LIMIT = 1024
FILE_IDS_MAX = 1000
LOADING_DIR = Path(__file__).resolve().parent.parent / "assets"
_TAG = re.compile(r"<[^>]*>")


def fits_caption(text: str) -> bool:
    plain = html.unescape(_TAG.sub("", text))
    return len(plain.encode("utf-16-le")) // 2 <= CAPTION_LIMIT


def _not_modified(error: BadRequest) -> bool:
    # A repeated tap that leads to the same screen: nothing to change.
    return "not modified" in str(error).lower()


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
        self._turns: dict = {}  # (chat, message) -> the tap whose result may land
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
        except TelegramError as e:
            logger.warning("card: the photo did not go, text instead: %s", e)
            await self._reply_text(message, screen)
            return
        if png is None:
            self._finish_later(sent, screen, ticket, self._claim(sent))

    async def tap(self, query, screen) -> None:
        """The next screen after a tap on ``query.message``."""
        message = query.message
        if not hasattr(message, "reply_photo"):
            # Too old for Telegram to show it to the bot: only an edit by id works.
            await self._edit_text(query, screen)
            return
        was_photo = bool(getattr(message, "photo", None))
        turn = self._claim(message)
        ticket = self._ticket(screen)
        png = None
        if ticket is not None:
            png = await ticket.wait(
                self._fast_wait, lambda: not self._current(message, turn)
            )
            if not self._current(message, turn):
                return
        shown = None
        if ticket is not None and not ticket.failed:
            try:
                if was_photo:
                    await self._edit_photo(message, screen, ticket, png)
                    shown = message
                else:
                    shown = await self._reply_photo(message, screen, ticket, png)
                    await self._delete(message)
            except TelegramError as e:
                logger.warning("card: tap failed, text instead: %s", e)
        if shown is None:
            self._release(message, turn)
            if was_photo:
                await self._replace_with_text(message, screen)
            else:
                await self._edit_text(query, screen)
        elif png is not None:
            self._release(message, turn)
        elif shown is message:
            self._finish_later(shown, screen, ticket, turn)
        else:
            self._release(message, turn)
            self._finish_later(shown, screen, ticket, self._claim(shown))

    # --- what a message is showing ----------------------------------------------

    @staticmethod
    def _key(message) -> tuple:
        return (message.chat.id, message.message_id)

    def _claim(self, message) -> int:
        self._turn += 1
        self._turns[self._key(message)] = self._turn
        return self._turn

    def _current(self, message, turn: int) -> bool:
        return self._turns.get(self._key(message)) == turn

    def _release(self, message, turn: int) -> None:
        if self._current(message, turn):
            del self._turns[self._key(message)]

    def _ticket(self, screen) -> Optional[Ticket]:
        report = getattr(screen, "picture", None)
        if not report or not fits_caption(screen.text):
            return None
        return self._renderer.request(report)

    # --- the slow way -----------------------------------------------------------

    def _finish_later(self, message, screen, ticket: Ticket, turn: int) -> None:
        task = asyncio.create_task(self._finish(message, screen, ticket, turn))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _finish(self, message, screen, ticket: Ticket, turn: int) -> None:
        try:
            png = await ticket.wait(
                self._slow_wait, lambda: not self._current(message, turn)
            )
            if not self._current(message, turn):
                return
            if png is None:
                await self._replace_with_text(message, screen)
            else:
                await self._edit_photo(message, screen, ticket, png)
        except Exception:
            logger.exception("card: the swap failed, text instead")
            try:
                await self._replace_with_text(message, screen)
            except TelegramError:
                logger.exception("card: the text did not go either")
        finally:
            self._release(message, turn)

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

    async def _reply_text(self, message, screen) -> None:
        await message.reply_text(
            screen.text, parse_mode=ParseMode.HTML, reply_markup=screen.markup
        )

    async def _edit_text(self, query, screen) -> None:
        try:
            await query.edit_message_text(
                screen.text, parse_mode=ParseMode.HTML, reply_markup=screen.markup
            )
        except BadRequest as e:
            if not _not_modified(e):
                raise

    async def _replace_with_text(self, message, screen) -> None:
        await self._reply_text(message, screen)
        await self._delete(message)

    @staticmethod
    async def _delete(message) -> None:
        try:
            await message.delete()
        except TelegramError as e:
            # Over 48 hours old it stays; the new screen is below it anyway.
            logger.info("card: the old message stays: %s", e)
