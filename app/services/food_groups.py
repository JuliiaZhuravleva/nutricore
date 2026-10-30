"""The eight food groups a meal is tagged with (stage 5, slice B, G5-R07).

Pure — no Telegram I/O, no DB. The analysis tags each food item
(``food_items``: ``[{"name": "тофу", "groups": ["legumes", "anchor"]}]``); the
meal's groups are their union until the user edits them in the draft.

A saved meal carries the result in ``ai_analysis`` (and in ``nutrients``, which
a favorite copies): ``food_items``, what the analysis said, and ``food_groups``,
what the user confirmed. The cards read ``food_groups``. A group that is absent
is never named: tags say what was there, not what was missing.
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# key → (mark, button label, the word in the draft's line)
GROUPS = {
    "legumes": ("🫘", "Бобовые", "бобовые"),
    "anchor": ("⚓", "Опора", "белковая опора"),
    "veg": ("🥦", "Овощи", "овощи"),
    "fruit": ("🍎", "Фрукты", "фрукты"),
    "grain": ("🌾", "Злаки", "цельные злаки"),
    "nuts": ("🥜", "Орехи", "орехи и семечки"),
    "calcium": ("🥛", "Кальций", "кальций"),
    "omega": ("🌰", "Омега-3", "омега-3"),
}


def ordered(keys) -> list:
    """Known group keys, each once, in the order every screen shows them."""
    wanted = set(keys)
    return [key for key in GROUPS if key in wanted]


def clean_items(raw: Any) -> list:
    """The model's ``food_items`` → valid items only; never raises.

    Tags are an addition to the analysis: a malformed tag loses that tag, not
    the meal. Anything dropped is logged, so a model that stops following the
    format shows up as a warning and not as meals that merely look untagged.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        logger.warning("food_items: expected a list, got %s", type(raw).__name__)
        return []
    items = []
    for entry in raw:
        name = entry.get("name") if isinstance(entry, dict) else None
        groups = entry.get("groups") if isinstance(entry, dict) else None
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(groups, list)
        ):
            logger.warning("food_items: dropped a malformed item: %r", entry)
            continue
        known = [g for g in groups if isinstance(g, str) and g in GROUPS]
        if len(known) != len(groups):
            logger.warning("food_items: dropped unknown groups in %r", groups)
        items.append({"name": name.strip(), "groups": ordered(known)})
    return items


def from_items(items: Optional[list]) -> list:
    """The meal's groups: the union of its items' groups."""
    return ordered(g for item in items or [] for g in item.get("groups") or [])


def line(groups: Optional[list]) -> Optional[str]:
    """The draft's «🏷 Группы: …» line; None without a group (never «нет овощей»)."""
    if not groups:
        return None
    words = " · ".join(f"{GROUPS[g][0]} {GROUPS[g][2]}" for g in ordered(groups))
    return f"🏷 Группы: {words}"


def stamp(analysis: dict, groups: list, *, by_user: bool) -> dict:
    """A copy of a stored analysis with the meal's confirmed groups on it."""
    return {
        **analysis,
        "food_groups": ordered(groups),
        "food_groups_by_user": by_user,
    }


def carried(nutrients: Optional[dict]) -> Optional[dict]:
    """What a meal logged from a favorite keeps; None if the favorite is untagged."""
    if not nutrients or "food_groups" not in nutrients:
        return None
    return {
        "food_items": nutrients.get("food_items") or [],
        "food_groups": ordered(nutrients["food_groups"] or []),
        "food_groups_by_user": bool(nutrients.get("food_groups_by_user")),
    }
