"""✅ ОК: a result that offers a way back also offers to put that way back away.

A cancel button left hanging in the chat can be tapped by accident long afterwards, and
knowing that is a small tension of its own. So a message that ends a job and still holds an
undo comes with this row first: the tap takes the buttons off the message, text stays.

Screens that go on living, with tabs or a way to other screens, change under the thumb
anyway and do not need it.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton

LABEL = "✅ ОК"
CALLBACK = "ok:close"
PATTERN = f"^{CALLBACK}$"


def ok_row() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(LABEL, callback_data=CALLBACK)]
