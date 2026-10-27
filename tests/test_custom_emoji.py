"""Custom emoji (``app.services.custom_emoji``): the bot's marks as pictures.

The bot here is the real ``EmojiBot`` over a fake HTTP transport, so every
assertion reads the parameters as PTB serialised them for Telegram — and the
transport can refuse a call the way the Bot API does.
"""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import Application, ExtBot
from telegram.request import BaseRequest

from app.core.config import settings
from app.services import custom_emoji as ce
from app.services import telegram as tg

_MESSAGE = {"message_id": 1, "date": 0, "chat": {"id": 42, "type": "private"}}


def _sticker(emoji, custom_id):
    return {
        "file_id": f"f{custom_id}",
        "file_unique_id": f"u{custom_id}",
        "type": "custom_emoji",
        "width": 100,
        "height": 100,
        "is_animated": False,
        "is_video": False,
        "emoji": emoji,
        "custom_emoji_id": custom_id,
    }


_SETS = {
    "nc_marks": {
        "name": "nc_marks",
        "title": "marks",
        "sticker_type": "custom_emoji",
        "stickers": [
            _sticker("✏️", "id-pencil"),  # with U+FE0F, as Telegram lists it
            _sticker("✅", "id-check"),
            _sticker("🔥", "id-fire"),
            _sticker("⭐", "id-star"),
            _sticker("🍳", "id-pan"),
            _sticker("👍", "id-thumb"),
        ],
    },
    "nc_more": {
        "name": "nc_more",
        "title": "more",
        "sticker_type": "custom_emoji",
        "stickers": [_sticker("✅", "id-check-2"), _sticker("📊", "id-chart")],
    },
    "nc_plain": {
        "name": "nc_plain",
        "title": "regular",
        "sticker_type": "regular",
        "stickers": [],
    },
}


class _Transport(BaseRequest):
    """The Bot API on a string: records each call, answers like Telegram.

    ``refuse(endpoint, params)`` returns an error description to refuse a call.
    """

    def __init__(self, refuse=None):
        self.calls = []
        self.refuse = refuse

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    @property
    def read_timeout(self):
        return 5.0

    async def do_request(self, url, method, request_data=None, **kwargs):
        endpoint = url.rsplit("/", 1)[-1]
        params = dict(request_data.json_parameters) if request_data else {}
        self.calls.append((endpoint, params))
        description = self.refuse(endpoint, params) if self.refuse else None
        if description:
            body = {"ok": False, "error_code": 400, "description": description}
            return 400, json.dumps(body).encode()
        if endpoint == "getStickerSet":
            name = params["name"]
            if name not in _SETS:
                body = {
                    "ok": False,
                    "error_code": 400,
                    "description": "Bad Request: STICKERSET_INVALID",
                }
                return 400, json.dumps(body).encode()
            return 200, json.dumps({"ok": True, "result": _SETS[name]}).encode()
        return 200, json.dumps({"ok": True, "result": _MESSAGE}).encode()


def _bot(transport, emoji=None):
    return ce.EmojiBot(
        "123:fake",
        request=transport,
        get_updates_request=transport,
        emoji=emoji or ce.CustomEmoji(),
    )


def _on(value="nc_marks,nc_more", transport=None):
    transport = transport or _Transport()
    bot = _bot(transport)
    asyncio.run(bot.emoji.load(bot, value))
    transport.calls.clear()
    return bot, transport


def _sent(transport, index=-1):
    endpoint, params = transport.calls[index]
    return endpoint, {
        k: json.loads(v) if v[:1] in "[{" else v for k, v in params.items()
    }


def _drawn(text, entity):
    """The text an entity covers, counted in UTF-16 like Telegram does."""
    units = text.encode("utf-16-le")
    start, end = entity["offset"] * 2, (entity["offset"] + entity["length"]) * 2
    return units[start:end].decode("utf-16-le")


# --- reading the sets --------------------------------------------------------------


def test_parse_sets_reads_names_and_refuses_a_malformed_one():
    assert ce.parse_sets(" nc_marks , nc_more ") == ["nc_marks", "nc_more"]
    assert ce.parse_sets("") == [] and ce.parse_sets(None) == []
    with pytest.raises(ValueError):
        ce.parse_sets("nc-marks")


def test_load_maps_glyphs_without_the_selector_and_the_first_set_wins():
    bot, _ = _on()

    assert bot.emoji.on and bot.emoji.problem is None
    assert bot.emoji.count == 7
    assert bot.emoji._table["✏"] == "id-pencil"
    assert bot.emoji._table["✅"] == "id-check"  # nc_marks, not nc_more


def test_unset_is_off_without_a_single_call():
    transport = _Transport()
    bot = _bot(transport)

    asyncio.run(bot.emoji.load(bot, None))

    assert not bot.emoji.on and bot.emoji.problem is None
    assert transport.calls == []


