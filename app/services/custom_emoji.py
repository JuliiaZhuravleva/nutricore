"""Custom emoji: the bot's own marks drawn as pictures from a sticker set.

Off unless ``CUSTOM_EMOJI`` names custom-emoji sticker sets ("set1,set2"). The
sets are read once at startup with getStickerSet; each sticker maps its emoji
(without U+FE0F) to its id, the first set winning. Unset, malformed or any set
unreadable: the module is off and every call leaves the bot exactly as built.

On, ``EmojiBot`` — the Application's bot — rewrites each call just before it
is sent, so every handler's message passes through it unchanged in the code:

- a mark in a message text or caption is drawn as its picture, the glyph kept
  as the fallback: a ``custom_emoji`` entity over it in plain text,
  ``<tg-emoji>`` in HTML, at most ``MAX_PER_MESSAGE``. MarkdownV2, or a text
  that already carries its own entities, is left as it is;
- an inline button whose label starts with a mark and a space shows the
  picture as its icon and loses the mark. A label that is only a mark keeps
  it: a client without icons would show an empty button;
- reply keyboards are never touched: a tapped one comes back as the user's
  text, and the menu handlers match that text literally.

A call Telegram refuses for its custom emoji is sent again plain. If that goes
through, the module is off until a restart and the owner gets one message; if
it fails too, its own error is raised. Any other refusal is raised as it is.
"""

import logging
import re
from typing import Optional

from telegram import InlineKeyboardMarkup, InputMediaPhoto
from telegram.error import BadRequest, TelegramError
from telegram.ext import ExtBot
from telegram.request import HTTPXRequest

from app.services.admin_notify import notify_admins

logger = logging.getLogger(__name__)

MAX_PER_MESSAGE = 100  # Telegram's cap on custom emoji in one message
_VS16 = "\ufe0f"
_SET_NAME = re.compile(r"[A-Za-z0-9_]+")
_REFUSAL_WORDS = ("document_invalid", "emoji", "icon", "can't parse entities")
_TAG = re.compile(r"(<[^>]*>)")
_RAW_TAGS = frozenset({"code", "pre", "tg-emoji"})  # shown as typed, or drawn

# The calls whose text the module draws in: endpoint -> (text, its entities).
_TEXT_FIELDS = {
    "sendMessage": ("text", "entities"),
    "editMessageText": ("text", "entities"),
    "sendPhoto": ("caption", "caption_entities"),
    "sendDocument": ("caption", "caption_entities"),
    "sendVideo": ("caption", "caption_entities"),
    "sendAnimation": ("caption", "caption_entities"),
    "editMessageCaption": ("caption", "caption_entities"),
}


def parse_sets(value: Optional[str]) -> list:
    """The set names in their order; [] when unset. Raises ValueError on a
    malformed value."""
    names = [part.strip() for part in (value or "").split(",") if part.strip()]
    for name in names:
        if not _SET_NAME.fullmatch(name):
            raise ValueError(f"CUSTOM_EMOJI: cannot read {name!r}; expected set1,set2")
    return names


def _utf16(text: str) -> int:
    """Telegram counts entity offsets in UTF-16 code units."""
    return len(text.encode("utf-16-le")) // 2


