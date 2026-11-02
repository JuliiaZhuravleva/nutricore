"""The ``usr:`` buttons of the owner's screens, which several screens must name.

Here, and free of those screens, so that the owner's copy of a feedback message can carry
a person's card, and that card's screen can re-send the copy.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton

CALLBACK_PREFIX = "usr:"
LIST = CALLBACK_PREFIX + "l:"  # + the page
CARD = CALLBACK_PREFIX + "p:"  # + the telegram id
CARD_HERE = CALLBACK_PREFIX + "c:"  # the same card as a message of its own
NAME_CHARS = 12  # what fits a button at three in a row


def button(label: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data=data)


def back_to_list() -> list:
    return [button("⬅️ Пользователи", LIST + "0")]


def card_button(name: str, telegram_id: int) -> InlineKeyboardButton:
    """«👤 Саша»: their card, on the screen this button is on."""
    return button(f"👤 {name[:NAME_CHARS]}", f"{CARD}{telegram_id}")


def card_below(name: str, telegram_id: int) -> InlineKeyboardButton:
    """«👤 Саша» under the owner's copy of a message: the card comes as a new message,
    since an edit would write it over the person's words."""
    return button(f"👤 {name[:NAME_CHARS]}", f"{CARD_HERE}{telegram_id}")


def pager(prefix: str, page: int, pages: int) -> list:
    """‹ 2/3 ›: three buttons that keep their places. The ends lead to the same page."""
    return [
        button("‹", f"{prefix}{max(page - 1, 0)}"),
        button(f"{page + 1}/{pages}", f"{prefix}{page}"),
        button("›", f"{prefix}{min(page + 1, pages - 1)}"),
    ]