@pytest.mark.parametrize(
    "value,problem",
    [
        ("nc_marks,nc_missing", "nc_missing: stickerset_invalid"),
        ("nc_plain", "nc_plain: not a custom emoji set"),
        ("nc-marks", "cannot read 'nc-marks'"),
    ],
)
def test_a_named_set_that_cannot_be_read_turns_the_module_off(value, problem):
    bot, _ = _on(value)

    assert not bot.emoji.on
    assert problem in bot.emoji.problem.lower()


# --- message text --------------------------------------------------------------------


def test_off_every_call_goes_out_exactly_as_the_plain_bot_sends_it():
    markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Сохранить", callback_data="s")]]
    )
    plain_transport, emoji_transport = _Transport(), _Transport()
    plain = ExtBot(
        "123:fake", request=plain_transport, get_updates_request=plain_transport
    )
    off = _bot(emoji_transport)

    for bot in (plain, off):
        asyncio.run(bot.send_message(42, "✅ Сохранено 🔥", reply_markup=markup))

    assert emoji_transport.calls == plain_transport.calls


def test_plain_text_gets_an_entity_over_each_mark_counted_in_utf16():
    bot, transport = _on()
    text = "🔥 Калории: ≈ 350 ккал\n✏️ Поправить — ✅"

    asyncio.run(bot.send_message(42, text))

    endpoint, params = _sent(transport)
    assert endpoint == "sendMessage"
    assert params["text"] == text  # the glyphs stay, as the fallback
    entities = params["entities"]
    assert [e["custom_emoji_id"] for e in entities] == [
        "id-fire",
        "id-pencil",
        "id-check",
    ]
    assert [_drawn(text, e) for e in entities] == ["🔥", "✏️", "✅"]
    assert entities[0] == {
        "type": "custom_emoji",
        "offset": 0,
        "length": 2,  # a surrogate pair
        "custom_emoji_id": "id-fire",
    }


def test_a_mark_inside_a_joined_emoji_or_with_a_skin_tone_is_left_alone():
    bot, transport = _on()

    asyncio.run(bot.send_message(42, "👩‍🍳 повар, 👍🏻 ок"))

    _endpoint, params = _sent(transport)
    assert "entities" not in params


def test_html_text_gets_tg_emoji_outside_tags_and_code():
    bot, transport = _on()
    text = '<b>⭐ Избранное</b> <a href="https://x/✅">ссылка</a> <code>✅</code>'

    asyncio.run(bot.send_message(42, text, parse_mode="HTML"))

    _endpoint, params = _sent(transport)
    assert params["text"] == (
        '<b><tg-emoji emoji-id="id-star">⭐</tg-emoji> Избранное</b> '
        '<a href="https://x/✅">ссылка</a> <code>✅</code>'
    )


def test_markdown_or_own_entities_are_left_as_they_are():
    bot, transport = _on()

    asyncio.run(bot.send_message(42, "✅ *да*", parse_mode="MarkdownV2"))
    asyncio.run(
        bot.send_message(
            42, "✅ да", entities=[{"type": "bold", "offset": 2, "length": 2}]
        )
    )

    assert _sent(transport, 0)[1]["text"] == "✅ *да*"
    assert "entities" not in _sent(transport, 0)[1]
    assert _sent(transport, 1)[1]["entities"] == [
        {"type": "bold", "offset": 2, "length": 2}
    ]


def test_a_caption_and_an_edit_are_drawn_a_toast_is_not():
    bot, transport = _on()

    asyncio.run(bot.send_photo(42, "PHOTO_ID", caption="🔥 фото"))
    asyncio.run(bot.edit_message_text("✅ Сохранено", chat_id=42, message_id=1))
    asyncio.run(bot.answer_callback_query("q1", text="✅ Готово"))

    assert _sent(transport, 0)[1]["caption_entities"][0]["custom_emoji_id"] == "id-fire"
    assert _sent(transport, 1)[1]["entities"][0]["custom_emoji_id"] == "id-check"
    endpoint, toast = _sent(transport, 2)
    assert endpoint == "answerCallbackQuery" and toast["text"] == "✅ Готово"
    assert "entities" not in toast


def test_at_most_a_hundred_marks_per_message():
    bot, transport = _on()

    asyncio.run(bot.send_message(42, "🔥" * 150))

    assert len(_sent(transport)[1]["entities"]) == ce.MAX_PER_MESSAGE


# --- buttons -------------------------------------------------------------------------


