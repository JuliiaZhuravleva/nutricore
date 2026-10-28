"""📊 Статистика: what was eaten today, yesterday and over the last 7 days.

    📊 Статистика → <b>📊 Сегодня, вт 28 окт</b> — the meals, then the totals
    [✓ Сегодня] [Вчера] [7 дней]

The numbers are the saved meals' own, summed — no goal yet (Stage 1 adds one).
Days are the owner's calendar days (``DEFAULT_TIMEZONE``), not UTC. Every tab
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

from app.core.config import settings
from app.crud.crud_meal import crud_meal
from app.models.meal import Meal
from app.models.user import User
from app.services.favorites import Screen, favorite_name
from app.services.meal_draft import num

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "st:"
WEEK_DAYS = 7
NAME_MAX = 32
BAR_WIDTH = 8

TODAY, YESTERDAY, WEEK = "t", "y", "w"
_TABS = ((TODAY, "Сегодня"), (YESTERDAY, "Вчера"), (WEEK, "7 дней"))

_WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
_MONTHS = (
    "янв", "фев", "мар", "апр", "мая", "июн",
    "июл", "авг", "сен", "окт", "ноя", "дек",
)  # fmt: skip


def _zone() -> ZoneInfo:
    return ZoneInfo(settings.DEFAULT_TIMEZONE)


def local_time(moment: datetime) -> datetime:
    # SQLite hands back naive datetimes; the column is written in UTC.
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(_zone())


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    """The local calendar day as a UTC [start, end) range."""
    start = datetime.combine(day, time(), tzinfo=_zone())
    end = datetime.combine(day + timedelta(days=1), time(), tzinfo=_zone())
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


def _tabs(current: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    ("✓ " if code == current else "") + label,
                    callback_data=CALLBACK_PREFIX + code,
                )
                for code, label in _TABS
            ]
        ]
    )


# --- screens ----------------------------------------------------------------


def day_screen(meals: list[Meal], day: date, tab: str) -> Screen:
    head = "Сегодня" if tab == TODAY else "Вчера"
    lines = [f"<b>📊 {head}, {day_title(day)}</b>", ""]
    if not meals:
        lines.append(
            "Записей нет. Еда попадает сюда после «✅ Сохранить» "
            "или из ⭐ Избранного."
        )
        return Screen("\n".join(lines), _tabs(tab))
    for meal in meals:
        when = local_time(meal.meal_time).strftime("%H:%M") if meal.meal_time else "—"
        label = f" · {meal.meal_type}" if meal.meal_type else ""
        lines.append(
            f"{when}{label} · {html.escape(_meal_name(meal))} · "
            f"{_kcal(_sum([meal], 'calories'))}"
        )
    lines += [
        "",
        f"<b>🔥 Итого: {_kcal(_sum(meals, 'calories'))}</b>",
        _macros(meals),
    ]
    return Screen("\n".join(lines), _tabs(tab))


def week_screen(days: list[tuple[date, list[Meal]]]) -> Screen:
    """``days`` oldest first, today last."""
    first, last = days[0][0], days[-1][0]
    lines = [
        f"<b>📊 {WEEK_DAYS} дней</b> · {first.day} {_MONTHS[first.month - 1]} – "
        f"{last.day} {_MONTHS[last.month - 1]}",
        "",
    ]
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
    return Screen("\n".join(lines), _tabs(WEEK))


# --- operations -------------------------------------------------------------


def _user_id(db, telegram_id: int) -> int | None:
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    return user.id if user else None


def _meals_on(db, user_id: int | None, day: date) -> list[Meal]:
    if user_id is None:
        return []
    start, end = _day_bounds(day)
    return crud_meal.get_user_meals_between(db, user_id, start, end)


def open_view(db, telegram_id: int, tab: str = TODAY, now: datetime | None = None):
    today = local_time(now or datetime.now(UTC)).date()
    user_id = _user_id(db, telegram_id)
    if tab == YESTERDAY:
        day = today - timedelta(days=1)
        return day_screen(_meals_on(db, user_id, day), day, YESTERDAY)
    if tab == WEEK:
        days = [today - timedelta(days=back) for back in range(WEEK_DAYS - 1, -1, -1)]
        return week_screen([(day, _meals_on(db, user_id, day)) for day in days])
    return day_screen(_meals_on(db, user_id, today), today, TODAY)


def handle_callback(db, telegram_id: int, data: str, now: datetime | None = None):
    tab = data[len(CALLBACK_PREFIX) :]
    if tab not in dict(_TABS):
        logger.warning("stats: unknown callback data %r", data)
        tab = TODAY
    return open_view(db, telegram_id, tab, now)
