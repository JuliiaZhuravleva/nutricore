"""When one message wants a plan: dish changes said in words, or several meals.

No model, no rows. ``wants_plan`` only opens the door; the planner reads the message,
and code checks what it says (``plan_service.check``).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from app.services.meal_words import clauses, says_several

DISH, MEALS = "dish", "meals"

# On the plain text (casefold, ё → е). «одно и то же» may be food's («ел одно и то же
# весь день»): the planner tells, and says ``meal`` then.
DISH_VERBS = re.compile(
    r"(?<!\w)(?:"
    r"объедин(?:и|ите|ить|яй)"
    r"|одно\s+блюдо|одно\s+и\s+то\s+же"
    r"|в\s+избранное|из\s+избранного"
    r"|переименуй(?:те)?|переименовать"
    r"|(?:убери|удали|убрать|удалить)(?:те)?\s+(?:\S+\s+){0,4}?"
    r"из\s+(?:моих\s+|списка\s+)?блюд"
    r")(?!\w)"
)


def plain(text: str) -> str:
    return text.casefold().replace("ё", "е")


def has_dish_verb(text: Optional[str]) -> bool:
    return bool(text) and DISH_VERBS.search(plain(text)) is not None


_FOOD_SAME = re.compile(r"(?<!\w)одно\s+и\s+то\s+же(?!\w)")


def asks_dish_change(text: Optional[str]) -> bool:
    """A dish verb that cannot be about food: «одно и то же» alone may be («ел одно и
    то же весь день»)."""
    return bool(text) and has_dish_verb(_FOOD_SAME.sub(" ", plain(text)))


# D1: a message that lists a whole day. On the plain text.
_DAY_WORDS = re.compile(
    r"(?<!\w)(?:за\s+(?:весь\s+)?день|за\s+сегодня|(?:сегодня|вчера)\s+(?:ел|ела|ели))"
    r"(?!\w)"
)


def says_day(text: Optional[str]) -> bool:
    """«за день: овсянка, суп», «вчера ела: суп, сырники»: a day's words and two or
    more clauses (a newline parts them too). One clause is one meal."""
    if not text or text.rstrip().endswith("?") or not _DAY_WORDS.search(plain(text)):
        return False
    pieces = [c for line in text.splitlines() for c in clauses(line) if c.strip()]
    return len(pieces) >= 2


def wants_plan(text: Optional[str], sent: datetime, zone) -> Optional[str]:
    """``DISH`` when the words change dishes, ``MEALS`` when they are several meals by
    R0's own rule, else None. A question is asked, not done."""
    if not text or text.rstrip().endswith("?"):
        return None
    if has_dish_verb(text):
        return DISH
    if says_several(text, sent, zone):
        return MEALS
    if says_day(text):
        return MEALS
    return None
