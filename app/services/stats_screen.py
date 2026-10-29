"""📊 Статистика: what was eaten today, yesterday and over the last 7 days.

    📊 Статистика → <b>📊 Сегодня, вт 28 окт</b> — the meals, then the totals
    [✓ Сегодня] [Вчера] [7 дней]
    [🔢 Цифры]

The numbers are the saved meals' own, summed, and there is no goal (stage 5,
G5-R03). Unless ⚙️ shows them, they wait behind «🔢 Цифры» (G5-R01): the screen
lists what was eaten and when. Days are the user's calendar days (their ⚙️
time zone), not UTC. Every tab
edits the same message; the callback data carries the whole view, so a tap
works after a restart too. Telegram-free apart from the keyboard, like
``favorites``.
"""

from __future__ import annotations

import html
import logging
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.crud.crud_meal import crud_meal
from app.models.meal import Meal
from app.models.user import User
from app.services.favorites import Screen, favorite_name
from app.services.meal_draft import num
from app.services.numbers import shows_numbers, toggle_label
from app.services.settings_screen import user_zone

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "st:"
WEEK_DAYS = 7
NAME_MAX = 32
BAR_WIDTH = 8

TODAY, YESTERDAY, WEEK = "t", "y", "w"
OPEN = "n"  # appended to a tab: the numbers are open on it
_TABS = ((TODAY, "Сегодня"), (YESTERDAY, "Вчера"), (WEEK, "7 дней"))

_WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
_MONTHS = (
    "янв", "фев", "мар", "апр", "мая", "июн",
    "июл", "авг", "сен", "окт", "ноя", "дек",
)  # fmt: skip


def local_time(moment: datetime, zone: ZoneInfo) -> datetime:
    # SQLite hands back naive datetimes; the column is written in UTC.
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(zone)


