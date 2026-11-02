"""The first run: what the bot is, who sees what, one question, then the first record.

    the first /start → <b>👋 Дневник питания</b>: the three answers, [Позже]
    an answer → <b>✍️ Первая запись</b> in a new message, the only kind that can bring the
      main keyboard
    the first record saved → one line about /feedback, once

For anyone with no mark and no records, so neither the owner nor the people already in the
bot ever see it. An invited person whose first word was food gets it with that message, and
only records from before the invitation mean they were here already. The mark lives in
``User.settings``: absent, then ``asked`` while the question waits, ``answered`` once the
main keyboard is there, and ``done`` once the hint went out.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.config import settings
from app.crud.crud_access_grant import crud_access_grant
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

MENU = "Меню внизу: 📊 Статистика, ⚙️ Настройки, 🍽 Добавить приём пищи."
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


def _aware(moment: datetime) -> datetime:
    """SQLite reads a stored moment back without its zone; stored is always UTC."""
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _was_here(db, user: User, telegram_id: int) -> bool:
    """Any row at all, deleted ones too: a person who has used the bot knows it. For
    someone invited, a row from before the invitation: what they wrote since their
    invitation is no reason to skip what the bot owes them."""
    grant = crud_access_grant.get(db, telegram_id)
    for model in (Meal, BodyMetric):
        first = db.scalar(
            select(model.created_at)
            .where(model.user_id == user.id)
            .order_by(model.created_at)
            .limit(1)
        )
        if first is None:
            continue
        if grant is None or _aware(first) < _aware(grant.created_at):
            return True
    return False


def opens_welcome(db, telegram_id: int) -> bool:
    """Whether /start owes this person the welcome. Marks it as shown when it does, so
    the next /start opens the menu, and the question stays answerable on its message."""
    if telegram_id in settings.admin_ids:
        return False
    user = _user(db, telegram_id)
    if _mark(user) is not None:
        return False
    if user is not None and _was_here(db, user, telegram_id):
        return False
    settings_screen._save(db, telegram_id, **{KEY: ASKED})
    return True


def owed_unasked(db, telegram_id: int) -> bool:
    """The same debt, for someone invited who wrote before pressing /start. Only them:
    anyone else's first message is theirs to make as before."""
    return crud_access_grant.is_active(db, telegram_id) and opens_welcome(
        db, telegram_id
    )


def still_owed(db, telegram_id: int) -> None:
    """Telegram did not take it: the next message or /start owes it again."""
    settings_screen._save(db, telegram_id, **{KEY: None})


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


def take_menu(db, telegram_id: int) -> str | None:
    """The line that brings the main keyboard, once, to someone who skipped the question
    and whose first message recorded nothing. ``answered`` from here: the hint still comes
    with the first record, and a late answer is saved with no second screen."""
    if _mark(_user(db, telegram_id)) != ASKED:
        return None
    settings_screen._save(db, telegram_id, **{KEY: ANSWERED})
    return MENU


def take_hint(db, telegram_id: int) -> str | None:
    """The hint, once, to someone who came through the welcome. Marked before it is sent:
    a lost hint is smaller than a repeated one."""
    user = _user(db, telegram_id)
    if _mark(user) not in (ASKED, ANSWERED):
        return None
    settings_screen._save(db, telegram_id, **{KEY: DONE})
    return HINT
