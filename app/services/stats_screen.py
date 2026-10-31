"""📊 Статистика: the day card for today and yesterday, the last 7 days.

    📊 Статистика → <b>📊 Сегодня, вт 28 октября</b> — the times of food, whole
    grains and protein anchors if there were any (``day_card``), ticked routines
    [✅ Готово] [🔄 Другой шаг] [😴 Не сейчас] — today's one step (``next_step``)
    [💊 Прогулка] — up to three ticks of the user's routines, then [💊 Ещё N]
    [🎯 Выбрать другое] [🔋 Силы: обычно]
    [🔢 Цифры] [🍽 Приёмы]
    [✓ Сегодня] [Вчера] [7 дней]
    7 дней → <b>📊 7 дней, 24–30 октября</b> — different plants, the days of each
    food group, ticked routines, at most one finding (``week_card``)

Sunday's message is the same card for the calendar week, with its own button
(``SUMMARY``) and no tabs; ``week_push`` sends it.

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
from app.services import (
    day_card,
    food_lists,
    next_step,
    routines,
    step_count,
    week_card,
)
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
SUMMARY = "i"  # i:<its Monday>[:n]: Sunday's message, on the week it was sent for
# The next step: s:<verb>:<day>:<the step shown>:<n or empty>; e:<mode>:<n or empty>
STEP, ENERGY = "s", "e"
# Choosing the direction: c:<n or empty> opens the screen, c:<id>:<n or empty> chooses.
CHOOSE = "c"
STEP_FIRST_BUTTON = "🎯 Выбрать один шаг"
STEP_OTHER_BUTTON = "🎯 Выбрать другое"
_CHOICE_DAYS = 14  # records the «earlier» hour is taken from
SUMMARY_NOTE = (
    "Итог приходит по воскресеньям в 22:00 без звука. Выключить — ⚙️ Настройки."
)
_LONG_AGO = datetime(2000, 1, 1, tzinfo=UTC)
TICK_ROW = 3
ADD = "add"  # the empty day's «🍽 Добавить приём пищи»; the conversation takes it
ADD_DATA = CALLBACK_PREFIX + ADD
MEALS_BUTTON = "🍽 Приёмы"
LOOK_DAY, LOOK_EVENING = "day", "evening"
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
_MONTHS_IN = (
    "январе", "феврале", "марте", "апреле", "мае", "июне",
    "июле", "августе", "сентябре", "октябре", "ноябре", "декабре",
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
    current: str, opened: bool | None = None, above: tuple = (), beside: tuple = ()
) -> InlineKeyboardMarkup:
    """The tabs, under the rows in ``above``; ``opened`` is None when ⚙️ shows the
    numbers anyway (no button), else whether «🔢 Цифры» is open on this view. An
    open view stays open across the tabs. ``beside``: buttons on the numbers' row."""
    keep = OPEN if opened else ""
    rows = [*above]
    row = [*beside]
    if opened is not None:
        flip = "" if opened else OPEN
        row.insert(
            0,
            InlineKeyboardButton(
                toggle_label(opened), callback_data=CALLBACK_PREFIX + current + flip
            ),
        )
    if row:
        rows.append(row)
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
    ticked: tuple = (),
    look: str = LOOK_DAY,
    step: next_step.View | None = None,
) -> Screen:
    """``opened``: see ``_tabs``; the numbers show unless it is False. ``step``:
    today's next step; an empty day never gets one."""
    head = "Сегодня" if tab == TODAY else "Вчера"
    lines = [f"<b>📊 {head}, {_card_title(day)}</b>", ""]
    ticks = [routine_line] if routine_line else []
    picture = {
        "view": "day",
        "theme": look,
        "date": _card_title(day),
        "title": head,
        "routines": [{"name": name, "done": True} for name in ticked],
    }
    if not meals:
        # The log may be incomplete; the day was not a failure (US-D2).
        above = routine_rows
        if tab == TODAY:
            lines.append(
                "🍽 Похоже, не всё записано? Пришли фото или напиши, что было, — "
                "карточка соберётся сама."
            )
            picture |= {
                "emptyTitle": "Похоже, не всё записано?",
                "emptyNote": "Пришли фото или напиши, что было, — "
                "карточка соберётся сама.",
            }
            add = InlineKeyboardButton("🍽 Добавить приём пищи", callback_data=ADD_DATA)
            above = (*routine_rows, [add])  # under the ticks: they never move
        else:
            lines.append("За этот день записей нет.")
            picture["emptyTitle"] = "За этот день записей нет"
        # Nothing to count: no «🔢 Цифры» on an empty day.
        if ticks:
            lines += ["", *ticks]
        picture["empty"] = True
        return Screen("\n".join(lines), _tabs(tab, None, above), picture=picture)
    mode_line = next_step.MODES[step.mode][2] if step and step.step else None
    if mode_line:
        lines += [mode_line, ""]
        picture["mode"] = step.mode
    if first_time:
        tail = " Шаг внизу — по желанию." if step else ""
        lines += [f"<i>Это карточка дня: что было, без оценок.{tail}</i>", ""]
    facts = day_card.facts(meals, zone, _meal_name)
    lines += day_card.lines(facts, html.escape)
    picture |= {
        "meals": day_card.marks(meals, zone),
        "gap": f"{facts.gap.capitalize()} длинный перерыв" if facts.gap else None,
        "grain": facts.grain,
        "anchor": facts.anchor,
        "foods": day_card.foods_label(facts),
    }
    lines += ticks
    # The way to this day's meals: only ``meal_card`` reads this data.
    beside = (
        InlineKeyboardButton(
            MEALS_BUTTON, callback_data=f"ml:dl:{_day_code(day)}:{tab}"
        ),
    )
    if step:
        told = next_step.lines(step)
        if told:
            lines += ["", *told]
            picture["step"] = next_step.picture(step)
        routine_rows = (*_step_rows(step, day, opened), *routine_rows)
    if opened is not False:
        lines += ["", _numbers_block(meals, zone)]
    markup = _tabs(tab, opened, routine_rows, beside)
    return Screen("\n".join(lines), markup, picture=picture)


