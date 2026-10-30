"""What the day card says about the food (stage 5, slice D). Pure, no Telegram.

When food happened as plain times (G5-R06) and the two groups the day card names
(G5-R07): whole grains and protein anchors, each only if it was there. Nothing
here can name what was missing, and a long gap gets words, never hours.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from app.models.meal import Meal
from app.services import food_groups

GAP_HOURS = 6
MAX_NAMES = 4
NAME_MAX = 32


def local_time(moment: datetime, zone: ZoneInfo) -> datetime:
    # SQLite hands back naive datetimes; the column is written in UTC.
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(zone)


@dataclass(frozen=True)
class DayFacts:
    times: tuple = ()  # «8:40», in order
    gap: Optional[str] = None  # «днём», where the one long gap fell
    grain: Optional[str] = None  # «овсянка утром»
    anchor: Optional[str] = None  # «тофу в обед · чечевица вечером»


def plain_time(moment: datetime) -> str:
    return f"{moment.hour}:{moment.minute:02d}"


def _part_of_day(hour: float) -> str:
    if hour < 5:
        return "ночью"
    if hour < 11:
        return "утром"
    if hour < 15:
        return "в обед"
    if hour < 18:
        return "днём"
    return "вечером"


def _gap_word(hour: float) -> str:
    # Not «в обед»: a gap that long is wider than a lunch.
    return "днём" if 11 <= hour < 18 else _part_of_day(hour)


def _hour(moment: datetime) -> float:
    return moment.hour + moment.minute / 60


def _long_gap(moments: list) -> Optional[str]:
    """The longest gap between two meals, if it is long; the night is not a gap,
    since only meals of one day are ever passed in."""
    pairs = list(zip(moments, moments[1:]))
    if not pairs:
        return None
    first, last = max(pairs, key=lambda pair: pair[1] - pair[0])
    if (last - first).total_seconds() < GAP_HOURS * 3600:
        return None
    return _gap_word((_hour(first) + _hour(last)) / 2)


def _tags(meal: Meal) -> dict:
    for source in (meal.ai_analysis, meal.nutrients):
        if isinstance(source, dict) and "food_groups" in source:
            return source
    return {}


def _foods_in(meal: Meal, group: str, name_of: Callable) -> list:
    """The foods behind a group the meal confirmed. A group ticked by hand has no
    item behind it, so the meal's own name stands in; one unticked by hand hides
    the items the analysis had tagged."""
    tags = _tags(meal)
    confirmed = tags.get("food_groups")
    if not isinstance(confirmed, list) or group not in confirmed:
        return []
    items = tags.get("food_items")
    names = [
        item["name"]
        for item in (items if isinstance(items, list) else [])
        if isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and group in (item.get("groups") or [])
    ]
    return names or [name_of(meal)]


def _short(name: str) -> str:
    name = name.strip()
    return name if len(name) <= NAME_MAX else name[: NAME_MAX - 1].rstrip() + "…"


def _group_line(meals: list, group: str, zone: ZoneInfo, name_of: Callable):
    """«тофу, рис в обед · чечевица вечером»; a food eaten twice in one part of the
    day is named once, and past ``MAX_NAMES`` the rest are a count."""
    seen, parts, total = set(), [], 0
    for meal in meals:
        part = (
            _part_of_day(_hour(local_time(meal.meal_time, zone)))
            if meal.meal_time
            else ""
        )
        for name in _foods_in(meal, group, name_of):
            if (part, name.casefold()) in seen:
                continue
            seen.add((part, name.casefold()))
            total += 1
            if total > MAX_NAMES:
                continue
            if parts and parts[-1][0] == part:
                parts[-1][1].append(_short(name))
            else:
                parts.append((part, [_short(name)]))
    if not parts:
        return None
    line = " · ".join(f"{', '.join(names)} {part}".strip() for part, names in parts)
    return line + (f" · ещё {total - MAX_NAMES}" if total > MAX_NAMES else "")


def facts(meals: list, zone: ZoneInfo, name_of: Callable) -> DayFacts:
    """``meals`` in time order, as the CRUD returns them."""
    moments = [local_time(m.meal_time, zone) for m in meals if m.meal_time]
    return DayFacts(
        times=tuple(plain_time(moment) for moment in moments),
        gap=_long_gap(moments),
        grain=_group_line(meals, "grain", zone, name_of),
        anchor=_group_line(meals, "anchor", zone, name_of),
    )


def lines(day: DayFacts, escape: Callable) -> list:
    """The card's text; every name passes ``escape``."""
    out = []
    if day.times:
        rhythm = "🕐 Еда: " + " · ".join(day.times)
        out.append(rhythm + (f", {day.gap} длинный перерыв" if day.gap else ""))
    if day.grain:
        out.append(
            f"{food_groups.GROUPS['grain'][0]} Цельные злаки: {escape(day.grain)}"
        )
    if day.anchor:
        out.append(
            f"{food_groups.GROUPS['anchor'][0]} Белковая опора: {escape(day.anchor)}"
        )
    return out
