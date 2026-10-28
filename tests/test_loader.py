"""The «⏳ …» loader: shown only for slow work, then edited into the answer."""

import asyncio
import functools

import pytest
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.services import loader as loader_mod
from app.services.loader import Loader

INLINE = InlineKeyboardMarkup([[InlineKeyboardButton("✅", callback_data="x")]])
REPLY_KB = ReplyKeyboardMarkup([[KeyboardButton("Сейчас")]])


class _Sent:
    """A message the bot sent: it can be edited and deleted."""

    def __init__(self, chat, text, markup, *, edit_fails=False):
        self.chat, self.text, self.markup = chat, text, markup
        self.edit_fails = edit_fails
        self.deleted = False
        self.message_id = len(chat.sent) + 1

    async def edit_text(self, text, reply_markup=None):
        if self.edit_fails:
            raise RuntimeError("message can't be edited")
        self.text, self.markup = text, reply_markup
        return self

    async def delete(self):
        self.deleted = True


class _Chat:
    """The user's message; ``sent`` is everything the bot put on screen."""

    def __init__(self, *, send_takes=0.0, edit_fails=False):
        self.sent = []
        self.sends = []  # every text the bot sent, before any edit
        self.send_takes = send_takes
        self.edit_fails = edit_fails

    async def reply_text(self, text, reply_markup=None):
        # Telegram shows the message before the bot hears back: a cancelled
        # wait does not take it off the screen.
        sent = _Sent(self, text, reply_markup, edit_fails=self.edit_fails)
        self.sent.append(sent)
        self.sends.append(text)
        await asyncio.sleep(self.send_takes)
        return sent

    def screen(self):
        return [(m.text, m.markup) for m in self.sent if not m.deleted]


@pytest.fixture(autouse=True)
def short_delay(monkeypatch):
    monkeypatch.setattr(loader_mod, "DELAY", 0.02)


SLOW, FAST = 0.08, 0.005  # around DELAY = 0.02; fast work still awaits


def _sync(test):
    """The suite has no pytest-asyncio: each test runs its own event loop."""

    @functools.wraps(test)
    def run(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))

    return run


@_sync
async def test_fast_work_never_shows_the_loader():
    chat = _Chat()
    async with Loader(chat, "⏳ Считаю…") as loader:
        await asyncio.sleep(FAST)
        await loader.reply("готово", reply_markup=INLINE)
    await asyncio.sleep(SLOW)  # a loader task left running would send now
    assert chat.sends == ["готово"]  # no «⏳», not even one edited away


@_sync
async def test_slow_work_shows_the_loader_and_becomes_the_answer():
    chat = _Chat()
    async with Loader(chat, "⏳ Считаю…") as loader:
        await asyncio.sleep(SLOW)
        assert chat.screen() == [("⏳ Считаю…", None)]
        sent = await loader.reply("готово", reply_markup=INLINE)
    assert chat.screen() == [("готово", INLINE)]
    assert sent is chat.sent[0]  # the caller keeps the edited message's id


@_sync
async def test_a_reply_keyboard_answer_replaces_the_loader():
    chat = _Chat()
    async with Loader(chat, "⏳ Считаю…") as loader:
        await asyncio.sleep(SLOW)
        await loader.reply("Когда?", reply_markup=REPLY_KB)
    assert chat.screen() == [("Когда?", REPLY_KB)]
    assert chat.sent[0].deleted


@_sync
async def test_a_failed_edit_still_delivers_the_answer():
    chat = _Chat(edit_fails=True)
    async with Loader(chat, "⏳ Считаю…") as loader:
        await asyncio.sleep(SLOW)
        await loader.reply("готово")
    assert chat.screen() == [("готово", None)]


@_sync
async def test_a_second_answer_goes_below_the_first():
    chat = _Chat()
    async with Loader(chat, "⏳ Спрашиваю…") as loader:
        await asyncio.sleep(SLOW)
        await loader.reply("⚠️ первое")
        await loader.reply("второе")
    assert chat.screen() == [("⚠️ первое", None), ("второе", None)]


@_sync
async def test_drop_takes_the_loader_away():
    chat = _Chat()
    async with Loader(chat, "⏳ Считаю…") as loader:
        await asyncio.sleep(SLOW)
        await loader.drop()
    assert chat.screen() == []


@_sync
async def test_a_loader_already_on_its_way_is_not_left_behind():
    # The work ends while the loader's send is in flight: the send finishes
    # and the answer is edited into it — never a lone «⏳» above the answer.
    chat = _Chat(send_takes=0.05)
    async with Loader(chat, "⏳ Считаю…") as loader:
        await asyncio.sleep(0.04)  # past DELAY, the send not back yet
        await loader.reply("готово")
    assert chat.screen() == [("готово", None)]


@_sync
async def test_work_that_raises_leaves_no_loader_coming():
    chat = _Chat()
    with pytest.raises(ValueError):
        async with Loader(chat, "⏳ Считаю…"):
            raise ValueError("boom")
    await asyncio.sleep(SLOW)
    assert chat.screen() == []
