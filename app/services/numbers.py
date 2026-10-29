"""«🔢 Цифры»: whether calories and macros show without a tap.

Off by default (stage 5, G5-R01/R02): 📊 Статистика, the meal draft and
⭐ Избранное show what was eaten, and the numbers wait behind a «🔢 Цифры»
button. The switch in ⚙️ Настройки shows them everywhere again. One setting
for every screen, kept in ``User.settings`` like the others. Its own module so
``favorites`` can read it without importing ``settings_screen`` (which imports
``favorites``).
"""

from __future__ import annotations

from app.models.user import User

SHOW_NUMBERS = "show_numbers"
LABEL = "🔢 Цифры"


def shows_numbers(user: User | None) -> bool:
    return bool(user and (user.settings or {}).get(SHOW_NUMBERS))


def shows_numbers_for(db, telegram_id: int) -> bool:
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    return shows_numbers(user)


def toggle_label(shown: bool) -> str:
    """The button under a screen: «✓ 🔢 Цифры» while they are open."""
    return ("✓ " if shown else "") + LABEL