class CustomEmoji:
    """The glyph -> custom emoji id table and the rewrite it drives."""

    def __init__(self) -> None:
        self._table: dict = {}
        self._pattern: Optional[re.Pattern] = None
        self._lead: Optional[re.Pattern] = None
        self.on = False
        # Why a configured module is off, for the owner; None when on or unset.
        self.problem: Optional[str] = None

    @property
    def count(self) -> int:
        return len(self._table)

    async def load(self, bot, value: Optional[str]) -> None:
        """Read the sets; any failure turns the module off with ``problem``
        set, never stops the bot."""
        self._off(None)
        try:
            names = parse_sets(value)
        except ValueError as exc:
            self._off(str(exc))
            return
        if not names:
            return
        table: dict = {}
        for name in names:
            found = await self._read(bot, name)
            if found is None:
                return
            for glyph, custom_id in found.items():
                table.setdefault(glyph, custom_id)
        if not table:
            self._off("the sets hold no custom emoji")
            return
        self._table = table
        glyphs = "|".join(re.escape(g) for g in sorted(table, key=len, reverse=True))
        # Not inside a joined sequence; the selector is taken whole, then no
        # join or skin tone may follow.
        self._pattern = re.compile(
            f"(?<!\u200d)({glyphs}){_VS16}?+(?![\u200d\U0001f3fb-\U0001f3ff])"
        )
        self._lead = re.compile(f"({glyphs}){_VS16}?+ (?=\\S)")
        self.on = True
        logger.info("Custom emoji on: %d marks from %s", len(table), ", ".join(names))

    async def _read(self, bot, name: str) -> Optional[dict]:
        try:
            found = await bot.get_sticker_set(name)
        except TelegramError as exc:
            self._off(f"{name}: {exc.message}")
            return None
        except Exception as exc:  # whatever breaks here, the bot starts plain
            self._off(f"{name}: {type(exc).__name__}")
            return None
        if found.sticker_type != "custom_emoji":
            self._off(f"{name}: not a custom emoji set")
            return None
        table: dict = {}
        for sticker in found.stickers:
            glyph = (sticker.emoji or "").replace(_VS16, "")
            if glyph and sticker.custom_emoji_id:
                table.setdefault(glyph, sticker.custom_emoji_id)
        return table

    def _off(self, problem: Optional[str]) -> None:
        self._table, self._pattern, self._lead = {}, None, None
        self.on = False
        self.problem = problem[:200] if problem else None
        if problem:
            logger.warning("Custom emoji off: %s", self.problem)

    def refused(self, message: str) -> None:
        """Telegram refused a drawn call: plain until a restart."""
        self._off(f"Telegram refused: {message}")

    # ── the rewrite ──────────────────────────────────────────────────────────

    def rewrite(self, endpoint: str, data: dict) -> dict:
        """The call's parameters with the marks drawn — ``data`` itself when
        nothing changes (always, while off). ``data`` is never modified."""
        if not self.on:
            return data
        update: dict = {}
        fields = _TEXT_FIELDS.get(endpoint)
        if fields is not None:
            field, entities_field = fields
            text = data.get(field)
            mode = data.get("parse_mode")
            if isinstance(text, str) and not data.get(entities_field):
                if mode is None:
                    entities = self._entities(text)
                    if entities:
                        update[entities_field] = entities
                elif mode == "HTML":
                    html = self._html(text)
                    if html != text:
                        update[field] = html
        media = data.get("media")
        if endpoint == "editMessageMedia" and isinstance(media, InputMediaPhoto):
            # A swapped card keeps the drawn marks its caption was sent with.
            if media.caption and media.parse_mode == "HTML":
                caption = self._html(media.caption)
                if caption != media.caption:
                    update["media"] = InputMediaPhoto(
                        media.media, caption=caption, parse_mode=media.parse_mode
                    )
        markup = self._markup(data.get("reply_markup"))
        if markup is not None:
            update["reply_markup"] = markup
        return {**data, **update} if update else data

    def _entities(self, text: str) -> list:
        entities = []
        for match in self._pattern.finditer(text):
            if len(entities) >= MAX_PER_MESSAGE:
                break
            entities.append(
                {
                    "type": "custom_emoji",
                    "offset": _utf16(text[: match.start()]),
                    "length": _utf16(match.group(0)),
                    "custom_emoji_id": self._table[match.group(1)],
                }
            )
        return entities

    def _html(self, text: str) -> str:
        left = MAX_PER_MESSAGE
        out = []
        raw = 0  # inside <code> or <pre>: shown as typed
        for part in _TAG.split(text):
            if part.startswith("<"):
                inner = part.strip("</>")
                name = inner.split(maxsplit=1)[0].lower() if inner else ""
                if name in _RAW_TAGS:
                    raw += -1 if part.startswith("</") else 1
                out.append(part)
                continue
            if raw > 0 or left <= 0:
                out.append(part)
                continue
            part, n = self._pattern.subn(self._swap, part, count=left)
            left -= n
            out.append(part)
        return "".join(out)

    def _swap(self, match: re.Match) -> str:
        custom_id = self._table[match.group(1)]
        return f'<tg-emoji emoji-id="{custom_id}">{match.group(0)}</tg-emoji>'

    def _markup(self, markup) -> Optional[dict]:
        """The inline keyboard with its leading marks turned into icons, or
        None when there is nothing to change."""
        if isinstance(markup, InlineKeyboardMarkup):
            markup = markup.to_dict()
        if not isinstance(markup, dict) or "inline_keyboard" not in markup:
            return None
        changed = False
        rows = []
        for row in markup["inline_keyboard"]:
            new_row = []
            for button in row:
                lead = None
                if isinstance(button, dict) and not button.get("icon_custom_emoji_id"):
                    lead = self._lead.match(button.get("text") or "")
                if lead is None:
                    new_row.append(button)
                    continue
                changed = True
                new_row.append(
                    {
                        **button,
                        "text": button["text"][lead.end() :],
                        "icon_custom_emoji_id": self._table[lead.group(1)],
                    }
                )
            rows.append(new_row)
        return {**markup, "inline_keyboard": rows} if changed else None


class EmojiBot(ExtBot):
    """The Application's bot: each call goes out through ``CustomEmoji``."""

    __slots__ = ("_emoji",)

    def __init__(self, *args, emoji: CustomEmoji, **kwargs):
        super().__init__(*args, **kwargs)
        self._emoji = emoji

    @property
    def emoji(self) -> CustomEmoji:
        return self._emoji

    async def _do_post(self, endpoint: str, data: dict, **kwargs):
        rich = self._emoji.rewrite(endpoint, data)
        if rich is data:
            return await super()._do_post(endpoint, data, **kwargs)
        try:
            return await super()._do_post(endpoint, rich, **kwargs)
        except BadRequest as exc:
            if not any(word in exc.message.lower() for word in _REFUSAL_WORDS):
                raise
            # Its own refusal is raised and the module stays on.
            result = await super()._do_post(endpoint, data, **kwargs)
            self._emoji.refused(exc.message)
            await notify_admins(
                "⚠️ Кастомные эмодзи выключены до перезапуска бота: Telegram "
                f"не принял сообщение с ними ({exc.message}). "
                "Бот работает с обычными эмодзи."
            )
            return result


def build_bot(token: str, emoji: CustomEmoji) -> EmojiBot:
    """The bot ``ApplicationBuilder`` would make from a bare token, as an
    ``EmojiBot``: the same two connection pools (256 for calls, 1 for
    getUpdates)."""
    return EmojiBot(
        token=token,
        request=HTTPXRequest(connection_pool_size=256),
        get_updates_request=HTTPXRequest(connection_pool_size=1),
        emoji=emoji,
    )
