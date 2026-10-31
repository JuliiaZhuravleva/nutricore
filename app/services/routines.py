"""💊 Рутины: the user's own daily things, ticked on the day card (stage 5, slice C).

    ⚙️ Настройки → <b>💊 Рутины</b>: the list, [one button each], [➕ Добавить рутину]
    ➕ → a typed name → «В какие дни?» [Каждый день] [По дням] [Раз в неделю]
       → «Какой значок?» [💊] [🚶] [🧘] [💧] [😴] → the list, <i>✓ Добавлено: …</i>
    a routine → [🗓 Дни] [✏️ Название] [🔣 Значок] [🗑 Из рутин]
    🗑 → the list with [↩️ Вернуть «…»], applied at once (no «вы уверены?»)

The list starts empty and the bot ships none: no presets, and it never suggests or
questions a supplement (G5-R08). Routines live in ``User.settings``; a new one is
a hidden draft from the typed name on, so every later step is a stateless tap that
works after a restart. A removed routine stays stored: the removal can be undone,
and a tick on an earlier day still happened. Ticks are rows in ``routine_ticks``,
and a day without a tick leaves nothing to count. Telegram-free apart from the
keyboard, like ``settings_screen``.
"""

from __future__ import annotations

import html
import logging
import uuid
from datetime import date, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.models.user import User
from app.services.favorites import Screen

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "rt:"
ROUTINES = "routines"  # the key in ``User.settings``
NAME_MAX = 30
MAX_ROUTINES = 9  # three rows of three
ICONS = ("💊", "🚶", "🧘", "💧", "😴")
DAILY, DAYS, WEEKLY = "daily", "days", "weekly"

_OPEN = CALLBACK_PREFIX + "open"
_ADD = CALLBACK_PREFIX + "add"
_RENAME = CALLBACK_PREFIX + "ren:"
ASK_PATTERN = f"^({_ADD}|{_RENAME}.+)$"  # these two wait for a typed name
_SETTINGS = "set:open"

_WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
_LIST_NOTE = "Отметки — на карточке дня. Неотмеченное нигде не считается."
_GONE = "Этой рутины уже нет"


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)


def _user(db, telegram_id: int) -> User | None:
    return db.query(User).filter(User.telegram_id == telegram_id).first()


def stored(user: User | None) -> list[dict]:
    """Every routine on record, removed ones and drafts included."""
    value = (user.settings or {}).get(ROUTINES) if user else None
    if value is None:
        return []
    if not isinstance(value, list):
        logger.warning("routines: expected a list, got %s", type(value).__name__)
        return []
    return [item for item in value if isinstance(item, dict) and item.get("id")]


def active(user: User | None) -> list[dict]:
    """The routines the user sees: not removed, not a draft still being set up."""
    return [r for r in stored(user) if not r.get("removed") and not r.get("draft")]


def _save(db, telegram_id: int, routines: list[dict]) -> None:
    user = _user(db, telegram_id)
    if user is None:
        user = User(telegram_id=telegram_id)
        db.add(user)
    # A new dict: SQLAlchemy does not see a JSON value changed in place.
    user.settings = {**(user.settings or {}), ROUTINES: routines}
    db.commit()


def icon_of(routine: dict) -> str:
    icon = routine.get("icon")
    return icon if icon in ICONS else ICONS[0]


def title(routine: dict) -> str:
    """«🚶 Прогулка», escaped for an HTML message."""
    return f"{icon_of(routine)} {html.escape(str(routine.get('name')))}"


def button_label(routine: dict) -> str:
    return f"{icon_of(routine)} {routine.get('name')}"


def button_rows(routines: list[dict], buttons: list) -> list[list]:
    """Rows that keep the names readable: Telegram cuts a label with «…» at about
    12 characters with three in a row. Counted as if ✓ were on, so a tick never
    moves a button."""
    longest = max((len(button_label(r)) + 2 for r in routines), default=0)
    width = 3 if longest <= 12 else 2 if longest <= 20 else 1
    return [buttons[i : i + width] for i in range(0, len(buttons), width)]


def schedule_label(routine: dict) -> str:
    schedule = routine.get("schedule")
    if schedule == WEEKLY:
        return "раз в неделю"
    days = sorted(day for day in routine.get("days") or [] if day in range(7))
    if schedule != DAYS or len(days) == 7:
        return "каждый день"
    return ", ".join(_WEEKDAYS[day] for day in days)


