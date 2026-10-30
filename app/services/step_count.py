"""The count of days on the chosen step (stage 5, slice K, G5-R26). Pure, no Telegram.

The person against their own past: a plain count of days, with no target and nothing
that resets. It says so plainly when the count fell. Comparison needs records in both
weeks: ``_ENOUGH_DAYS`` is the project's heuristic, no study gives a threshold.

For «earlier», «first food» is not the calendar's: food at 4:00 usually closes the
day before. The middle of the longest stretch of hours with no food stands in for
sleep (``turn``), and the person's day runs from one turn to the next. The hour is
saved when the direction is chosen, so the count does not move with the habit.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import median_low
from typing import Optional
from zoneinfo import ZoneInfo

from app.services import next_step
from app.services.day_card import local_time
from app.services.week_card import _confirmed

UP, SAME, DOWN, FEW = "up", "same", "down", "few"
ASKS = (SAME, DOWN)  # the states that offer a change
_ENOUGH_DAYS = 4
_CLOCK_MIN_DAYS = 3
_EARLIER_MINUTES = 120
_DAY_START, _LATE_FROM = 6, 12  # first food inside these hours is not late
_MIN_AFTER_TURN = 180  # less than this after the turn leaves no room to move earlier


@dataclass(frozen=True)
class Count:
    label: str
    now: int
    before: int
    logged_now: int
    logged_before: int
    month: int


def days_word(count: int) -> str:
    tens, ones = count % 100, count % 10
    if 11 <= tens <= 14:
        return "дней"
    if ones == 1:
        return "день"
    return "дня" if 2 <= ones <= 4 else "дней"


def _moments(meals: list, zone: ZoneInfo) -> list:
    return sorted(local_time(m.meal_time, zone) for m in meals if m.meal_time)


def _turn(moments: list) -> Optional[int]:
    """The hour in the middle of the longest stretch with no food, on a 24-hour ring."""
    hours = {moment.hour for moment in moments}
    if not hours or len(hours) == 24:
        return None
    best_start, best = 0, 0
    for hour in hours:
        length = 0
        while (hour + 1 + length) % 24 not in hours:
            length += 1
        if length > best:
            best_start, best = hour + 1, length
    return (best_start + best // 2) % 24


def _offset(moment: datetime, turn: int) -> int:
    """Minutes since the turn."""
    return ((moment.hour - turn) % 24) * 60 + moment.minute


def _own_day(moment: datetime, turn: int) -> date:
    return (moment - timedelta(hours=turn)).date()


def _first_foods(moments: list, turn: int) -> dict:
    """The person's own day → minutes from the turn to its first food."""
    first: dict = {}
    for moment in moments:
        day, offset = _own_day(moment, turn), _offset(moment, turn)
        first[day] = min(offset, first.get(day, offset))
    return first


def early_clock(meals: list, zone: ZoneInfo) -> Optional[dict]:
    """{"hour", "turn"} when first food comes late in the records: the hour is about
    two earlier than usual. None for too few days, or a first food that is not late."""
    moments = _moments(meals, zone)
    turn = _turn(moments)
    if turn is None:
        return None
    first = _first_foods(moments, turn)
    if len(first) < _CLOCK_MIN_DAYS:
        return None
    usual = median_low(first.values())
    clock = (turn + usual // 60) % 24
    late = (clock - _DAY_START) % 24 >= _LATE_FROM - _DAY_START
    if not late or usual < _MIN_AFTER_TURN:
        return None
    hour = (turn + (usual - _EARLIER_MINUTES) // 60) % 24
    return {"hour": hour, "turn": turn}


def _valid_clock(chosen: dict) -> bool:
    hour, turn = chosen.get("hour"), chosen.get("turn")
    return isinstance(hour, int) and isinstance(turn, int)


def count_days(
    meals: list, chosen: dict, zone: ZoneInfo, first: date, last: date
) -> int:
    """Days in ``first``..``last`` with the chosen thing in the records."""
    moments = _moments(meals, zone)
    if chosen.get("id") == next_step.EARLY:
        if not _valid_clock(chosen):
            return 0
        turn = chosen["turn"]
        limit = (chosen["hour"] - turn) % 24 * 60
        return sum(
            1
            for day, offset in _first_foods(moments, turn).items()
            if first <= day <= last and offset < limit
        )
    wanted = next_step.FOCUSES[chosen["id"]].groups
    found = set()
    for meal in meals:
        if meal.meal_time and wanted.intersection(_confirmed(meal)):
            found.add(local_time(meal.meal_time, zone).date())
    return sum(1 for day in found if first <= day <= last)


def _logged(meals: list, zone: ZoneInfo, first: date, last: date) -> int:
    return len({m.date() for m in _moments(meals, zone) if first <= m.date() <= last})


def count(
    meals: list,
    chosen: dict,
    zone: ZoneInfo,
    first: date,
    last: date,
    month_start: date,
) -> Count:
    """``first``..``last`` against the same days a week earlier."""
    week = timedelta(days=7)
    label = next_step.FOCUSES[chosen["id"]].label
    if chosen["id"] == next_step.EARLY:
        label = f"{label} {chosen.get('hour')}:00"
    return Count(
        label,
        count_days(meals, chosen, zone, first, last),
        count_days(meals, chosen, zone, first - week, last - week),
        _logged(meals, zone, first, last),
        _logged(meals, zone, first - week, last - week),
        count_days(meals, chosen, zone, month_start, last),
    )


def state(found: Count) -> str:
    if min(found.logged_now, found.logged_before) < _ENOUGH_DAYS:
        return FEW
    if found.now == found.before:
        return SAME
    return UP if found.now > found.before else DOWN


def lines(found: Count, month: str) -> list:
    """The count in the card's text; ``month``: «октябре». Empty when there is nothing
    to say."""
    if not found.now and not found.before:
        return []  # nothing moved, and a line of zeros is only a report on the person
    now = f"🎯 {found.label}: {found.now} {days_word(found.now)}"
    kind = state(found)
    if kind == FEW:
        if not found.now:
            return []
        return [f"{now}.", "Для сравнения пока не хватает записей."]
    if kind == SAME:
        return [
            f"{now}, и на прошлой неделе {found.before}.",
            "Можно оставить как есть, сделать проще или выбрать другое.",
        ]
    told = [f"{now}, на прошлой неделе {found.before}."]
    if kind == DOWN:
        return [*told, "Можно оставить как есть или сделать шаг меньше."]
    # Early in a month, its count can be under the week's: it would read as a fall.
    if found.month >= found.now:
        told.append(f"В {month} таких дней {found.month}.")
    return told
