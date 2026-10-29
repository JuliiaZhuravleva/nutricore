"""⚙️ Настройки: what the bot runs with, and the switches the user can flip.

    ⚙️ Настройки → <b>⚙️ Настройки</b> — time zone, numbers, the cost line
    [🕐 Часовой пояс] [🔢 Цифры сразу: выкл] [💸 Стоимость запросов: выкл]
    💸 tap → <i>✓ Стоимость запросов включена</i> on top, the same screen below
    🔢 tap → <i>✓ Цифры видны сразу</i>: calories and macros on every screen
    🕐 tap → <b>🕐 Часовой пояс</b>: nine cities, [🌐 Другой: UTC±], [⬅️ Настройки]
      a city → <i>✓ Часовой пояс: Москва (UTC+3)</i> on top of ⚙️ Настройки

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
from app.services.favorites import Screen
from app.services.numbers import SHOW_NUMBERS, shows_numbers

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "set:"
SHOW_COSTS = "show_costs"  # the 💸 line under each analysis
TIMEZONE = "timezone"  # an IANA name; days in 📊 and dates in ⚖️ follow it
_COSTS_CB = CALLBACK_PREFIX + "costs"
_NUMBERS_CB = CALLBACK_PREFIX + "num"
_ZONES_CB = CALLBACK_PREFIX + "tz"
_PICK = _ZONES_CB + ":"  # + the zone's IANA name
_OFFSETS = CALLBACK_PREFIX + "tzo:"  # + the page

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
) -> Screen:
    lines = [f"<i>{notice}</i>", ""] if notice else []
    lines += [
        "<b>⚙️ Настройки</b>",
        "",
        f"🕐 Часовой пояс: {zone_label(zone, now)} — по нему "
        "📊 Статистика делит дни.",
        "🔢 Калории и БЖУ: "
        + ("видны сразу." if numbers else "под кнопкой «🔢 Цифры» на экранах."),
        f"💸 Стоимость запросов: {'показываю' if on else 'не показываю'} "
        "под каждым разбором еды.",
    ]
    rows = [
        [_btn("🕐 Часовой пояс", _ZONES_CB)],
        [_btn(f"🔢 Цифры сразу: {'вкл' if numbers else 'выкл'}", _NUMBERS_CB)],
        [_btn(f"💸 Стоимость запросов: {'вкл' if on else 'выкл'}", _COSTS_CB)],
    ]
    return Screen("\n".join(lines), InlineKeyboardMarkup(rows))


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
    user = _user(db, telegram_id)
    on = bool(user and (user.settings or {}).get(SHOW_COSTS))
    return screen(
        on, zone_name(user), now or datetime.now(UTC), notice, shows_numbers(user)
    )


def _save(db, telegram_id: int, **values) -> None:
    user = _user(db, telegram_id)
    if user is None:
        user = User(telegram_id=telegram_id)
        db.add(user)
    # A new dict: SQLAlchemy does not see a JSON value changed in place.
    user.settings = {**(user.settings or {}), **values}
    db.commit()


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
