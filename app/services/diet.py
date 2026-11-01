"""What a person usually eats: an answer that changes only the hints.

Pure, no Telegram and no database: functions take ``User.settings``, like
``next_step``. Any food can be recorded whatever the answer. No answer means what the
bot did before this question existed: a plant food can be eaten by anyone, an animal
one must not be offered to a person who did not ask for it.

``settings_screen`` draws the picker and saves the answer, and first-run screens use
the same two functions there.
"""

from typing import Optional

KEY = "diet"  # in ``User.settings``; absent means no answer
ALL, NO_MEAT, PLANTS = "all", "no_meat", "plants"
CHOICES = {ALL: "🍖 Всё", NO_MEAT: "🥚 Без мяса", PLANTS: "🌱 Растения"}
TITLE = "🍽 Обычная еда"
QUESTION = (
    "Что обычно ешь? От этого зависят только подсказки, записывать можно любую еду."
)


def chosen(settings: dict) -> Optional[str]:
    """The answer, or None: settings are open to other writers, so a damaged value
    reads as no answer rather than costing the person a screen."""
    value = settings.get(KEY)
    return value if isinstance(value, str) and value in CHOICES else None


def with_choice(settings: dict, value: str) -> dict:
    """A new dict with the answer in it; an unknown answer changes nothing."""
    return {**settings, KEY: value} if value in CHOICES else settings


def shows_nutrients(settings: dict) -> bool:
    """The reference on vitamins is nearly all about vegans: of no use with «🍖 Всё»."""
    return chosen(settings) != ALL
