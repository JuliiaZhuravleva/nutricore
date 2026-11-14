"""When one message wants a plan: dish changes said in words, or several meals.

No model, no rows. ``wants_plan`` only opens the door; the planner reads the message,
and code checks what it says (``plan_service.check``).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from app.services.meal_words import says_several

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
    """«за день: овсянка, суп», «вчера ела: суп, сырники»: a day's words and a list's
    shape. Missing a split keeps one meal, which is safe; parting one meal is not, so
    only list separators count (never «и», never a sentence end): a colon right after
    the day words with two items after it, or three items parted by «,» «;» (and a line
    break, when there are three lines)."""
    if not text or text.rstrip().endswith("?"):
        return False
    plained = plain(text)
    found = _DAY_WORDS.search(plained)
    if found is None:
        return False
    after = plained[found.end() :]
    if after.lstrip(" \t").startswith(":"):
        edges, body, least = r"[,;\n]", after.lstrip(" \t")[1:], 2
    else:
        lines = [line for line in plained.splitlines() if line.strip()]
        edges = r"[,;\n]" if len(lines) >= 3 else r"[,;]"
        body, least = plained, 3
    return len([item for item in re.split(edges, body) if item.strip()]) >= least


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