def _week_numbers(days: list[tuple[date, list[Meal]]]) -> str:
    """Only the days with records: an empty day is not a gap to look at."""
    logged = [(day, meals) for day, meals in days if meals]
    everything = [meal for _day, meals in logged for meal in meals]
    average = _sum(everything, "calories") / len(logged)
    rows = [
        f"🔢 В среднем за день с записями: ≈ {_kcal(average)} · "
        f"{_macros(everything, len(logged))}"
    ]
    totals = [_sum(meals, "calories") for _day, meals in logged]
    top = max(totals) or 1.0
    for (day, _meals), total in zip(logged, totals):
        filled = max(1, round(BAR_WIDTH * total / top))
        bar = "▰" * filled + "▱" * (BAR_WIDTH - filled)
        amount = _kcal(total).removesuffix(" ккал")
        rows.append(f"{_WEEKDAYS[day.weekday()]} {bar} {amount}")
    return "<blockquote expandable>" + "\n".join(rows) + "</blockquote>"


def _summary_markup(
    monday: date, opened: bool | None, above: tuple = ()
) -> InlineKeyboardMarkup | None:
    """No tabs: the summary keeps its own week, days after it was sent."""
    if opened is None:
        return InlineKeyboardMarkup(above) if above else None
    flip = "" if opened else f":{OPEN}"
    data = f"{CALLBACK_PREFIX}{SUMMARY}:{_day_code(monday)}{flip}"
    toggle = InlineKeyboardButton(toggle_label(opened), callback_data=data)
    return InlineKeyboardMarkup([*above, [toggle]])


