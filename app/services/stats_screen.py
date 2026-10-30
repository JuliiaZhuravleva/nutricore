"""📊 Статистика: the day card for today and yesterday, the last 7 days.

    📊 Статистика → <b>📊 Сегодня, вт 28 октября</b> — the times of food, whole
    grains and protein anchors if there were any (``day_card``), ticked routines
    [💊 Прогулка] — up to three ticks of the user's routines, then [💊 Ещё N]
    [🔢 Цифры]
    [✓ Сегодня] [Вчера] [7 дней]

The numbers are the saved meals' own, summed, and there is no goal (stage 5,
G5-R03). Unless ⚙️ shows them, they wait behind «🔢 Цифры» (G5-R01), in a collapsed
quote. Only ticked routines are named in the text, on any day; an unticked one is a
button today and nothing afterwards (G5-R08). Days are the user's calendar days
(their ⚙️ time zone), not UTC. Every tab edits the same message; the callback data
carries the whole view, so a tap works after a restart too. Telegram-free apart
from the keyboard, like ``favorites``.
"""

from __future__ import annotations

import html
import logging
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.crud.crud_meal import crud_meal
from app.crud.crud_routine_tick import crud_routine_tick
from app.models.meal import Meal
from app.models.user import User
from app.services import day_card, routines
from app.services.day_card import local_time
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
# Routine ticks: k:<id>:<day>:<1 tick, 0 untick>:<n or empty>; m:<day>:<n or empty>
TICK, MORE_TICK, MORE = "k", "K", "m"
TICK_ROW = 3
ADD = "add"  # the empty day's «🍽 Добавить приём пищи»; the conversation takes it
ADD_DATA = CALLBACK_PREFIX + ADD
DAY_CARD_SEEN = "day_card_seen"  # in ``User.settings``: the one-line intro was shown
_TABS = ((TODAY, "Сегодня"), (YESTERDAY, "Вчера"), (WEEK, "7 дней"))

_WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
_MONTHS = (
    "янв", "фев", "мар", "апр", "мая", "июн",
    "июл", "авг", "сен", "окт", "ноя", "дек",
)  # fmt: skip
_MONTHS_FULL = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)  # fmt: skip


