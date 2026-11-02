"""The first run: what the bot is, who sees what, one question, then the first record.

    the first /start → <b>👋 Дневник питания</b>: the three answers, [Позже]
    an answer → <b>✍️ Первая запись</b> in a new message, the only kind that can bring the
      main keyboard
    the first record saved → one line about /feedback, once

For anyone with no mark and no records, so neither the owner nor the people already in the
bot ever see it. The mark lives in ``User.settings``: absent, then ``asked`` while the
question waits, ``answered``, and ``done`` once the hint went out.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.config import settings
from app.models.body_metric import BodyMetric
from app.models.meal import Meal
from app.models.user import User
from app.services import diet, settings_screen
from app.services.favorites import Screen
from app.services.settings_screen import diet_row, save_diet

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "wl:"
KEY = "onboarding"
ASKED, ANSWERED, DONE = "asked", "answered", "done"
_EATS = CALLBACK_PREFIX + "eat:"  # + the answer
_LATER = CALLBACK_PREFIX + "later"

HINT = (
    "Записано. Вопросы и пожелания — /feedback, объяснения — "
    "ℹ️ Как это работает в ⚙️ Настройках."
)
FIRST_RECORD = (
    "<b>✍️ Первая запись</b>\n\n"
    "Пришли, что съедено сейчас или недавно: фото, текст или голосовое. Например, "
    "«омлет и кофе». Ошибку бота можно будет поправить под записью."
)
_WELCOME = (
    "<b>👋 Дневник питания</b>\n\n"
    "Записывает, что ты ешь, по фото, тексту или голосу. Без оценок и запретов, готовая "
    "еда считается наравне с любой.\n\n"
    "Владелец бота видит, как часто ты пользуешься ботом и сколько стоят запросы. Записи "
    "хранятся на сервере владельца, другим людям они не видны.\n\n"
    f"{diet.QUESTION}"
)


def _user(db, telegram_id: int) -> User | None:
    return db.scalar(select(User).where(User.telegram_id == telegram_id))


def _mark(user: User | None) -> str | None:
    return (user.settings or {}).get(KEY) if user else None


def _has_records(db, user: User) -> bool:
    """Any row at all, deleted ones too: a person who has used the bot knows it."""
    return any(
        db.scalar(select(model.id).where(model.user_id == user.id).limit(1)) is not None
        for model in (Meal, BodyMetric)
    )


def opens_welcome(db, telegram_id: int) -> bool:
    """Whether /start owes this person the welcome. Marks it as shown when it does, so
    the next /start opens the menu, and the question stays answerable on its message."""
    if telegram_id in settings.admin_ids:
        return False
    user = _user(db, telegram_id)
    if _mark(user) is not None:
        return False
    if user is not None and _has_records(db, user):
        return False
    settings_screen._save(db, telegram_id, **{KEY: ASKED})
    return True


def welcome() -> Screen:
    rows = [
        diet_row(_EATS, None),
        [InlineKeyboardButton("Позже", callback_data=_LATER)],
    ]
    return Screen(_WELCOME, InlineKeyboardMarkup(rows))


def handle(db, telegram_id: int, data: str) -> Screen:
    """An answer. A screen with no text is a tap that changed nothing the person can see:
    the first-record screen comes once, and a stale button only saves the answer."""
    if data.startswith(_EATS):
        if not save_diet(db, telegram_id, data[len(_EATS) :]):
            logger.warning("welcome: unknown answer %r", data)
            return Screen("", None)
    elif data != _LATER:
        logger.warning("welcome: unknown callback %r", data)
        return Screen("", None)
    if _mark(_user(db, telegram_id)) in (ANSWERED, DONE):
        return Screen("", None, toast="✓")
    settings_screen._save(db, telegram_id, **{KEY: ANSWERED})
    return Screen(FIRST_RECORD, None)


def take_hint(db, telegram_id: int) -> str | None:
    """The hint, once, to someone who came through the welcome. Marked before it is sent:
    a lost hint is smaller than a repeated one."""
    user = _user(db, telegram_id)
    if _mark(user) not in (ASKED, ANSWERED):
        return None
    settings_screen._save(db, telegram_id, **{KEY: DONE})
    return HINT