def week_screen(
    days: list[tuple[date, list[Meal]]],
    zone: ZoneInfo,
    opened: bool | None = None,
    earlier: list[Meal] = (),
    routine_line: str | None = None,
    ticked: tuple = (),
    look: str = LOOK_DAY,
    summary: date | None = None,
    first: bool = False,
    count: step_count.Count | None = None,
    month: str = "",
    count_rows: tuple = (),
) -> Screen:
    """``days`` oldest first. The 7 days tab, or with ``summary`` (its Monday) the
    calendar week as Sunday's message. No count of days with records and no empty
    days anywhere: two records must not look worse than seven (G5-R07). ``count``: the
    chosen step's days against the week before (G5-R26), the one comparison here."""
    head = "Неделя" if summary else f"{WEEK_DAYS} дней"
    when = week_card.period(days[0][0], days[-1][0], _MONTHS_FULL)
    new_label = "Новое на этой неделе" if summary else f"Новое за {WEEK_DAYS} дней"
    lines = [f"<b>📊 {head}, {when}</b>", ""]
    ticks = [routine_line] if routine_line else []
    picture = {
        "view": "week",
        "theme": look,
        "period": when,
        "title": head,
        "routines": list(ticked),
    }
    if not any(meals for _day, meals in days):
        lines.append(
            f"🍽 За {WEEK_DAYS} дней записей нет. Карточка соберётся с первой записью."
        )
        if ticks:
            lines += ["", *ticks]
        picture |= {
            "empty": True,
            "emptyTitle": f"За {WEEK_DAYS} дней записей нет",
            "emptyNote": "Карточка соберётся с первой записью.",
        }
        add = InlineKeyboardButton("🍽 Добавить приём пищи", callback_data=ADD_DATA)
        markup = None if summary else _tabs(WEEK, None, ([add],))
        return Screen("\n".join(lines), markup, picture=picture)
    week = week_card.facts(days, list(earlier), zone)
    if week_card.has_tags(week):
        lines += week_card.lines(week)
    else:
        lines.append(week_card.NO_TAGS)
        picture |= {
            "emptyTitle": "Записи есть",
            "emptyNote": "Группы в них пока не видны.",
        }
    lines += ticks
    finding = week_card.finding_line(week, new_label, html.escape)
    if finding:
        lines.append(finding)
    counted = step_count.lines(count, month) if count else []
    if counted:
        lines += ["", *counted]
        picture["count"] = {
            "text": counted[0].removeprefix("🎯 "),
            "note": counted[1] if len(counted) > 1 else None,
        }
    picture |= {
        "plants": week.plants,
        "plantsLabel": week_card.plants_label(week.plants),
        "groups": [{"key": key, "days": count} for key, count in week.groups],
        "win": week_card.finding_block(week, new_label),
    }
    if opened is not False:
        lines += ["", _week_numbers(days)]
    if first:
        lines += ["", f"<i>{SUMMARY_NOTE}</i>"]
    rows = count_rows if counted else ()
    markup = (
        _summary_markup(summary, opened, rows) if summary else _tabs(WEEK, opened, rows)
    )
    return Screen("\n".join(lines), markup, picture=picture)


# --- the next step ----------------------------------------------------------


def _choose_button(label: str, opened: bool | None) -> InlineKeyboardButton:
    keep = OPEN if opened else ""
    return InlineKeyboardButton(
        label, callback_data=f"{CALLBACK_PREFIX}{CHOOSE}:{keep}"
    )


def _step_rows(step: next_step.View, day: date, opened: bool | None) -> list:
    if step.step is None:
        return [[_choose_button(STEP_FIRST_BUTTON, opened)]]
    keep = OPEN if opened else ""
    other = _choose_button(STEP_OTHER_BUTTON, opened)

    def button(label: str, verb: str, **extra) -> InlineKeyboardButton:
        data = f"{CALLBACK_PREFIX}{STEP}:{verb}:{_day_code(day)}:{step.step.id}:{keep}"
        return InlineKeyboardButton(label, callback_data=data, **extra)

    # Beside 🎯 where the row has room; the two states below hold two buttons already.
    energy = _energy_button(step.mode, opened)
    if step.state == next_step.DONE:
        return [[button("↩️ Отменить", next_step.UNDO), other], [energy]]
    if step.state == next_step.LATER:
        return [[button("👣 Вернуть шаг", next_step.BRING_BACK), other], [energy]]
    return [
        [
            # PTB 21.9 predates ``style``: see meal_draft's «✅ Сохранить».
            button("✅ Готово", next_step.TAKE, api_kwargs={"style": "success"}),
            button("🔄 Другой шаг", next_step.SWAP),
            button("😴 Не сейчас", next_step.PUT_OFF),
        ],
        [other, energy],
    ]


