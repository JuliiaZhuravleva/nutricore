"""What the week card says about the food (stage 5, slice E). Pure, no Telegram.

Different plants by name (G5-R09), the days each food group was there, and at most one
finding: coming back after a pause, else one food new to the records.
Only what was there is named, so two records read the same way as seven. Plants
are counted from tagged foods only: a group ticked by hand has no name to count,
and the number is a plain count with no target (the research behind «30» does not
support one).
"""

from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from app.models.meal import Meal
from app.services import food_groups
from app.services.day_card import local_time, short, tags

PLANT_GROUPS = frozenset({"legumes", "veg", "fruit", "grain", "nuts", "omega"})
PAUSE_DAYS = 7
_ROW = 2  # groups on one line of the text
_FINDING_NAME_MAX = 20  # longer, and the words after it no longer fit on the card
_SHORT_WORDS = {"anchor": "опора", "nuts": "орехи"}  # the rest use their own word
NO_TAGS = "🍽 Записи есть, группы в них пока не видны."
BACK_LINE = "✨ Снова с записями — после паузы"


@dataclass(frozen=True)
class WeekFacts:
    plants: int = 0
    groups: tuple = ()  # (key, days), in the order of ``food_groups.GROUPS``
    new_food: Optional[str] = None  # as first written
    back: bool = False  # the records start again after a pause


def _key(name: str) -> str:
    """One food, however it was spelled: «Свёкла » and «свекла» are the same."""
    return " ".join(name.casefold().replace("ё", "е").split())


def _items(meal: Meal) -> list:
    items = tags(meal).get("food_items")
    if not isinstance(items, list):
        return []
    return [
        item
        for item in items
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    ]


def _confirmed(meal: Meal) -> list:
    groups = tags(meal).get("food_groups")
    return groups if isinstance(groups, list) else []


def _plant_names(meal: Meal) -> list:
    """The plant foods the meal still confirms: a group unticked by hand hides them."""
    confirmed = PLANT_GROUPS.intersection(_confirmed(meal))
    return [
        item["name"]
        for item in _items(meal)
        if confirmed.intersection(item.get("groups") or [])
    ]


def _known_names(meals: list) -> set:
    """Every food the records have named, with or without tags."""
    known = set()
    for meal in meals:
        known.update(_key(item["name"]) for item in _items(meal))
        foods = (
            (meal.nutrients or {}).get("foods")
            if isinstance(meal.nutrients, dict)
            else None
        )
        if isinstance(foods, list):
            known.update(_key(food) for food in foods if isinstance(food, str))
    return known


def _came_back(logged: list, earlier: list, zone: ZoneInfo) -> bool:
    moments = [m.meal_time for m in earlier if m.meal_time]
    if not logged or not moments:
        return False
    last = local_time(max(moments), zone).date()
    return (logged[0] - last).days >= PAUSE_DAYS


def facts(days: list, earlier: list, zone: ZoneInfo) -> WeekFacts:
    """``days``: (date, meals), oldest first; ``earlier``: the meals before them."""
    logged = [day for day, meals in days if meals]
    plants, new_food = {}, None
    known = _known_names(earlier)
    group_days = {key: set() for key in food_groups.GROUPS}
    for day, meals in days:
        for meal in meals:
            for key in _confirmed(meal):
                if key in group_days:
                    group_days[key].add(day)
            for name in _plant_names(meal):
                plants.setdefault(_key(name), name)
                if new_food is None and known and _key(name) not in known:
                    new_food = name
    back = _came_back(logged, earlier, zone)
    return WeekFacts(
        plants=len(plants),
        groups=tuple((key, len(found)) for key, found in group_days.items() if found),
        new_food=None if back else new_food,
        back=back,
    )


def plants_label(count: int) -> str:
    """The words under the number on the picture."""
    if count == 1:
        return "растение"
    if count % 100 == 11 or count % 10 != 1:
        tail = count % 100
        few = count % 10 in (2, 3, 4) and not 12 <= tail <= 14
        return "разных растения" if few else "разных растений"
    return "разное растение"


def plants_phrase(count: int) -> str:
    return f"{count} {plants_label(count)}"


def has_tags(week: WeekFacts) -> bool:
    return bool(week.plants or week.groups)


def lines(week: WeekFacts) -> list:
    """The plants and the groups, two to a line; only what was there."""
    out = [f"🌱 {plants_phrase(week.plants)}"] if week.plants else []
    named = [
        f"{food_groups.GROUPS[key][0]} "
        f"{_SHORT_WORDS.get(key, food_groups.GROUPS[key][2])} {count} дн."
        for key, count in week.groups
    ]
    out += [" · ".join(named[i : i + _ROW]) for i in range(0, len(named), _ROW)]
    return out


def finding_line(week: WeekFacts, new_label: str, escape: Callable) -> Optional[str]:
    if week.back:
        return BACK_LINE
    if week.new_food:
        return f"✨ {new_label}: {escape(short(week.new_food))}"
    return None


def finding_block(week: WeekFacts, new_label: str) -> Optional[dict]:
    """The same finding on the picture."""
    if week.back:
        return {"kick": "После паузы", "text": "Снова с записями"}
    if week.new_food:
        name = short(week.new_food)
        text = f"{name[:1].upper()}{name[1:]}"
        if len(name) <= _FINDING_NAME_MAX:
            text += " — впервые в записях"
        return {"kick": new_label, "text": text}
    return None


def period(first: date, last: date, months: tuple) -> str:
    """«24–30 октября»; across a month, «28 сентября – 4 октября»."""
    if first.month == last.month:
        return f"{first.day}–{last.day} {months[last.month - 1]}"
    return (
        f"{first.day} {months[first.month - 1]} – {last.day} {months[last.month - 1]}"
    )
