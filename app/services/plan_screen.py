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
TAP_FAILED = "Не получилось, попробуй ещё раз."
CLOSED_LINE = "Вопрос закрыт: прошло 2 дня. Ничего не изменено."


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


# --- the questions of a plan (D3): buttons only ----------------------------------------

QUESTION_PREFIX = f"{CALLBACK_PREFIX}c:"  # pl:c:<plan>:<q>:<o>
UNDO_ONE_PREFIX = f"{CALLBACK_PREFIX}q:"  # pl:q:<plan>:<q>
ALL = "a"  # «Все»
YES = "y"
NO = "n"
ALL_LABEL = "Все"
NO_LABEL = "✖ Не надо"
DECLINED_LINE = "Оставляю как есть, не трогаю."
ASKS_FOLLOW = "Нужен твой выбор — вопросы ниже."
LABEL_MAX = 60  # a button's text, cut with «…» past this

# verb → (mark, what the question asks to do)
_PICK_VERB = {
    "star": ("⭐", "добавить в избранное"),
    "unstar": ("⭐", "убрать из избранного"),
    "hide": ("🍲", "убрать из блюд"),
    "merge": ("🍲", "объединить"),
    "rename": ("🍲", "переименовать"),
}
PICK_QUESTION = "{mark} {which} {name} {what}?"
ASK_MERGE = "Объединить {names} в одно блюдо?\nЗаписи останутся, отменить можно."
ASK_STAR = "Добавить {names} в избранное?"
ASK_UNSTAR = "Убрать {names} из избранного?"
ASK_HIDE = "Убрать {names} из блюд?"
ASK_RENAME = "Переименовать {names} в «{new}»?"
_ASK_TEXT = {
    "merge": ASK_MERGE,
    "star": ASK_STAR,
    "unstar": ASK_UNSTAR,
    "hide": ASK_HIDE,
    "rename": ASK_RENAME,
}
YES_LABEL = {
    "merge": "Да, объединить",
    "star": "Да, добавить",
    "unstar": "Да, убрать",
    "hide": "Да, убрать",
    "rename": "Да, переименовать",
}
ALL_VERBS = ("star", "unstar", "hide", "merge")  # «Все» is offered for these only


def _which(name: str) -> tuple:
    """(the question word, the name as it follows it): «Какую гречку» (also from
    «гречка»), «Какой суп», «Какое молоко». A name of one word only is inflected."""
    name = name.strip()
    end = name.casefold().split()[0][-1:] if name else ""
    single = len(name.split()) == 1
    if end in ("а", "я") and single:
        return "Какую", name[:-1] + ("у" if end == "а" else "ю")
    if end in ("а", "я", "у", "ю"):
        return "Какую", name
    if end in ("о", "е"):
        return "Какое", name
    if end in ("ы", "и"):
        return "Какие", name
    return "Какой", name


def _names(names: list) -> str:
    quoted = [f"«{n}»" for n in names]
    if len(quoted) > 1:
        return ", ".join(quoted[:-1]) + " и " + quoted[-1]
    return quoted[0]


def question_text(ask: dict, names: list, new: str = "") -> str:
    """The words of a question; ``names`` are an ask's dishes. A pick's head is the
    person's own word (``label``). Plain text: the caller escapes."""
    if ask["kind"] == "pick":
        mark, what = _PICK_VERB[ask["verb"]]
        which, name = _which(ask["label"])
        return PICK_QUESTION.format(mark=mark, which=which, name=name, what=what)
    return _ASK_TEXT[ask["verb"]].format(names=_names(names), new=new)


def _cut(label: str) -> str:
    return label if len(label) <= LABEL_MAX else label[: LABEL_MAX - 1] + "…"


def question_markup(plan_id: int, ask: dict, cand_names: list):
    """A pick: one button per dish by full name, «Все», «✖ Не надо». An ask: yes and
    no in one row."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    def button(label: str, option) -> InlineKeyboardButton:
        data = f"{QUESTION_PREFIX}{plan_id}:{ask['q']}:{option}"
        return InlineKeyboardButton(_cut(label), callback_data=data)

    no = button(NO_LABEL, NO)
    if ask["kind"] == "ask":
        return InlineKeyboardMarkup([[button(YES_LABEL[ask["verb"]], YES), no]])
    rows = [[button(name, i)] for i, name in enumerate(cand_names)]
    if ask["verb"] in ALL_VERBS and len(cand_names) > 1:
        rows.append([button(ALL_LABEL, ALL)])
    return InlineKeyboardMarkup([*rows, [no]])


def done_markup(plan_id: int, q: int, undoable: bool):
    """The answered question's own ✅ ОК first, then ↩️ for that answer only."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    from app.services.close_row import ok_row
    from app.services.dish_tidy_screen import UNDO_ALL

    if not undoable:
        return None
    undo = InlineKeyboardButton(
        UNDO_ALL, callback_data=f"{UNDO_ONE_PREFIX}{plan_id}:{q}"
    )
    return InlineKeyboardMarkup([ok_row(), [undo]])
