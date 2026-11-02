"""⚙️ Настройки: what the bot runs with, and the switches the user can flip.

    ⚙️ Настройки → <b>⚙️ Настройки</b> — time zone, numbers, routines, the cost line
    [🔢 Цифры сразу: выкл] [📋 Черновик: выкл]
    [🗓 Итог недели: вкл] [💸 Стоимость: выкл]
    [🧺 Надёжная еда] [✋ Не предлагать]
    [🕐 Пояс] [💊 Рутины] [💬 Фидбэк]
    [🍽 Обычная еда] [ℹ️ Как это работает]
    💸 tap → <i>✓ Стоимость запросов включена</i> on top, the same screen below
    🔢 tap → <i>✓ Цифры видны сразу</i>: calories and macros on every screen
    🕐 tap → <b>🕐 Часовой пояс</b>: nine cities, [🌐 Другой: UTC±], [⬅️ Настройки]
      a city → <i>✓ Часовой пояс: Москва (UTC+3)</i> on top of ⚙️ Настройки
    🍽 tap → <b>🍽 Обычная еда</b>: [🍖 Всё] [🥚 Без мяса] [🌱 Растения], [⬅️ Настройки]
      an answer → <i>✓ Обычная еда: 🍖 Всё</i> on top of ⚙️ Настройки; no undo button,
      since another answer is one tap away and nothing is lost

A switch flips at once and says so on the next screen (no «вы уверены?»).
The values live in ``User.settings`` (JSON), so a new one needs no migration.
A city follows its own summer time; a bare UTC offset (``Etc/GMT-4``) does not,
which the offset screen says. Telegram-free apart from the keyboard, like
``stats_screen``.
"""

from __future__ import annotations

import html
import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.config import settings
from app.models.user import User
from app.services import diet, food_lists
from app.services.explain_screen import FEEDBACK_OPEN, FEEDBACK_TITLE
from app.services.explain_screen import OPEN as EXPLAIN_OPEN
from app.services.favorites import Screen
from app.services.numbers import SHOW_NUMBERS, shows_numbers
from app.services.routines import CALLBACK_PREFIX as ROUTINES_PREFIX
from app.services.routines import active as active_routines

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "set:"
SHOW_COSTS = "show_costs"  # the 💸 line under each analysis
TIMEZONE = "timezone"  # an IANA name; days in 📊 and dates in ⚖️ follow it
WEEK_PUSH = "week_push"  # False: no summary on Sunday; on unless turned off
WEEK_PUSH_SENT = "week_push_last"  # the Monday of the last summary sent
DRAFT_MODE = "draft_mode"  # True: food waits for «✅ Записать»; off unless turned on
_COSTS_CB = CALLBACK_PREFIX + "costs"
_WEEK_CB = CALLBACK_PREFIX + "week"
_DRAFT_CB = CALLBACK_PREFIX + "draft"
_NUMBERS_CB = CALLBACK_PREFIX + "num"
_ZONES_CB = CALLBACK_PREFIX + "tz"
_PICK = _ZONES_CB + ":"  # + the zone's IANA name
_OFFSETS = CALLBACK_PREFIX + "tzo:"  # + the page
_DIET_CB = CALLBACK_PREFIX + "diet"
_DIET_PICK = _DIET_CB + ":"  # + the answer

CITIES = (
    ("Тбилиси", "Asia/Tbilisi"),
    ("Ереван", "Asia/Yerevan"),
    ("Баку", "Asia/Baku"),
    ("Москва", "Europe/Moscow"),
    ("Стамбул", "Europe/Istanbul"),
    ("Киев", "Europe/Kyiv"),
    ("Минск", "Europe/Minsk"),
    ("Берлин", "Europe/Berlin"),
    ("Лондон", "Europe/London"),
)
_CITY = {zone: city for city, zone in CITIES}
# Whole-hour offsets UTC−12 … UTC+14. The Etc names count the other way round:
# Etc/GMT-4 is UTC+4.
OFFSETS = tuple(
    (hours, "UTC" if hours == 0 else f"Etc/GMT{'-' if hours > 0 else '+'}{abs(hours)}")
    for hours in range(-12, 15)
)
_OFFSET_ZONES = {zone for _hours, zone in OFFSETS}
PAGE = 9  # offsets per page, three rows of three


def _user(db, telegram_id: int) -> User | None:
    return db.query(User).filter(User.telegram_id == telegram_id).first()


