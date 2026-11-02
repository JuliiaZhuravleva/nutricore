"""💬 Фидбэк: a message from a person to the owner of the bot, and what goes with it.

    /feedback, ⚙️ Настройки or ℹ️ → <b>💬 Фидбэк</b> [👁 Что уйдёт] [✖️ Отмена]
    👁 tap → <b>👁 Что уйдёт</b>: the journal in the owner's words, [⬅️ Фидбэк]
    ✖️ tap → <i>✓ Фидбэк отменён</i> on top of ⚙️ Настройки
    the next message → <i>✓ Отправлено. Спасибо!</i> [↩️ Это была еда: записать]

Plain data, free of Telegram apart from the keyboard. The row comes first and the owner's
copy after it, in ``app.services.telegram``. Everything a person wrote or is called goes
through ``html.escape``, and every message has a plain twin in case the HTML is refused.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.crud.crud_action_event import crud_action_event
from app.crud.crud_feedback import crud_feedback
from app.models.feedback import PHOTO, TEXT, VOICE
from app.services import action_journal, invites, settings_screen
from app.services.admin_notify import Part
from app.services.day_card import local_time
from app.services.explain_screen import FEEDBACK_OPEN as OPEN
from app.services.explain_screen import FEEDBACK_TITLE as TITLE
from app.services.favorites import Screen
from app.services.owner_buttons import card_below

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "fb:"
_PREVIEW = "fb:see"
CANCEL = "fb:x"
_FOOD = "fb:food:"
LIMIT = (
    4096  # Telegram's, after the entities are parsed; UTF-16 is the safe way to count
)
JOURNAL_ACTIONS = 20
QUOTE_HEAD = "Последние действия"

PROMPT = (
    f"<b>{TITLE}</b>\n\n"
    "Напиши или наговори, что было непонятно и чего не хватает. Можно приложить "
    "скриншот.\n\n"
    "Следующее сообщение уйдёт владельцу бота, а не в дневник. С ним уйдут твои "
    "последние 20 действий: время и какие кнопки нажаты, без текста записей."
)
PREVIEW_TITLE = "👁 Что уйдёт"
NO_ACTIONS = "Пока ничего: ни одного действия не записано."
SENT = "<i>✓ Отправлено. Спасибо!</i>"
FOOD_BUTTON = "↩️ Это была еда: записать"
FOOD_DONE = "<i>✓ Записано в дневник.</i>"
FOOD_ALREADY = "Уже записано в дневник"
FOOD_LOCKED = "Запись в дневник сейчас закрыта"
FOOD_FAILED = "Не получилось, попробуй ещё раз"
NOT_YOURS = "Этого сообщения уже нет"
CANCELLED = "✓ Фидбэк отменён"  # the hub sets a notice in italics itself
UNSUPPORTED = "Подойдёт текст, голосовое или фото. Попробуй ещё раз."
NOT_SAVED = "Не получилось сохранить, сообщение не ушло. Отправь его ещё раз."
_VOICE_LINE = "🎙 голосовое (само сообщение — выше)"
_PHOTO_LINE = "📷 фото (само сообщение — выше)"
_NOT_HEARD = "Без расшифровки."
_WAS_FOOD = "↩️ Это оказалась еда, записано в дневник."


@dataclass(frozen=True)
class Replay:
    """A tap that holds up: put this message through the diary."""

    feedback_id: int
    original: dict
    sent: bool


def units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def kind_of(came_kind: str) -> str:
    """A screenshot sent as a file is a photo to whoever reads this."""
    return {"voice": VOICE, "text": TEXT}.get(came_kind, PHOTO)


def journal_lines(db, telegram_id: int, zone) -> list[str]:
    """The very lines the owner gets, for 👁 too. From closed lists only."""
    events = crud_action_event.last_actions(db, telegram_id, limit=JOURNAL_ACTIONS)
    return [action_journal.line(event, zone) for event in events]


def quote(lines: list[str]) -> str:
    """Collapsed, so that the person's own words are what is read first."""
    body = html.escape("\n".join([QUOTE_HEAD, *lines]), quote=False)
    return f"<blockquote expandable>{body}</blockquote>"


def take(db, came, original: dict):
    """The row, committed. The journal is a courtesy: if it cannot be read, the words
    still go in."""
    user = settings_screen._user(db, came.telegram_id)
    zone = settings_screen.user_zone(user)
    try:
        journal = "\n".join(journal_lines(db, came.telegram_id, zone))
    except Exception as e:
        logger.warning("Feedback: journal not read: %s", type(e).__name__)
        db.rollback()
        journal = None
    return crud_feedback.create(
        db,
        telegram_id=came.telegram_id,
        kind=kind_of(came.kind),
        original=original,
        text=came.text,
        file_id=came.file_id,
        journal=journal,
    )


def prompt_screen() -> Screen:
    rows = [
        [
            InlineKeyboardButton("👁 Что уйдёт", callback_data=_PREVIEW),
            InlineKeyboardButton("✖️ Отмена", callback_data=CANCEL),
        ]
    ]
    return Screen(PROMPT, InlineKeyboardMarkup(rows))


def preview_screen(db, telegram_id: int) -> Screen:
    user = settings_screen._user(db, telegram_id)
    lines = journal_lines(db, telegram_id, settings_screen.user_zone(user))
    body = quote(lines) if lines else NO_ACTIONS
    text = f"<b>{PREVIEW_TITLE}</b>\n\n{body}"
    back = [[InlineKeyboardButton(f"⬅️ {_title_words()}", callback_data=OPEN)]]
    return Screen(text, InlineKeyboardMarkup(back))


