"""The person's last actions, for the owner's screens and a feedback message.

Times, kinds and fixed names of buttons, never what about. The closed lists are in
``app.models.action_event``, and ``crud_action_event.add`` enforces them for any writer;
here is only what is safe to take from an update in the first place. Plain text and a
caption store the kind alone. Nothing the bot said is stored.

A write that fails says so in the log and leaves the person's flow alone.
"""

import logging
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes

from app.crud.crud_action_event import crud_action_event
from app.db.session import SessionLocal
from app.models.action_event import (
    BUTTON,
    BUTTON_VERBS,
    COMMAND,
    COMMANDS,
    CORRECTED,
    FAILED,
    MENU_LABELS,
    PHOTO,
    SAVED,
    TEXT,
    VOICE,
)
from app.services.access_control import is_user_allowed
from app.services.attribution import begin_update
from app.services.day_card import local_time

logger = logging.getLogger(__name__)

# The old spelling a keyboard already on someone's screen still sends.
_OLD_MENU_LABELS = {"🍽 Добавить прием пищи": "🍽 Добавить приём пищи"}

# Translated when shown, so a name can change without touching a row.
BUTTON_NAMES = {
    "dh": "🍲 Блюда",
    "fav": "⭐ Избранное",
    "fl": "🧺 Списки еды",
    "hw": "ℹ️ Как это работает",
    "meal": "🍽 Запись",
    "ml": "🍽 Запись",
    "rt": "💊 Рутины",
    "set": "⚙️ Настройки",
    "st": "📊 Статистика",
    "us": "👥 Пользователи",
    "wl": "👋 Первый запуск",
    "wt": "⚖️ Вес",
}
CORRECTION_NAMES = {
    "add": "блюдо добавлено",
    "grams": "вес изменён",
    "groups": "группы изменены",
    "join": "записи объединены",
    "numbers": "цифры изменены",
    "portion": "порция блюда изменена",
    "qty": "порция изменена",
    "remove": "блюдо убрано",
    "rename": "название изменено",
    "replace": "блюдо заменено",
    "split": "запись разделена",
    "time": "время изменено",
}
_READ_ICONS = {"image": "📷", "text": "✍️", "voice": "🎙", "dish": "🍲"}


def classify(update: Update) -> Optional[tuple]:
    """(kind, label) of what the person did; None for what is no action here (an edit,
    a sticker). The label is only ever something from a closed list."""
    query = update.callback_query
    if query is not None:
        verb = (query.data or "").partition(":")[0]
        return BUTTON, verb if verb in BUTTON_VERBS else None
    message = update.message
    if message is None:
        return None
    if message.photo:
        return PHOTO, None
    if message.document and (message.document.mime_type or "").startswith("image/"):
        return PHOTO, None
    if message.voice:
        return VOICE, None
    words = message.text
    if words is None:
        return None
    words = _OLD_MENU_LABELS.get(words, words)
    if words in MENU_LABELS:
        return BUTTON, words
    if words.startswith("/") and len(words) > 1:
        head = words[1:].split(maxsplit=1)
        name = head[0].partition("@")[0].lower() if head else ""
        # «/омлет» is no command this bot has: it is words, and they are not kept.
        if name in COMMANDS:
            return COMMAND, name
    return TEXT, None


def note(telegram_id: int, kind: str, label: Optional[str] = None) -> None:
    """One row. Never raises."""
    try:
        with SessionLocal() as db:
            crud_action_event.add(db, telegram_id=telegram_id, kind=kind, label=label)
            db.commit()
    except Exception as e:
        logger.warning("Action journal: a %s not written: %s", kind, type(e).__name__)


async def note_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """The first handler of every update: whose it is, then what they did. Someone the
    gate is about to drop gets no row: strangers leave nothing here."""
    user = update.effective_user
    telegram_id = user.id if user else None
    begin_update(telegram_id)
    try:
        if telegram_id is None or not is_user_allowed(telegram_id):
            return
        action = classify(update)
    except Exception as e:
        logger.warning("Action journal: an update not read: %s", type(e).__name__)
        return
    if action is not None:
        note(telegram_id, *action)


def describe(kind: str, label: Optional[str]) -> str:
    """What a row says on a screen."""
    if kind == PHOTO:
        return "📷 фото"
    if kind == TEXT:
        return "✍️ текст"
    if kind == VOICE:
        return "🎙 голосовое"
    if kind == BUTTON:
        return f"👆 {BUTTON_NAMES.get(label, label or 'кнопка')}"
    if kind == COMMAND:
        return f"/{label}" if label else "команда"
    if kind == SAVED:
        return f"{_READ_ICONS.get(label, '🍽')} → записано"
    if kind == CORRECTED:
        return f"✏️ {CORRECTION_NAMES.get(label, 'запись изменена')}"
    if kind == FAILED:
        return f"{_READ_ICONS.get(label, '🍽')} → ⚠️ чтение не удалось"
    return kind


def line(event, zone) -> str:
    """«14:03 ✏️ вес изменён»."""
    return f"{local_time(event.at, zone):%H:%M} {describe(event.kind, event.label)}"