def week_bounds(day: date) -> tuple[date, date]:
    """Monday to Sunday around ``day``: what «раз в неделю» counts by."""
    monday = day - timedelta(days=day.weekday())
    return monday, monday + timedelta(days=6)


def due(user: User | None, ticks: list, day: date) -> list[dict]:
    """The routines the day card offers on ``day``. ``ticks``: the week's.

    A weekly routine shows every day until it is ticked, then only on that day:
    no day of the week ever reads as a miss. One already ticked on ``day`` stays
    whatever its schedule says now, so the tick can be taken back.
    """
    shown = []
    today = {tick.routine_id for tick in ticks if tick.day == day}
    for routine in active(user):
        schedule = routine.get("schedule")
        if routine["id"] in today:
            shown.append(routine)
            continue
        if schedule == DAYS and day.weekday() not in (routine.get("days") or []):
            continue
        if schedule == WEEKLY and any(
            tick.routine_id == routine["id"] and tick.day != day for tick in ticks
        ):
            continue
        shown.append(routine)
    return shown


def ticked_line(user: User | None, ticked_ids: set) -> str | None:
    """«💊 Витамин D ✓ · 🚶 Прогулка ✓», or None: what was not ticked is not named."""
    names = [
        f"{title(routine)} ✓" for routine in stored(user) if routine["id"] in ticked_ids
    ]
    # dict: removed and added again under one name, it is still one line item.
    return " · ".join(dict.fromkeys(names)) or None


def ticked_names(user: User | None, ticked_ids: set) -> list[str]:
    """The names alone, for the picture: its chip draws the mark."""
    names = [routine["name"] for routine in stored(user) if routine["id"] in ticked_ids]
    return list(dict.fromkeys(names))


def clean_name(text: str | None) -> str:
    return " ".join((text or "").split())


# --- screens ----------------------------------------------------------------


def list_screen(user: User | None, notice: str | None = None, undo=None) -> Screen:
    """``undo``: a routine just removed, offered back until the next step."""
    routines = active(user)
    lines = [f"<i>{notice}</i>", ""] if notice else []
    lines += ["<b>💊 Рутины</b>", ""]
    if routines:
        lines += [f"{title(r)} — {schedule_label(r)}" for r in routines]
        lines += ["", _LIST_NOTE]
    else:
        lines.append("Пока пусто. Здесь только то, что задаёшь ты: название и дни.")
    rows = []
    if undo:
        rows.append(
            [
                _btn(
                    f"↩️ Вернуть «{undo.get('name')}»",
                    f"{CALLBACK_PREFIX}un:{undo['id']}",
                )
            ]
        )
    buttons = [_btn(button_label(r), f"{CALLBACK_PREFIX}r:{r['id']}") for r in routines]
    rows += button_rows(routines, buttons)
    if len(routines) < MAX_ROUTINES:
        rows.append([_btn("➕ Добавить рутину", _ADD)])
    rows.append([_btn("⬅️ Настройки", _SETTINGS)])
    return Screen("\n".join(lines), InlineKeyboardMarkup(rows))


def _back_row() -> list:
    return [_btn("⬅️ Рутины", _OPEN)]


def name_screen(routine: dict | None = None) -> Screen:
    if routine:
        head = title(routine)
        ask = "Напиши новое название одним сообщением."
    else:
        head = "💊 Новая рутина"
        ask = (
            "Напиши название одним сообщением — например, «Прогулка» "
            "или «Стакан воды утром»."
        )
    return Screen(f"<b>{head}</b>\n\n{ask}", InlineKeyboardMarkup([_back_row()]))


def when_screen(routine: dict) -> Screen:
    rid = routine["id"]
    text = (
        f"<b>{title(routine)}</b>\n\nВ какие дни показывать её на карточке дня?\n\n"
        "<i>Раз в неделю — видна каждый день, пока не отмечена на этой неделе.</i>"
    )
    rows = [
        [
            _btn("Каждый день", f"{CALLBACK_PREFIX}sd:{rid}"),
            _btn("По дням", f"{CALLBACK_PREFIX}dd:{rid}"),
            _btn("Раз в неделю", f"{CALLBACK_PREFIX}sw:{rid}"),
        ],
        _back_row(),
    ]
    return Screen(text, InlineKeyboardMarkup(rows))