def _title_words() -> str:
    return TITLE.partition(" ")[2]


def sent_screen(feedback_id: int) -> Screen:
    button = InlineKeyboardButton(FOOD_BUTTON, callback_data=f"{_FOOD}{feedback_id}")
    return Screen(SENT, InlineKeyboardMarkup([[button]]))


def food_done_screen() -> Screen:
    return Screen(FOOD_DONE, None)


def _v_open(_db, _by) -> Screen:
    return prompt_screen()


def _v_preview(db, by) -> Screen:
    return preview_screen(db, by)


def _v_cancel(db, by) -> Screen:
    return settings_screen.open_view(db, by, notice=CANCELLED)


def _v_food(db, by, feedback_id: str):
    row = crud_feedback.get_own(db, int(feedback_id), by)
    if row is None:
        logger.warning("feedback: %s by %s: unknown or foreign", feedback_id, by)
        return Screen("", None, toast=NOT_YOURS)
    if row.was_food_at is not None:
        return Screen(FOOD_DONE, None, toast=FOOD_ALREADY)
    return Replay(row.id, row.original, row.sent_at is not None)


_VERBS = (
    (re.compile(r"fb:open"), _v_open),
    (re.compile(r"fb:see"), _v_preview),
    (re.compile(r"fb:x"), _v_cancel),
    (re.compile(r"fb:food:(\d{1,18})"), _v_food),
)


def handle(db, telegram_id: int, data: str):
    """The next screen, or a ``Replay`` for the handler. A stale tap: the settings."""
    for pattern, verb in _VERBS:
        match = pattern.fullmatch(data)
        if match is not None:
            return verb(db, telegram_id, *match.groups())
    logger.warning("feedback: unknown callback %r", data)
    return settings_screen.open_view(db, telegram_id)


def _head(person: invites.Person, at: datetime, zone) -> str:
    who = [TITLE, person.name]
    if person.username:
        who.append(f"@{person.username}")
    who.append(f"{local_time(at, zone):%H:%M}")
    return " · ".join(who)


def _top_lines(row) -> list[str]:
    lines = []
    if row.kind == VOICE:
        lines.append(_VOICE_LINE)
        if not row.transcript:
            lines.append(_NOT_HEARD)
    elif row.kind == PHOTO:
        lines.append(_PHOTO_LINE)
    if row.was_food_at is not None:
        lines.append(_WAS_FOOD)
    return lines


def _cut(words: str, room: int) -> tuple[str, str]:
    """As much of ``words`` as fits ``room`` units, and the rest. A pair never splits."""
    used = 0
    for index, ch in enumerate(words):
        used += units(ch)
        if used > room:
            return words[:index], words[index:]
    return words, ""


def _chunks(words: str, first_room: int) -> list[str]:
    """Every word of it, over as many messages as it takes. The marks «» are added on
    the way out, so each chunk leaves them room."""
    pieces, room = [], first_room
    while words:
        piece, words = _cut(words, room)
        pieces.append(piece)
        room = LIMIT - 2
    return pieces


def _fitting(lines: list[str], room: int) -> list[str]:
    """The newest of the lines that fit: what was done just before matters most."""
    kept: list[str] = []
    used = units(QUOTE_HEAD)
    for one in reversed(lines):
        used += units(one) + 1
        if used > room:
            break
        kept.insert(0, one)
    return kept


def owner_parts(
    row, person: invites.Person, at: datetime, zone, button: bool = True
) -> list[Part]:
    """What each admin reads, in order. The journal gives way first, whole lines from
    the oldest; the person's words are never cut, only spread over more messages."""
    head = _head(person, at, zone)
    top = "\n".join([head, *_top_lines(row)])
    words = "\n".join(w for w in (row.text, row.transcript) if w)
    pieces = _chunks(words, LIMIT - units(top) - 4) if words else []
    plains, htmls = [], []
    for index, piece in enumerate(pieces or [None]):
        said = f"«{piece}»" if piece else None
        if index == 0:
            plain = "\n\n".join(filter(None, [top, said]))
            rest = "\n".join(_top_lines(row))
            marked = f"<b>{html.escape(head, quote=False)}</b>"
            if rest:
                marked += "\n" + html.escape(rest, quote=False)
            if said:
                marked += "\n\n" + html.escape(said, quote=False)
        else:
            plain, marked = said, html.escape(said, quote=False)
        plains.append(plain)
        htmls.append(marked)
    lines = (row.journal or "").split("\n") if row.journal else []
    room = LIMIT - units(plains[-1]) - 3
    fitting = _fitting(lines, room)
    if fitting:
        plains[-1] += "\n\n" + "\n".join([QUOTE_HEAD, *fitting])
        htmls[-1] += "\n\n" + quote(fitting)
    markup = None
    if button:
        card = card_below(person.name, row.telegram_id)
        markup = InlineKeyboardMarkup([[card]])
    parts = [Part(h, p) for h, p in zip(htmls, plains)]
    parts[-1] = Part(htmls[-1], plains[-1], markup)
    return parts


def food_follow_up(person: invites.Person) -> str:
    """To the owner, whose copy is already there. Plain text."""
    return f"↩️ {person.name}: то сообщение оказалось едой, записано в дневник."