def test_a_leading_mark_on_an_inline_button_becomes_its_icon():
    bot, transport = _on()
    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Сохранить", callback_data="s", api_kwargs={"style": "success"}
                ),
                InlineKeyboardButton("✏️ Поправить вес", callback_data="w"),
            ],
            [
                InlineKeyboardButton("⭐", callback_data="only"),  # keeps its mark
                InlineKeyboardButton("✖️ Отмена", callback_data="x"),  # not in set
                InlineKeyboardButton("Сейчас ✅", callback_data="t"),  # not leading
            ],
        ]
    )

    asyncio.run(bot.send_message(42, "Всё верно?", reply_markup=markup))

    rows = _sent(transport)[1]["reply_markup"]["inline_keyboard"]
    assert rows[0][0] == {
        "text": "Сохранить",
        "callback_data": "s",
        "style": "success",
        "icon_custom_emoji_id": "id-check",
    }
    assert rows[0][1]["text"] == "Поправить вес"
    assert rows[0][1]["icon_custom_emoji_id"] == "id-pencil"
    assert [b["text"] for b in rows[1]] == ["⭐", "✖️ Отмена", "Сейчас ✅"]
    assert not any("icon_custom_emoji_id" in b for b in rows[1])


def test_a_reply_keyboard_is_never_touched():
    # A tapped reply button comes back as the user's text; the menu handlers
    # match "📊 Статистика" literally.
    bot, transport = _on()

    asyncio.run(
        bot.send_message(
            42, "Меню", reply_markup=ReplyKeyboardMarkup([["📊 Статистика"]])
        )
    )

    keyboard = _sent(transport)[1]["reply_markup"]["keyboard"]
    assert keyboard == [[{"text": "📊 Статистика"}]]


# --- Telegram refuses ----------------------------------------------------------------


def _refuse_drawn(endpoint, params):
    drawn = "custom_emoji" in params.get("entities", "") or "icon_custom" in params.get(
        "reply_markup", ""
    )
    return "Bad Request: DOCUMENT_INVALID" if drawn else None


def test_a_refused_drawn_call_goes_out_plain_and_the_module_turns_off(monkeypatch):
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(ce, "notify_admins", notify)
    bot, transport = _on()
    transport.refuse = _refuse_drawn

    message = asyncio.run(bot.send_message(42, "✅ Сохранено"))

    assert message.message_id == 1
    assert len(transport.calls) == 2
    assert "entities" in _sent(transport, 0)[1]
    assert "entities" not in _sent(transport, 1)[1]
    assert not bot.emoji.on
    assert "document_invalid" in bot.emoji.problem.lower()
    notify.assert_awaited_once()
    # From now on: plain, one call each.
    asyncio.run(bot.send_message(42, "✅ ещё"))
    assert len(transport.calls) == 3
    notify.assert_awaited_once()


def test_when_the_plain_resend_fails_too_its_own_error_is_raised(monkeypatch):
    notify = AsyncMock()
    monkeypatch.setattr(ce, "notify_admins", notify)
    bot, transport = _on()
    transport.refuse = lambda e, p: (
        "Bad Request: DOCUMENT_INVALID"
        if "entities" in p
        else "Bad Request: chat not found"
    )

    with pytest.raises(BadRequest, match="hat not found"):
        asyncio.run(bot.send_message(42, "✅"))

    assert len(transport.calls) == 2
    assert bot.emoji.on
    notify.assert_not_awaited()


def test_any_other_refusal_is_raised_without_a_resend(monkeypatch):
    notify = AsyncMock()
    monkeypatch.setattr(ce, "notify_admins", notify)
    bot, transport = _on()
    transport.refuse = lambda e, p: "Bad Request: chat not found"

    with pytest.raises(BadRequest):
        asyncio.run(bot.send_message(42, "✅"))

    assert len(transport.calls) == 1
    assert bot.emoji.on
    notify.assert_not_awaited()


# --- wiring --------------------------------------------------------------------------


def test_the_application_bot_is_an_emoji_bot_with_the_builders_pools(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:fake")
    application = tg.create_bot_application()
    default = Application.builder().token("123:fake").build()

    assert isinstance(application.bot, ce.EmojiBot)
    assert application.post_init is tg._load_custom_emoji
    for ours, theirs in zip(application.bot._request, default.bot._request):
        assert ours._client_kwargs == theirs._client_kwargs


def test_startup_tells_the_owner_when_named_sets_cannot_be_read(monkeypatch):
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(tg, "notify_admins", notify)
    transport = _Transport()
    application = type("App", (), {"bot": _bot(transport)})()

    monkeypatch.setattr(settings, "CUSTOM_EMOJI", "nc_missing")
    asyncio.run(tg._load_custom_emoji(application))
    assert "nc_missing" in notify.call_args.args[0]

    notify.reset_mock()
    monkeypatch.setattr(settings, "CUSTOM_EMOJI", "nc_marks")
    asyncio.run(tg._load_custom_emoji(application))
    assert application.bot.emoji.on
    notify.assert_not_awaited()


def test_unset_at_startup_says_nothing_and_calls_nothing(monkeypatch):
    notify = AsyncMock()
    monkeypatch.setattr(tg, "notify_admins", notify)
    monkeypatch.setattr(settings, "CUSTOM_EMOJI", None)
    transport = _Transport()
    application = type("App", (), {"bot": _bot(transport)})()

    asyncio.run(tg._load_custom_emoji(application))

    notify.assert_not_awaited()
    assert transport.calls == []