def _energy_button(mode: str, opened: bool | None) -> InlineKeyboardButton:
    """The mode it leads to travels with it: a double tap moves it once."""
    keep = OPEN if opened else ""
    wanted = next_step.next_energy(mode)
    data = f"{CALLBACK_PREFIX}{ENERGY}:{wanted}:{keep}"
    return InlineKeyboardButton(next_step.MODES[mode][0], callback_data=data)


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


def _stale_card(db, telegram_id: int, now: datetime | None, opened: bool) -> Screen:
    card = open_view(db, telegram_id, TODAY, now, opened)
    notice = "<i>Это была карточка прошлого дня, вот сегодняшняя.</i>"
    return replace(card, text=f"{notice}\n\n{card.text}")


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

    shape = 2 if verb == MORE else 4
    day = rest[0] if verb == MORE else rest[1] if len(rest) > 1 else None
    if len(rest) != shape:
        return today_card(None)  # not ours to explain: nothing happened on a day
    if day != _day_code(today):
        return _stale_card(db, telegram_id, now, opened)
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


def _look(moment: datetime) -> str:
    return LOOK_EVENING if moment.hour >= 20 or moment.hour < 6 else LOOK_DAY


def _first_day_card(db, user: User | None) -> bool:
    """True once, the first time a user sees a card with food on it."""
    if user is None or (user.settings or {}).get(DAY_CARD_SEEN):
        return False
    user.settings = {**(user.settings or {}), DAY_CARD_SEEN: True}
    db.commit()
    return True