def _day_bounds(day: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    """The local calendar day as a UTC [start, end) range."""
    start = datetime.combine(day, time(), tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time(), tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC)


def day_title(day: date) -> str:
    return f"{_WEEKDAYS[day.weekday()]} {day.day} {_MONTHS[day.month - 1]}"


def _sum(meals: list[Meal], field: str) -> float:
    total = 0.0
    for meal in meals:
        try:
            total += float(getattr(meal, field) or 0)
        except (TypeError, ValueError):
            pass
    return total


def _kcal(value: float) -> str:
    return f"{round(value):,}".replace(",", " ") + " ккал"


def _macros(meals: list[Meal], divide_by: int = 1) -> str:
    return " · ".join(
        f"{label} {num(_sum(meals, field) / divide_by, 0)} г"
        for label, field in (("Б", "proteins"), ("Ж", "fats"), ("У", "carbohydrates"))
    )


def _meal_name(meal: Meal) -> str:
    foods = (
        (meal.nutrients or {}).get("foods")
        if isinstance(meal.nutrients, dict)
        else None
    )
    name = favorite_name(caption=None, description=meal.description, foods=foods)
    if len(name) > NAME_MAX:
        name = name[: NAME_MAX - 1].rstrip() + "…"
    return name


def _tabs(current: str, opened: bool | None = None) -> InlineKeyboardMarkup:
    """The tabs; ``opened`` is None when ⚙️ shows the numbers anyway (no button),
    else whether «🔢 Цифры» is open on this view. An open view stays open
    across the tabs."""
    keep = OPEN if opened else ""
    rows = [
        [
            InlineKeyboardButton(
                ("✓ " if code == current else "") + label,
                callback_data=CALLBACK_PREFIX + code + keep,
            )
            for code, label in _TABS
        ]
    ]
    if opened is not None:
        flip = "" if opened else OPEN
        rows.append(
            [
                InlineKeyboardButton(
                    toggle_label(opened), callback_data=CALLBACK_PREFIX + current + flip
                )
            ]
        )
    return InlineKeyboardMarkup(rows)


def _records(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        return f"{count} запись"
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return f"{count} записи"
    return f"{count} записей"


# --- screens ----------------------------------------------------------------


def day_screen(
    meals: list[Meal],
    day: date,
    tab: str,
    zone: ZoneInfo,
    opened: bool | None = None,
) -> Screen:
    """``opened``: see ``_tabs``; the numbers show unless it is False."""
    numbers = opened is not False
    head = "Сегодня" if tab == TODAY else "Вчера"
    lines = [f"<b>📊 {head}, {day_title(day)}</b>", ""]
    if not meals:
        lines.append(
            "Записей нет. Еда попадает сюда после «✅ Сохранить» "
            "или из ⭐ Избранного."
        )
        return Screen("\n".join(lines), _tabs(tab, opened))
    for meal in meals:
        when = (
            local_time(meal.meal_time, zone).strftime("%H:%M")
            if meal.meal_time
            else "—"
        )
        label = f" · {meal.meal_type}" if meal.meal_type else ""
        kcal = f" · {_kcal(_sum([meal], 'calories'))}" if numbers else ""
        lines.append(f"{when}{label} · {html.escape(_meal_name(meal))}{kcal}")
    if numbers:
        lines += [
            "",
            f"<b>🔥 Итого: {_kcal(_sum(meals, 'calories'))}</b>",
            _macros(meals),
        ]
    return Screen("\n".join(lines), _tabs(tab, opened))


def week_screen(
    days: list[tuple[date, list[Meal]]], opened: bool | None = None
) -> Screen:
    """``days`` oldest first, today last. Without the numbers it lists only the
    days that have records: an empty day is not shown as a gap."""
    first, last = days[0][0], days[-1][0]
    lines = [
        f"<b>📊 {WEEK_DAYS} дней</b> · {first.day} {_MONTHS[first.month - 1]} – "
        f"{last.day} {_MONTHS[last.month - 1]}",
        "",
    ]
    if opened is False:
        logged_days = [(day, meals) for day, meals in days if meals]
        for day, meals in logged_days:
            title = f"{_WEEKDAYS[day.weekday()]} {day.day:02d}.{day.month:02d}"
            lines.append(f"{title} · {_records(len(meals))}")
        if not logged_days:
            lines.append("За неделю записей нет.")
        return Screen("\n".join(lines), _tabs(WEEK, opened))
    totals = [_sum(meals, "calories") for _day, meals in days]
    top = max(totals) or 1.0
    for (day, meals), total in zip(days, totals):
        title = f"{_WEEKDAYS[day.weekday()]} {day.day:02d}.{day.month:02d}"
        if not meals:
            lines.append(f"{title}  {'▱' * BAR_WIDTH}  —")
            continue
        filled = max(1, round(BAR_WIDTH * total / top))
        bar = "▰" * filled + "▱" * (BAR_WIDTH - filled)
        lines.append(f"{title}  {bar}  {_kcal(total)}")
    logged = [meals for _day, meals in days if meals]
    lines.append("")
    if not logged:
        lines.append("За неделю записей нет.")
    else:
        everything = [meal for meals in logged for meal in meals]
        lines += [
            f"<b>🔥 В среднем: {_kcal(_sum(everything, 'calories') / len(logged))}"
            "</b> в день",
            _macros(everything, len(logged)),
            f"<i>Среднее по {len(logged)} дн. с записями из {WEEK_DAYS}.</i>",
        ]
    return Screen("\n".join(lines), _tabs(WEEK, opened))


# --- operations -------------------------------------------------------------


def _meals_on(db, user: User | None, day: date, zone: ZoneInfo) -> list[Meal]:
    if user is None:
        return []
    start, end = _day_bounds(day, zone)
    return crud_meal.get_user_meals_between(db, user.id, start, end)


def open_view(
    db,
    telegram_id: int,
    tab: str = TODAY,
    now: datetime | None = None,
    opened: bool = False,
):
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    zone = user_zone(user)
    today = local_time(now or datetime.now(UTC), zone).date()
    shown = None if shows_numbers(user) else opened
    if tab == YESTERDAY:
        day = today - timedelta(days=1)
        return day_screen(_meals_on(db, user, day, zone), day, YESTERDAY, zone, shown)
    if tab == WEEK:
        days = [today - timedelta(days=back) for back in range(WEEK_DAYS - 1, -1, -1)]
        return week_screen(
            [(day, _meals_on(db, user, day, zone)) for day in days], shown
        )
    return day_screen(_meals_on(db, user, today, zone), today, TODAY, zone, shown)


def handle_callback(db, telegram_id: int, data: str, now: datetime | None = None):
    view = data[len(CALLBACK_PREFIX) :]
    tab, opened = view[:1], view[1:] == OPEN
    if tab not in dict(_TABS) or view[1:] not in ("", OPEN):
        logger.warning("stats: unknown callback data %r", data)
        tab, opened = TODAY, False
    return open_view(db, telegram_id, tab, now, opened)