def days_screen(routine: dict, toast: str | None = None) -> Screen:
    rid = routine["id"]
    chosen = routine.get("pending_days") or []
    buttons = [
        _btn(
            ("✓ " if index in chosen else "") + name.capitalize(),
            f"{CALLBACK_PREFIX}dt:{rid}:{index}",
        )
        for index, name in enumerate(_WEEKDAYS)
    ]
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    rows.append([_btn("✅ Сохранить", f"{CALLBACK_PREFIX}ds:{rid}"), *_back_row()])
    return Screen(
        f"<b>{title(routine)}</b>\n\nОтметь дни.",
        InlineKeyboardMarkup(rows),
        toast,
    )


def icon_screen(routine: dict) -> Screen:
    buttons = [
        _btn(icon, f"{CALLBACK_PREFIX}ip:{routine['id']}:{index}")
        for index, icon in enumerate(ICONS)
    ]
    rows = [buttons[:3], buttons[3:], _back_row()]
    return Screen(
        f"<b>{title(routine)}</b>\n\nКакой значок?", InlineKeyboardMarkup(rows)
    )


def routine_screen(routine: dict) -> Screen:
    rid = routine["id"]
    rows = [
        [
            _btn("🗓 Дни", f"{CALLBACK_PREFIX}w:{rid}"),
            _btn("✏️ Название", f"{_RENAME}{rid}"),
        ],
        [
            _btn("🔣 Значок", f"{CALLBACK_PREFIX}ic:{rid}"),
            _btn("🗑 Из рутин", f"{CALLBACK_PREFIX}rm:{rid}"),
        ],
        _back_row(),
    ]
    return Screen(
        f"<b>{title(routine)}</b>\n\nДни: {schedule_label(routine)}",
        InlineKeyboardMarkup(rows),
    )


# --- operations -------------------------------------------------------------


def _gone(user: User | None) -> Screen:
    screen = list_screen(user)
    screen.toast = _GONE
    return screen


def ask_name(db, telegram_id: int, data: str) -> tuple[Screen, str | None, bool]:
    """A tap on ➕ or ✏️ → (the screen, the routine being renamed, wait for a name?)."""
    user = _user(db, telegram_id)
    if data == _ADD:
        if len(active(user)) >= MAX_ROUTINES:
            screen = list_screen(user)
            screen.toast = f"Рутин уже {MAX_ROUTINES}: сначала убери одну"
            return screen, None, False
        return name_screen(), None, True
    rid = data[len(_RENAME) :]
    routine = next((r for r in active(user) if r["id"] == rid), None)
    if routine is None:
        return _gone(user), None, False
    return name_screen(routine), rid, True


def submit_name(
    db, telegram_id: int, text: str | None, rename: str | None = None
) -> tuple[Screen, bool]:
    """A typed name → (the next screen, done?). Not done: ask again, nothing saved."""
    user = _user(db, telegram_id)
    name = clean_name(text)
    if not name or len(name) > NAME_MAX:
        return Screen(f"Название — до {NAME_MAX} знаков. Напиши другое.", None), False
    # Here: weight_screen imports stats_screen, which imports this module.
    from app.services.weight_screen import KG_PATTERN

    if KG_PATTERN.match(name):
        # A bare number is a weigh-in everywhere else, and it must stay one.
        return Screen("Это похоже на вес. Напиши другое название.", None), False
    routines = stored(user)
    taken = {
        clean_name(str(r.get("name"))).casefold()
        for r in active(user)
        if r["id"] != rename
    }
    if name.casefold() in taken:
        return Screen("Такая рутина уже есть. Напиши другое название.", None), False
    if rename:
        routine = next((r for r in routines if r["id"] == rename), None)
        if routine is None or routine.get("removed"):
            return _gone(user), True
        _save(
            db,
            telegram_id,
            [{**r, "name": name} if r["id"] == rename else r for r in routines],
        )
        return (
            list_screen(_user(db, telegram_id), f"✓ Название: {html.escape(name)}"),
            True,
        )
    routine = {
        "id": uuid.uuid4().hex[:6],
        "name": name,
        "icon": ICONS[0],
        "schedule": DAILY,
        "days": [],
        "draft": True,
    }
    # An earlier new routine left halfway is dropped here.
    _save(db, telegram_id, [r for r in routines if not r.get("draft")] + [routine])
    return when_screen(routine), True


def _set(db, telegram_id: int, routines: list, rid: str, **values) -> dict:
    changed = None
    result = []
    for routine in routines:
        if routine["id"] == rid:
            routine = {**routine, **values}
            changed = routine
        result.append(routine)
    _save(db, telegram_id, result)
    return changed