def week_bounds_utc(first: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    """The seven local days from ``first`` as a UTC [start, end) range."""
    last = first + timedelta(days=WEEK_DAYS - 1)
    return _day_bounds(first, zone)[0], _day_bounds(last, zone)[1]


def _week_view(
    db,
    user: User | None,
    first: date,
    zone: ZoneInfo,
    opened: bool | None,
    look: str,
    summary: date | None = None,
    note: bool = False,
) -> Screen:
    dates = [first + timedelta(days=shift) for shift in range(WEEK_DAYS)]
    done, earlier = set(), []
    if user is not None:
        ticks = crud_routine_tick.between(db, user.id, dates[0], dates[-1])
        done = {tick.routine_id for tick in ticks}
        start = _day_bounds(first, zone)[0]
        earlier = crud_meal.get_user_meals_between(db, user.id, _LONG_AGO, start)
    count, month = _step_count(db, user, dates[0], dates[-1], zone)
    return week_screen(
        [(day, _meals_on(db, user, day, zone)) for day in dates],
        zone,
        opened,
        earlier,
        routines.ticked_line(user, done),
        tuple(routines.ticked_names(user, done)),
        look,
        summary,
        note,
        count,
        month,
        _count_rows(user, count, opened),
    )


def _step_count(
    db, user: User | None, first: date, last: date, zone: ZoneInfo
) -> tuple[step_count.Count | None, str]:
    """The chosen direction's days in ``first``..``last`` against the week before."""
    wanted = next_step.chosen(user.settings or {}) if user else None
    if wanted not in next_step.FOCUSES:
        return None, ""
    month_start = last.replace(day=1)
    since = min(month_start, first - timedelta(days=WEEK_DAYS))
    # A day past ``last``: its small hours can still belong to ``last``'s own day.
    meals = crud_meal.get_user_meals_between(
        db,
        user.id,
        _day_bounds(since, zone)[0],
        _day_bounds(last + timedelta(days=1), zone)[1],
    )
    count = step_count.count(
        meals, user.settings[next_step.FOCUS], zone, first, last, month_start
    )
    return count, _MONTHS_IN[last.month - 1]


def _count_rows(
    user: User | None, count: step_count.Count | None, opened: bool | None
) -> tuple:
    """Only when the count did not rise: then the choice is worth a button."""
    if count is None or step_count.state(count) not in step_count.ASKS:
        return ()
    row = []
    lower = next_step.lower_energy(next_step.energy(user.settings or {}))
    if lower:
        keep = OPEN if opened else ""
        data = f"{CALLBACK_PREFIX}{ENERGY}:{lower}:{keep}"
        row.append(InlineKeyboardButton("🪫 Сделать проще", callback_data=data))
    row.append(_choose_button(STEP_OTHER_BUTTON, opened))
    return (row,)


def summary_view(
    db,
    user: User,
    monday: date,
    now: datetime,
    first: bool = False,
    opened: bool = False,
) -> Screen:
    """Sunday's message; ``first`` adds the line saying how to turn it off."""
    zone = user_zone(user)
    shown = None if shows_numbers(user) else opened
    look = _look(local_time(now, zone))
    return _week_view(db, user, monday, zone, shown, look, monday, first)


def _summary_callback(db, telegram_id: int, parts: list, now) -> Screen | None:
    """The numbers button on Sunday's message; None when the data is not one."""
    code = parts[1] if len(parts) > 1 else ""
    keep = parts[2:]
    try:
        monday = datetime.strptime(code, "%Y%m%d").date()
    except ValueError:
        return None
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if user is None or monday.weekday() != 0 or keep not in ([], [OPEN]):
        return None
    moment = now or datetime.now(UTC)
    return summary_view(db, user, monday, moment, opened=bool(keep))


def _group_days(db, user: User, today: date, zone: ZoneInfo) -> dict:
    """Days per food group over the seven days up to today."""
    first = today - timedelta(days=WEEK_DAYS - 1)
    dates = [first + timedelta(days=shift) for shift in range(WEEK_DAYS)]
    days = [(day, _meals_on(db, user, day, zone)) for day in dates]
    return dict(week_card.facts(days, [], zone).groups)


def _save_settings(db, user: User, values: dict) -> None:
    if values != (user.settings or {}):
        user.settings = values
        db.commit()


def _step_view(db, user: User, today: date, zone: ZoneInfo) -> next_step.View:
    if next_step.energy(user.settings or {}) != next_step.NORMAL:
        food_lists.seed(db, user, _day_bounds(today, zone)[1])
    values = next_step.current(
        user.settings or {}, today, lambda: _group_days(db, user, today, zone)
    )
    _save_settings(db, user, values)
    return next_step.view(values)


def _step_callback(
    db, telegram_id: int, parts: list, now: datetime | None
) -> Screen | None:
    """A tap on the step or on «🔋 Силы»; None when the data is not one. The card's
    day travels in the step's buttons, as in the ticks."""
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    if user is None:
        return None
    verb, *rest = parts
    keep = rest[-1] if rest else None
    if keep not in ("", OPEN) or len(rest) != (4 if verb == STEP else 2):
        return None
    zone = user_zone(user)
    today = local_time(now or datetime.now(UTC), zone).date()

    def days() -> dict:
        return _group_days(db, user, today, zone)

    if verb == ENERGY:
        # The modes below the usual one look here first, and the list may be unopened.
        food_lists.seed(db, user, _day_bounds(today, zone)[1])
        values, toast = next_step.switch_energy(
            user.settings or {}, rest[0], today, days
        )
    else:
        action, day, shown = rest[:3]
        if action not in next_step.VERBS:
            return None
        if day != _day_code(today):
            try:
                datetime.strptime(day, "%Y%m%d")
            except ValueError:
                return None
            return _stale_card(db, telegram_id, now, keep == OPEN)
        values, toast = next_step.tap(user.settings or {}, action, shown, today, days)
    _save_settings(db, user, values)
    return replace(open_view(db, telegram_id, TODAY, now, keep == OPEN), toast=toast)


def _recent_clock(db, user: User, today: date, zone: ZoneInfo) -> dict | None:
    start = _day_bounds(today - timedelta(days=_CHOICE_DAYS - 1), zone)[0]
    end = _day_bounds(today, zone)[1]
    meals = crud_meal.get_user_meals_between(db, user.id, start, end)
    return step_count.early_clock(meals, zone)


def _choice_screen(db, user: User, today: date, zone: ZoneInfo, opened: bool) -> Screen:
    """Text alone, no picture: it is a question, there is nothing here to look at."""
    settings = user.settings or {}
    in_use = next_step.chosen(settings)
    keep = OPEN if opened else ""
    found = next_step.options(
        settings,
        _group_days(db, user, today, zone),
        _recent_clock(db, user, today, zone) is not None,
    )
    if in_use in next_step.FOCUSES and in_use not in found:
        found = [*found, in_use]
    labels = [(key, next_step.FOCUSES[key].button) for key in found]
    labels.append((next_step.RECORDS, next_step.RECORDS_BUTTON))
    rows = [
        [
            InlineKeyboardButton(
                ("✓ " if key == in_use else "") + label,
                callback_data=f"{CALLBACK_PREFIX}{CHOOSE}:{key}:{keep}",
            )
        ]
        for key, label in labels
    ]
    back = CALLBACK_PREFIX + TODAY + keep
    rows.append([InlineKeyboardButton("⬅️ Сегодня", callback_data=back)])
    head = "теперь" if in_use else "первым"
    text = (
        f"🎯 <b>Что попробовать {head}?</b>\n"
        "Одно на выбор. Сменить можно в любой момент."
    )
    return Screen(text, InlineKeyboardMarkup(rows))


def _choose_callback(
    db, telegram_id: int, parts: list, now: datetime | None
) -> Screen | None:
    """The choice screen, or a choice made on it; None when the data is not one."""
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    rest = parts[1:]
    if user is None or len(rest) not in (1, 2) or rest[-1] not in ("", OPEN):
        return None
    opened = rest[-1] == OPEN
    zone = user_zone(user)
    today = local_time(now or datetime.now(UTC), zone).date()
    if len(rest) == 1:
        return _choice_screen(db, user, today, zone, opened)
    wanted = rest[0]
    if wanted != next_step.RECORDS and wanted not in next_step.FOCUSES:
        return None
    settings = user.settings or {}
    extra = None
    if wanted == next_step.EARLY and next_step.chosen(settings) != wanted:
        extra = _recent_clock(db, user, today, zone)
        if extra is None:
            # An old button: the records no longer give an hour.
            return open_view(db, telegram_id, TODAY, now, opened)
    _save_settings(db, user, next_step.choose(settings, wanted, extra))
    toast = "📋 Только записи" if wanted == next_step.RECORDS else "🎯 Шаг выбран"
    return replace(open_view(db, telegram_id, TODAY, now, opened), toast=toast)


def open_view(
    db,
    telegram_id: int,
    tab: str = TODAY,
    now: datetime | None = None,
    opened: bool = False,
):
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    zone = user_zone(user)
    moment = local_time(now or datetime.now(UTC), zone)
    today = moment.date()
    look = _look(moment)
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
            ticked=tuple(routines.ticked_names(user, done)),
            look=look,
        )
    if tab == WEEK:
        first = today - timedelta(days=WEEK_DAYS - 1)
        return _week_view(db, user, first, zone, shown, look)
    due, done = _day_ticks(db, user, today)
    meals = _meals_on(db, user, today, zone)
    step = _step_view(db, user, today, zone) if meals and user else None
    # The lowest mode keeps the keyboard to the step: ticks wait, typed names still work.
    hide_ticks = bool(step) and bool(step.step) and step.mode == next_step.SIMPLE
    return day_screen(
        meals,
        today,
        TODAY,
        zone,
        shown,
        routines.ticked_line(user, done),
        () if hide_ticks else _routine_rows(due, done, today, OPEN if opened else ""),
        first_time=bool(meals) and _first_day_card(db, user),
        ticked=tuple(routines.ticked_names(user, done)),
        look=look,
        step=step,
    )


def handle_callback(db, telegram_id: int, data: str, now: datetime | None = None):
    view = data[len(CALLBACK_PREFIX) :]
    parts = view.split(":")
    if parts[0] in (TICK, MORE_TICK, MORE) and len(parts) > 1:
        return _tick_view(db, telegram_id, parts, now)
    if parts[0] == SUMMARY:
        screen = _summary_callback(db, telegram_id, parts, now)
        if screen is not None:
            return screen
    if parts[0] == CHOOSE:
        screen = _choose_callback(db, telegram_id, parts, now)
        if screen is not None:
            return screen
    if parts[0] in (STEP, ENERGY):
        screen = _step_callback(db, telegram_id, parts, now)
        if screen is not None:
            return screen
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
