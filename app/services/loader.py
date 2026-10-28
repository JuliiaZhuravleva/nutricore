"""A «⏳ …» line while slow work runs; the result is edited into it.

    🍽 photo → (0.3 s) ⏳ Смотрю фото… → the same message becomes the answer

The loader shows only when the work outlasts ``DELAY``, so a fast answer never
flashes. ``reply`` is the work's one answer: it stops a pending loader, edits a
shown one into the answer, and sends a new message otherwise. A reply keyboard
cannot be edited in, so an answer that carries one replaces the loader (the
loader is deleted). Every Telegram failure here falls back to a plain new
message: the loader may stay on screen, the answer never goes missing.
"""

from __future__ import annotations

import asyncio
import logging

from telegram import InlineKeyboardMarkup

logger = logging.getLogger(__name__)

DELAY = 0.3  # seconds of silence before the loader shows


class Loader:
    def __init__(self, message, text: str, delay: float | None = None) -> None:
        self._message = message
        self._text = text
        self._delay = DELAY if delay is None else delay
        self._task: asyncio.Task | None = None
        self._sending = False
        self.shown = None  # the loader's Message, once it is on screen

    async def __aenter__(self) -> "Loader":
        self._task = asyncio.create_task(self._show())
        return self

    async def __aexit__(self, *exc) -> None:
        await self._stop()

    async def _show(self) -> None:
        await asyncio.sleep(self._delay)
        # Past the sleep the send is not cancelled: a loader that reached
        # Telegram must be known, or it would stay «⏳» forever.
        self._sending = True
        try:
            self.shown = await self._message.reply_text(self._text)
        except Exception as e:
            logger.debug("Could not show the loader: %s", e)

    async def _stop(self) -> None:
        task, self._task = self._task, None
        if task is None or task.done():
            return
        if self._sending:
            await task
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def drop(self) -> None:
        """No answer goes here (the caller sends its own): take the loader away."""
        await self._stop()
        shown, self.shown = self.shown, None
        if shown is None:
            return
        try:
            await shown.delete()
        except Exception as e:
            logger.debug("Could not delete the loader: %s", e)

    async def reply(self, text: str, reply_markup=None):
        """The answer: edited into the loader when it is shown and can be."""
        await self._stop()
        editable = reply_markup is None or isinstance(
            reply_markup, InlineKeyboardMarkup
        )
        if self.shown is not None and editable:
            shown, self.shown = self.shown, None
            try:
                return await shown.edit_text(text, reply_markup=reply_markup)
            except Exception as e:
                logger.warning("Could not edit the loader into the answer: %s", e)
                self.shown = shown  # deleted below, the answer goes out new
        await self.drop()
        return await self._message.reply_text(text, reply_markup=reply_markup)