def _valid(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def zone_name(user: User | None) -> str:
    """The user's time zone, else the bot's ``DEFAULT_TIMEZONE``."""
    name = (user.settings or {}).get(TIMEZONE) if user else None
    if isinstance(name, str) and _valid(name):
        return name
    if name:
        logger.warning("settings: unknown time zone %r, using the default", name)
    return settings.DEFAULT_TIMEZONE


def user_zone(user: User | None) -> ZoneInfo:
    return ZoneInfo(zone_name(user))


def week_push_on(user: User | None) -> bool:
    return (user.settings or {}).get(WEEK_PUSH) is not False if user else True


def draft_on(user: User | None) -> bool:
    return bool((user.settings or {}).get(DRAFT_MODE)) if user else False


def shows_costs(db, telegram_id: int) -> bool:
    user = _user(db, telegram_id)
    return bool(user and (user.settings or {}).get(SHOW_COSTS))


def _offset(name: str, now: datetime) -> str:
    minutes = int(now.astimezone(ZoneInfo(name)).utcoffset().total_seconds() // 60)
    hours, rest = divmod(abs(minutes), 60)
    sign = "+" if minutes >= 0 else "−"
    return f"UTC{sign}{hours}" + (f":{rest:02d}" if rest else "")


def zone_label(name: str, now: datetime) -> str:
    """«Тбилиси (UTC+4)»; a bare offset is just «UTC+4»."""
    offset = _offset(name, now)
    if name in _OFFSET_ZONES:
        return offset
    return f"{html.escape(_CITY.get(name, name))} ({offset})"


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)


# --- screens ----------------------------------------------------------------


def screen(
    on: bool,
    zone: str,
    now: datetime,
    notice: str | None = None,
    numbers: bool = False,
    routines: int = 0,
    week_push: bool = True,
    reliable: int = 0,
    never: int = 0,
    draft: bool = False,
    eats: str | None = None,
) -> Screen:
    lines = [f"<i>{notice}</i>", ""] if notice else []
    # One short line each, in two groups. A switch's state is on its button; what a
    # setting means is said when it is flipped, not on every visit.
    lines += [
        "<b>⚙️ Настройки</b>",
        "",
        f"🕐 Часовой пояс: {zone_label(zone, now)}",
        _diet_line(eats),
        "🗓 Итог недели: " + ("вс, 22:00" if week_push else "не присылаю"),
        "",
        f"💊 Рутины: {routines or 'пока нет'}",
        f"🧺 Надёжная еда: {reliable or 'пока нет'}",
        f"✋ Не предлагать: {never or 'пока нет'}",
    ]
    # Eleven buttons in five rows: the one row of three takes the shortest labels, and
    # «🕐 Пояс» is short for the line above. Longer ones Telegram cuts on a phone.
    rows = [
        [
            _btn(f"🔢 Цифры сразу: {'вкл' if numbers else 'выкл'}", _NUMBERS_CB),
            _btn(f"📋 Черновик: {'вкл' if draft else 'выкл'}", _DRAFT_CB),
        ],
        [
            _btn(f"🗓 Итог недели: {'вкл' if week_push else 'выкл'}", _WEEK_CB),
            _btn(f"💸 Стоимость: {'вкл' if on else 'выкл'}", _COSTS_CB),
        ],
        [
            _btn("🧺 Надёжная еда", food_lists.RELIABLE_OPEN),
            _btn("✋ Не предлагать", food_lists.NEVER_OPEN),
        ],
        [
            _btn("🕐 Пояс", _ZONES_CB),
            _btn("💊 Рутины", ROUTINES_PREFIX + "open"),
            _btn(FEEDBACK_TITLE, FEEDBACK_OPEN),
        ],
        [_btn(diet.TITLE, _DIET_CB), _btn("ℹ️ Как это работает", EXPLAIN_OPEN)],
    ]
    return Screen("\n".join(lines), InlineKeyboardMarkup(rows))


def _diet_line(eats: str | None) -> str:
    return f"{diet.TITLE}: {diet.CHOICES[eats] if eats else 'не выбрано'}"


def diet_row(prefix: str, eats: str | None) -> list:
    """The three answers as one row of buttons, ``prefix`` + the answer as their data:
    the picker here, and the first-run screen with its own prefix."""
    return [
        _btn(("✓ " if value == eats else "") + label, prefix + value)
        for value, label in diet.CHOICES.items()
    ]


def diet_screen(eats: str | None) -> Screen:
    text = f"<b>{diet.TITLE}</b>\n\n{diet.QUESTION}"
    rows = [diet_row(_DIET_PICK, eats), [_btn("⬅️ Настройки", CALLBACK_PREFIX + "open")]]
    return Screen(text, InlineKeyboardMarkup(rows))


def _now_line(zone: str, now: datetime) -> str:
    there = now.astimezone(ZoneInfo(zone)).strftime("%H:%M")
    return f"Сейчас: {zone_label(zone, now)}, там {there}."


def zones_screen(zone: str, now: datetime) -> Screen:
    lines = [
        "<b>🕐 Часовой пояс</b>",
        "",
        _now_line(zone, now),
        "По нему 📊 Статистика делит дни, а ⚖️ Вес ставит даты.",
    ]
    cities = [
        _btn(("✓ " if name == zone else "") + city, _PICK + name)
        for city, name in CITIES
    ]
    rows = [cities[i : i + 3] for i in range(0, len(cities), 3)]
    rows.append([_btn("🌐 Другой: UTC±", f"{_OFFSETS}{_page_of(zone)}")])
    rows.append([_btn("⬅️ Настройки", CALLBACK_PREFIX + "open")])
    return Screen("\n".join(lines), InlineKeyboardMarkup(rows))


def _pages() -> int:
    return -(-len(OFFSETS) // PAGE)


def _page_of(zone: str) -> int:
    """The page that holds the chosen offset, else the one with UTC−3 … UTC+5."""
    names = [name for _hours, name in OFFSETS]
    index = names.index(zone) if zone in names else names.index("UTC")
    return index // PAGE


def offsets_screen(zone: str, now: datetime, page: int) -> Screen:
    lines = [
        "<b>🕐 Часовой пояс: UTC±</b>",
        "",
        _now_line(zone, now),
        "Летнее время здесь не учитывается — если оно у тебя есть, выбери город.",
    ]
    chunk = OFFSETS[page * PAGE : (page + 1) * PAGE]
    buttons = [
        _btn(("✓ " if name == zone else "") + _offset(name, now), _PICK + name)
        for _hours, name in chunk
    ]
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    last = _pages() - 1
    rows.append(
        [
            _btn("‹", f"{_OFFSETS}{max(page - 1, 0)}"),
            _btn(f"{page + 1}/{last + 1}", f"{_OFFSETS}{page}"),
            _btn("›", f"{_OFFSETS}{min(page + 1, last)}"),
        ]
    )
    rows.append([_btn("⬅️ Часовой пояс", _ZONES_CB)])
    return Screen("\n".join(lines), InlineKeyboardMarkup(rows))


# --- operations -------------------------------------------------------------


def open_view(
    db, telegram_id: int, notice: str | None = None, now: datetime | None = None
) -> Screen:
    now = now or datetime.now(UTC)
    user = _user(db, telegram_id)
    if user is not None:
        food_lists.seed(db, user, now)
    on = bool(user and (user.settings or {}).get(SHOW_COSTS))
    reliable, never = food_lists.counts(user)
    return screen(
        on,
        zone_name(user),
        now,
        notice,
        shows_numbers(user),
        len(active_routines(user)),
        week_push_on(user),
        reliable,
        never,
        draft_on(user),
        diet.chosen((user.settings or {}) if user else {}),
    )


def _save(db, telegram_id: int, **values) -> None:
    user = _user(db, telegram_id)
    if user is None:
        user = User(telegram_id=telegram_id)
        db.add(user)
    # A new dict: SQLAlchemy does not see a JSON value changed in place.
    user.settings = {**(user.settings or {}), **values}
    db.commit()


def save_diet(db, telegram_id: int, value: str) -> bool:
    """The answer to «Что обычно ешь?», from any screen; False for an unknown one."""
    if value not in diet.CHOICES:
        return False
    _save(db, telegram_id, **{diet.KEY: value})
    return True


def handle_callback(
    db, telegram_id: int, data: str, now: datetime | None = None
) -> Screen:
    now = now or datetime.now(UTC)
    if data == _COSTS_CB:
        on = not shows_costs(db, telegram_id)
        _save(db, telegram_id, **{SHOW_COSTS: on})
        notice = (
            "✓ Стоимость запросов включена" if on else "✓ Стоимость запросов скрыта"
        )
        return open_view(db, telegram_id, notice, now)
    if data == _NUMBERS_CB:
        on = not shows_numbers(_user(db, telegram_id))
        _save(db, telegram_id, **{SHOW_NUMBERS: on})
        notice = "✓ Цифры видны сразу" if on else "✓ Цифры теперь за кнопкой"
        return open_view(db, telegram_id, notice, now)
    if data == _WEEK_CB:
        on = not week_push_on(_user(db, telegram_id))
        _save(db, telegram_id, **{WEEK_PUSH: on})
        notice = "✓ Итог недели включён" if on else "✓ Итог недели выключен"
        return open_view(db, telegram_id, notice, now)
    if data == _DRAFT_CB:
        on = not draft_on(_user(db, telegram_id))
        _save(db, telegram_id, **{DRAFT_MODE: on})
        notice = (
            "✓ Черновик перед записью: вкл. Еда попадёт в дневник после «✅ Записать»."
            if on
            else "✓ Черновик перед записью: выкл. Еда записывается сразу."
        )
        return open_view(db, telegram_id, notice, now)
    if data == _DIET_CB:
        user = _user(db, telegram_id)
        return diet_screen(diet.chosen((user.settings or {}) if user else {}))
    if data.startswith(_DIET_PICK):
        value = data[len(_DIET_PICK) :]
        if save_diet(db, telegram_id, value):
            notice = f"✓ Обычная еда: {diet.CHOICES[value]}"
            return open_view(db, telegram_id, notice, now)
    zone = zone_name(_user(db, telegram_id))
    if data == _ZONES_CB:
        return zones_screen(zone, now)
    if data.startswith(_PICK):
        name = data[len(_PICK) :]
        if name in _CITY or name in _OFFSET_ZONES:
            _save(db, telegram_id, **{TIMEZONE: name})
            notice = f"✓ Часовой пояс: {zone_label(name, now)}"
            return open_view(db, telegram_id, notice, now)
    if data.startswith(_OFFSETS):
        page = data[len(_OFFSETS) :]
        if page.isdigit() and int(page) < _pages():
            return offsets_screen(zone, now, int(page))
    if data != CALLBACK_PREFIX + "open":
        logger.warning("settings: unknown callback data %r", data)
    return open_view(db, telegram_id, now=now)
