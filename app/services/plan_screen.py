"""What a plan shows: its lines and its buttons (no state moves; those are
``crud_message_plan``, driven by ``plan_service``)."""

from __future__ import annotations

CALLBACK_PREFIX = "pl:"
READING_LINE = "Сообщение получено, читаю…"
NOTHING_LINE = (
    "Не нашлось блюда для этой команды — напиши название, как в списке 🍲 Блюда."
)
SAME_TOAST = "Уже сделано."
GONE_TOAST = "Это сообщение уже не изменить."


def answer_markup(plan_id: int, changed: bool):
    """✅ ОК first, then ↩️: a result holding an undo can be put away."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    from app.services.close_row import ok_row
    from app.services.dish_tidy_screen import UNDO_ALL

    if not changed:
        return None
    undo = InlineKeyboardButton(UNDO_ALL, callback_data=f"{CALLBACK_PREFIX}u:{plan_id}")
    return InlineKeyboardMarkup([ok_row(), [undo]])


def fail_line(plan_id: int):
    """The line of a plan that wrote nothing, with 🔁."""
    from app.services import capture

    return capture.Line(capture.PLAN_FAILED, capture.plan_retry_markup(plan_id))


def nothing_line():
    from app.services import capture

    return capture.Line(NOTHING_LINE)


def undo_head(taken: list, labels: list, refused: list) -> str:
    """What the answer adds after an undo: what came back, then what stayed."""
    from app.services.dish_tidy_screen import NOT_BACK, UNDONE_NAMED

    what = ", ".join([*taken, *labels])
    head = UNDONE_NAMED.format(what=what) if what else "↩️"  # all refused: named next
    if refused:
        names = ", ".join(f"«{n}»" for n in refused)
        them = "его" if len(refused) == 1 else "их"
        head += NOT_BACK.format(names=names, them=them)
    return head
