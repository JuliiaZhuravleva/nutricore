"""A dish's emoji from its name (owner 2026-11-10), and how its button shows it."""

import asyncio

import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.services import dish_emoji
from tests.test_custom_emoji import _on, _sent


@pytest.mark.parametrize(
    "name, emoji",
    [
        ("картошка фри", "🍟"),  # the dish, before the potato it is made of
        ("наггетсы во фритюре", "🍗"),
        ("Twix с печеньем", "🍫"),  # a bar, not a biscuit
        ("салатные листья", "🥬"),  # leaves, not a salad
        ("салат с курицей", "🥗"),
        ("арахис сладкий чили", "🥜"),
        ("говяжья печень", "🥩"),
        ("Сёмга на гриле", "🐟"),  # ё in the name, е in a stem
        ("лепешка", "🍞"),  # е in the name, ё in the stem
        ("омлет с сыром", "🍳"),
        ("рис", "🍚"),
    ],
)
def test_a_name_finds_its_emoji(name, emoji):
    assert dish_emoji.of_name(name) == emoji


def test_a_stem_is_a_word_start_not_any_part():
    """«рис» finds «рисовая» but not the «рис» inside «барбарис»."""
    assert dish_emoji.of_name("рисовая каша") != dish_emoji.of_name("барбарис")
    assert dish_emoji.of_name("барбарис") != "🍚"


@pytest.mark.parametrize("name", [None, "", "   ", "что-то 123"])
def test_nothing_known_is_the_plate(name):
    assert dish_emoji.of_name(name) == dish_emoji.DEFAULT


def test_the_lead_is_the_emoji_and_a_no_break_space():
    assert dish_emoji.SEP == " "
    assert dish_emoji.lead("рис") == "🍚 "


def test_a_dish_button_keeps_its_stock_emoji_with_custom_emoji_on():
    """🍳 is in the drawn set here: a dish's picture still is not made an icon, so a
    list never mixes drawn and stock pictures. A star before it still is."""
    bot, transport = _on()
    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    dish_emoji.lead("омлет") + "Омлет", callback_data="a"
                )
            ],
            [
                InlineKeyboardButton(
                    "⭐ " + dish_emoji.lead("омлет") + "Омлет", callback_data="b"
                )
            ],
            # Control: the same mark with a plain space does become the icon.
            [InlineKeyboardButton("🍳 Омлет", callback_data="c")],
        ]
    )

    asyncio.run(bot.send_message(42, "Блюда", reply_markup=markup))

    rows = _sent(transport)[1]["reply_markup"]["inline_keyboard"]
    assert rows[0][0] == {"text": "🍳 Омлет", "callback_data": "a"}
    assert rows[1][0]["text"] == "🍳 Омлет"
    assert rows[1][0]["icon_custom_emoji_id"] == "id-star"
    assert rows[2][0] == {
        "text": "Омлет",
        "callback_data": "c",
        "icon_custom_emoji_id": "id-pan",
    }