def _after_schedule(db, telegram_id: int, routine: dict) -> Screen:
    if routine.get("draft"):
        return icon_screen(routine)
    notice = f"✓ {html.escape(str(routine['name']))} — {schedule_label(routine)}"
    return list_screen(_user(db, telegram_id), notice)


def _cannot_return(user: User | None, routine: dict) -> str | None:
    """Undo from an older message: the list may have changed since."""
    if not routine.get("removed"):
        return None
    current = active(user)
    if len(current) >= MAX_ROUTINES:
        return f"Рутин уже {MAX_ROUTINES}: сначала убери одну"
    name = clean_name(str(routine.get("name"))).casefold()
    if any(clean_name(str(r.get("name"))).casefold() == name for r in current):
        return "Такая рутина уже есть"
    return None


def handle_callback(db, telegram_id: int, data: str) -> Screen:
    user = _user(db, telegram_id)
    if data == _OPEN:
        return list_screen(user)
    if data == _ADD or data.startswith(_RENAME):
        # Only reaches here from a step that keeps its state: a meal being entered.
        screen = list_screen(user)
        screen.toast = "Сначала заверши текущий шаг или нажми /cancel"
        return screen
    parts = data[len(CALLBACK_PREFIX) :].split(":")
    verb, rid = parts[0], parts[1] if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else ""
    routines = stored(user)
    routine = next((r for r in routines if r["id"] == rid), None)
    if routine is None:
        if verb not in ("r", "w", "sd", "sw", "dd", "dt", "ds", "ic", "ip", "rm", "un"):
            logger.warning("routines: unknown callback data %r", data)
            return list_screen(user)
        return _gone(user)
    if verb == "rm" and routine.get("removed"):
        # A double tap: the second one must not take the undo button away.
        notice = f"Убрано: {html.escape(str(routine['name']))}"
        return list_screen(user, notice, undo=routine)
    if routine.get("removed") and verb != "un":
        return _gone(user)
    if verb == "r":
        return routine_screen(routine)
    if verb == "w":
        return when_screen(routine)
    if verb == "sd":
        routine = _set(db, telegram_id, routines, rid, schedule=DAILY, days=[])
        return _after_schedule(db, telegram_id, routine)
    if verb == "sw":
        routine = _set(db, telegram_id, routines, rid, schedule=WEEKLY, days=[])
        return _after_schedule(db, telegram_id, routine)
    if verb == "dd":
        routine = _set(
            db, telegram_id, routines, rid, pending_days=routine.get("days") or []
        )
        return days_screen(routine)
    if verb == "dt" and arg.isdigit() and int(arg) in range(7):
        chosen = set(routine.get("pending_days") or [])
        chosen.symmetric_difference_update({int(arg)})
        routine = _set(db, telegram_id, routines, rid, pending_days=sorted(chosen))
        return days_screen(routine)
    if verb == "ds":
        chosen = routine.get("pending_days") or []
        if not chosen:
            return days_screen(routine, "Отметь хотя бы один день")
        routine = _set(db, telegram_id, routines, rid, schedule=DAYS, days=chosen)
        return _after_schedule(db, telegram_id, routine)
    if verb == "ic":
        return icon_screen(routine)
    if verb == "ip" and arg.isdigit() and int(arg) < len(ICONS):
        draft = routine.get("draft")
        routine = _set(
            db, telegram_id, routines, rid, icon=ICONS[int(arg)], draft=False
        )
        name = html.escape(str(routine["name"]))
        notice = (
            f"✓ Добавлено: {name} — {schedule_label(routine)}"
            if draft
            else f"✓ Значок: {icon_of(routine)} {name}"
        )
        return list_screen(_user(db, telegram_id), notice)
    if verb == "rm":
        routine = _set(db, telegram_id, routines, rid, removed=True)
        notice = f"Убрано: {html.escape(str(routine['name']))}"
        return list_screen(_user(db, telegram_id), notice, undo=routine)
    if verb == "un":
        problem = _cannot_return(user, routine)
        if problem:
            screen = list_screen(user)
            screen.toast = problem
            return screen
        routine = _set(db, telegram_id, routines, rid, removed=False)
        notice = f"✓ Вернулось: {html.escape(str(routine['name']))}"
        return list_screen(_user(db, telegram_id), notice)
    logger.warning("routines: unknown callback data %r", data)
    return list_screen(user)
