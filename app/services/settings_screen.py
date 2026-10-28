"""⚙️ Настройки: what the bot runs with, and the switches the owner can flip.

    ⚙️ Настройки → <b>⚙️ Настройки</b> — time zone, goal, the cost line
    [💸 Стоимость запросов: выкл]
    tap → <i>✓ Стоимость запросов включена</i> on top, the same screen below

A switch flips at once and says so on the next screen (no «вы уверены?»).
The flags live in ``User.settings`` (JSON), so a new one needs no migration.
Telegram-free apart from the keyboard, like ``stats_screen``.
"""

from __future__ import annotations

import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.config import settings
from app.models.user import User
from app.services.favorites import Screen

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "set:"
SHOW_COSTS = "show_costs"  # the 💸 line under each analysis
_COSTS_CB = CALLBACK_PREFIX + "costs"


def _user(db, telegram_id: int) -> User | None:
    return db.query(User).filter(User.telegram_id == telegram_id).first()


def shows_costs(db, telegram_id: int) -> bool:
    user = _user(db, telegram_id)
    return bool(user and (user.settings or {}).get(SHOW_COSTS))


def screen(on: bool, notice: str | None = None) -> Screen:
    lines = [f"<i>{notice}</i>", ""] if notice else []
    lines += [
        "<b>⚙️ Настройки</b>",
        "",
        f"🕐 Часовой пояс: {html.escape(settings.DEFAULT_TIMEZONE)} — по нему "
        "📊 Статистика делит дни.",
        "🎯 Цель по калориям: не задана.",
        f"💸 Стоимость запросов: {'показываю' if on else 'не показываю'} "
        "под каждым разбором еды.",
    ]
    button = InlineKeyboardButton(
        f"💸 Стоимость запросов: {'вкл' if on else 'выкл'}", callback_data=_COSTS_CB
    )
    return Screen("\n".join(lines), InlineKeyboardMarkup([[button]]))


def open_view(db, telegram_id: int) -> Screen:
    return screen(shows_costs(db, telegram_id))


def handle_callback(db, telegram_id: int, data: str) -> Screen:
    if data != _COSTS_CB:
        logger.warning("settings: unknown callback data %r", data)
        return open_view(db, telegram_id)
    user = _user(db, telegram_id)
    if user is None:
        user = User(telegram_id=telegram_id)
        db.add(user)
    on = not (user.settings or {}).get(SHOW_COSTS)
    # A new dict: SQLAlchemy does not see a JSON value changed in place.
    user.settings = {**(user.settings or {}), SHOW_COSTS: on}
    db.commit()
    notice = "✓ Стоимость запросов включена" if on else "✓ Стоимость запросов скрыта"
    return screen(on, notice)