def _day_bounds(day: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    """The local calendar day as a UTC [start, end) range."""
    start = datetime.combine(day, time(), tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time(), tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC)


def day_title(day: date) -> str:
    return f"{_WEEKDAYS[day.weekday()]} {day.day} {_MONTHS[day.month - 1]}"


def _card_title(day: date) -> str:
    return f"{_WEEKDAYS[day.weekday()]} {day.day} {_MONTHS_FULL[day.month - 1]}"


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


def _tabs(
    current: str, opened: bool | None = None, above: tuple = ()
) -> InlineKeyboardMarkup:
    """The tabs, under the rows in ``above``; ``opened`` is None when ⚙️ shows the
    numbers anyway (no button), else whether «🔢 Цифры» is open on this view. An
    open view stays open across the tabs."""
    keep = OPEN if opened else ""
    rows = [*above]
    if opened is not None:
        flip = "" if opened else OPEN
        rows.append(
            [
                InlineKeyboardButton(
                    toggle_label(opened), callback_data=CALLBACK_PREFIX + current + flip
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                ("✓ " if code == current else "") + label,
                callback_data=CALLBACK_PREFIX + code + keep,
            )
            for code, label in _TABS
        ]
    )
    return InlineKeyboardMarkup(rows)


def _when(meal: Meal, zone: ZoneInfo) -> str:
    return local_time(meal.meal_time, zone).strftime("%H:%M") if meal.meal_time else "—"


# --- screens ----------------------------------------------------------------


def _numbers_block(meals: list[Meal], zone: ZoneInfo) -> str:
    """The day in numbers, collapsed: only a tap away and never the first thing."""
    rows = [f"🔢 За день: ≈ {_kcal(_sum(meals, 'calories'))} · {_macros(meals)}"]
    for meal in meals:
        when = (
            day_card.plain_time(local_time(meal.meal_time, zone))
            if meal.meal_time
            else "—"
        )
        name = html.escape(_meal_name(meal))
        rows.append(f"{when} {name} — ≈ {_kcal(_sum([meal], 'calories'))}")
    return "<blockquote expandable>" + "\n".join(rows) + "</blockquote>"


def day_screen(
    meals: list[Meal],
    day: date,
    tab: str,
    zone: ZoneInfo,
    opened: bool | None = None,
    routine_line: str | None = None,
    routine_rows: tuple = (),
    first_time: bool = False,
) -> Screen:
    """``opened``: see ``_tabs``; the numbers show unless it is False."""
    head = "Сегодня" if tab == TODAY else "Вчера"
    lines = [f"<b>📊 {head}, {_card_title(day)}</b>", ""]
    ticks = [routine_line] if routine_line else []
    if not meals:
        # The log may be incomplete; the day was not a failure (US-D2).
        above = routine_rows
        if tab == TODAY:
            lines.append(
                "🍽 Похоже, не всё записано? Пришли фото или напиши, что было, — "
                "карточка соберётся сама."
            )
            add = InlineKeyboardButton("🍽 Добавить приём пищи", callback_data=ADD_DATA)
            above = (*routine_rows, [add])  # under the ticks: they never move
        else:
            lines.append("За этот день записей нет.")
        # Nothing to count: no «🔢 Цифры» on an empty day.
        if ticks:
            lines += ["", *ticks]
        return Screen("\n".join(lines), _tabs(tab, None, above))
    if first_time:
        lines += ["<i>Это карточка дня: что было, без оценок.</i>", ""]
    lines += day_card.lines(day_card.facts(meals, zone, _meal_name), html.escape)
    lines += ticks
    if opened is not False:
        lines += ["", _numbers_block(meals, zone)]
    return Screen("\n".join(lines), _tabs(tab, opened, routine_rows))


def week_screen(
    days: list[tuple[date, list[Meal]]],
    zone: ZoneInfo,
    opened: bool | None = None,
) -> Screen:
    """``days`` oldest first, today last. Without the numbers it lists only the
    days that have records, each with its meal times: an empty day is not shown
    as a gap, and no count invites comparing one week's logging with another's."""
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
            lines.append(f"{title} · {', '.join(_when(m, zone) for m in meals)}")
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


# --- routine ticks ----------------------------------------------------------


def _day_code(day: date) -> str:
    return day.strftime("%Y%m%d")


def _tick_button(routine: dict, ticked: bool, verb: str, day: date, keep: str):
    label = ("✓ " if ticked else "") + routines.button_label(routine)
    # What the button shows travels with it, so an old card or a double tap can
    # only ask for that state, never flip one set elsewhere.
    want = "0" if ticked else "1"
    data = f"{CALLBACK_PREFIX}{verb}:{routine['id']}:{_day_code(day)}:{want}:{keep}"
    return InlineKeyboardButton(label, callback_data=data)


def _day_ticks(db, user: User | None, day: date) -> tuple[list[dict], set]:
    """What the card offers on ``day``, and the ids ticked that day."""
    if user is None:
        return [], set()
    first, last = routines.week_bounds(day)
    ticks = crud_routine_tick.between(db, user.id, first, last)
    done = {tick.routine_id for tick in ticks if tick.day == day}
    return routines.due(user, ticks, day), done


def _routine_rows(due: list[dict], done: set, day: date, keep: str) -> tuple:
    """Up to three ticks in their own order (a tap never moves a button), then
    «Ещё N»; the rest of the card stays one screen."""
    if not due:
        return ()
    shown = [
        _tick_button(routine, routine["id"] in done, TICK, day, keep)
        for routine in due[:TICK_ROW]
    ]
    rows = routines.button_rows(due[:TICK_ROW], shown)
    if len(due) > TICK_ROW:
        rows.append(
            [
                InlineKeyboardButton(
                    f"💊 Ещё {len(due) - TICK_ROW}",
                    callback_data=f"{CALLBACK_PREFIX}{MORE}:{_day_code(day)}:{keep}",
                )
            ]
        )
    return tuple(rows)


def _more_screen(user: User, due: list[dict], done: set, day: date, keep: str):
    buttons = [
        _tick_button(routine, routine["id"] in done, MORE_TICK, day, keep)
        for routine in due
    ]
    rows = routines.button_rows(due, buttons)
    rows.append(
        [
            InlineKeyboardButton(
                "⬅️ Сегодня", callback_data=CALLBACK_PREFIX + TODAY + keep
            )
        ]
    )
    lines = ["<b>💊 Рутины на сегодня</b>", ""]
    line = routines.ticked_line(user, done)
    lines.append(line or "Отметка ставится и снимается нажатием.")
    return Screen("\n".join(lines), InlineKeyboardMarkup(rows))


def _tick_view(db, telegram_id: int, parts: list, now: datetime | None) -> Screen:
    """A tap on a tick or on «Ещё N». The card's day travels in the button, so a
    card left open overnight cannot tick the wrong day."""
    verb, *rest = parts
    keep = rest[-1] if rest and rest[-1] == OPEN else ""
    opened = keep == OPEN
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    today = local_time(now or datetime.now(UTC), user_zone(user)).date()

    def today_card(toast: str | None) -> Screen:
        return replace(open_view(db, telegram_id, TODAY, now, opened), toast=toast)

    def stale_card() -> Screen:
        card = open_view(db, telegram_id, TODAY, now, opened)
        notice = "<i>Это была карточка прошлого дня, вот сегодняшняя.</i>"
        return replace(card, text=f"{notice}\n\n{card.text}")

    shape = 2 if verb == MORE else 4
    day = rest[0] if verb == MORE else rest[1] if len(rest) > 1 else None
    if len(rest) != shape:
        return today_card(None)  # not ours to explain: nothing happened on a day
    if day != _day_code(today):
        return stale_card()
    due, done = _day_ticks(db, user, today)
    if verb == MORE:
        return _more_screen(user, due, done, today, keep)
    rid, want = rest[0], rest[2]
    routine = next((r for r in due if r["id"] == rid), None)
    if routine is None or want not in ("0", "1"):
        exists = any(r["id"] == rid for r in routines.active(user))
        return today_card(
            "Этой рутины сегодня нет на карточке" if exists else "Этой рутины уже нет"
        )
    if want == "1":
        crud_routine_tick.tick(db, user.id, rid, today)
        toast = f"✓ {routine.get('name')}"
    else:
        crud_routine_tick.untick(db, user.id, rid, today)
        toast = "Отметка снята"
    if verb == MORE_TICK:
        due, done = _day_ticks(db, user, today)
        return replace(_more_screen(user, due, done, today, keep), toast=toast)
    return today_card(toast)


def tick_by_name(
    db, telegram_id: int, text: str | None, now: datetime | None = None
) -> str | None:
    """A typed message that is exactly a routine on today's card ticks it; returns
    its name. Anything else is None, and the message is not a tick."""
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if user is None:
        return None
    wanted = routines.clean_name(text).casefold()
    today = local_time(now or datetime.now(UTC), user_zone(user)).date()
    due, _done = _day_ticks(db, user, today)
    for routine in due:
        if routines.clean_name(str(routine.get("name"))).casefold() == wanted:
            crud_routine_tick.tick(db, user.id, routine["id"], today)
            return routine.get("name")
    return None


# --- operations -------------------------------------------------------------


def _meals_on(db, user: User | None, day: date, zone: ZoneInfo) -> list[Meal]:
    if user is None:
        return []
    start, end = _day_bounds(day, zone)
    return crud_meal.get_user_meals_between(db, user.id, start, end)


def _first_day_card(db, user: User | None) -> bool:
    """True once, the first time a user sees a card with food on it."""
    if user is None or (user.settings or {}).get(DAY_CARD_SEEN):
        return False
    user.settings = {**(user.settings or {}), DAY_CARD_SEEN: True}
    db.commit()
    return True


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
        _due, done = _day_ticks(db, user, day)
        return day_screen(
            _meals_on(db, user, day, zone),
            day,
            YESTERDAY,
            zone,
            shown,
            routines.ticked_line(user, done),
        )
    if tab == WEEK:
        days = [today - timedelta(days=back) for back in range(WEEK_DAYS - 1, -1, -1)]
        return week_screen(
            [(day, _meals_on(db, user, day, zone)) for day in days], zone, shown
        )
    due, done = _day_ticks(db, user, today)
    meals = _meals_on(db, user, today, zone)
    return day_screen(
        meals,
        today,
        TODAY,
        zone,
        shown,
        routines.ticked_line(user, done),
        _routine_rows(due, done, today, OPEN if opened else ""),
        first_time=bool(meals) and _first_day_card(db, user),
    )


def handle_callback(db, telegram_id: int, data: str, now: datetime | None = None):
    view = data[len(CALLBACK_PREFIX) :]
    parts = view.split(":")
    if parts[0] in (TICK, MORE_TICK, MORE) and len(parts) > 1:
        return _tick_view(db, telegram_id, parts, now)
    if view == ADD:
        # Only reaches here mid-step: elsewhere the conversation takes this tap.
        return replace(
            open_view(db, telegram_id, TODAY, now),
            toast="Сначала заверши текущий шаг или нажми /cancel",
        )
    tab, opened = view[:1], view[1:] == OPEN
    if tab not in dict(_TABS) or view[1:] not in ("", OPEN):
        logger.warning("stats: unknown callback data %r", data)
        tab, opened = TODAY, False
    return open_view(db, telegram_id, tab, now, opened)
