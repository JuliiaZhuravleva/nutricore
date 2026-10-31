"""A meal's own card, prefix ``ml:`` (stage 5, slice N).

    ✏️ Исправить, 🍽 Приём, a clock chip on Приёмы → <b>🍽 Приём, пн 26 октября, 13:20</b>
    [рис] [курица] [овощи]         a dish (N2): quantity, grams, replace, remove, ⭐
    [➕ Блюдо] [🕐 Время] [🗑 Удалить]
    [🔢 Цифры] [Ещё ›]
    [⬅️ К ответу бота]             by origin, kept through every nested tap

The callback data carries the whole step, so a tap works in any conversation state
and after a restart. What is typed goes through one slot in ``User.settings``, which
lives in the database for the same reason. Foreign or malformed data gets today's card
and a warning. Telegram-free apart from the keyboard, like ``favorites``. The screens
are in docs/stages/05-gentle-review-screens.md, sections 2 and 4.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from datetime import time as clock_time
from datetime import timedelta
from typing import Callable, Optional

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.crud.crud_favorite_meal import crud_favorite_meal
from app.crud.crud_meal_item import crud_meal_item
from app.models.meal import Meal
from app.models.meal_item import MealItem
from app.models.personal_food import PersonalFood
from app.models.record_event import RecordEvent
from app.models.user import User
from app.services import favorites, food_groups, meal_items, next_step, stats_screen
from app.services.day_card import APPROX, DAY_ONLY
from app.services.day_card import clock as _clock
from app.services.day_card import local_time, plain_time
from app.services.favorites import Screen
from app.services.meal_draft import num
from app.services.numbers import shows_numbers, toggle_label
from app.services.settings_screen import user_zone

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "ml:"
NOP = "ml:nop"
ASK = "ask"  # in ``User.settings``: the one typed slot
ASK_TTL = timedelta(minutes=10)
GRAMS, DISH_ADD, DISH_REPLACE = "grams", "dish_add", "dish_replace"
TIME = "time"
EXACT = "exact"
# A card opened on the bot's line: its time screen goes back to the card, while the
# line's own 🕐, whose data is also ``r``, goes back to the line.
FROM_CARD = "c"
GRAMS_MIN, GRAMS_MAX = 1.0, 5000.0
NAMES_MAX = 300
LINE_NAME_MAX = 80
CHIP_MAX = 11
PICK_LABEL_MAX = 20
PICK_PAGE = 4
CHIPS_ROW = 3
DAY_CHIPS = 9
_INT_MAX = 2**31 - 1  # what an id column holds; a bigger one is not a row

STALE_MEAL = "<i>Этого приёма уже нет, вот сегодняшний день.</i>"
STALE_DISH = "<i>Этого блюда в приёме уже нет.</i>"
STALE_PICK = "<i>Этого блюда уже нет в списке.</i>"
UNDONE = "<i>✓ Возвращено</i>"
PLATE_LINE = "Цифры посчитаны на всю тарелку."
PLATE_KEPT = "Цифры посчитаны на всю тарелку, поэтому не менялись."
HINT = "<i>Нажми на блюдо, чтобы поправить.</i>"
HOW = (
    "Считается от первой записи: ½ — вдвое меньше, ×1 — столько же, ×2 — вдвое больше."
)
UNDO_LABEL = "↩️ Вернуть"
NOTHING_SAME = "<i>Уже так</i>"
TIME_FUTURE = "<i>Это время ещё не наступило. Напиши другое.</i>"
_PRECISION_OF = {"e": EXACT, "a": APPROX, "d": DAY_ONLY}

_EXACT = frozenset({"barcode", "label", "user"})
_QUANTITY = {"h": 0.5, "1": 1.0, "2": 2.0}
_PORTION = {0.5: "½ порции", 2.0: "2 порции"}
_DONE_NOTICE = {
    "h": "<i>✓ ½ порции</i>",
    "1": "<i>✓ Как записано</i>",
    "2": "<i>✓ 2 порции</i>",
}
# The shipped badges, as a line of their own.
_SOURCE_LINE = {
    "saved_rag": "⭐ Из твоей базы",
    "label_ocr": "🏷️ С этикетки (проверь)",
    "name_web": "🌐 Нашли в сети (проверь)",
    "name_off": "🔍 Нашли в базе (проверь)",
}
_DAY_BACK = {
    stats_screen.TODAY: "⬅️ Назад: сегодня",
    stats_screen.YESTERDAY: "⬅️ Назад: вчера",
    stats_screen.WEEK: "⬅️ Назад: 7 дней",
}
_GRAMS_TEXT = re.compile(r"\s*(\d{1,4}(?:[.,]\d{1,2})?)\s*[гГ]?\s*")
_TIME_TEXT = re.compile(r"\s*(вчера\s+)?(\d{1,2})[:.](\d{2})\s*", re.IGNORECASE)


@dataclass
class _Tap:
    db: object
    user: Optional[User]
    telegram_id: int
    now: datetime
    message_id: Optional[int] = None

    @property
    def zone(self):
        return user_zone(self.user)

    @property
    def simple(self) -> bool:
        settings = (self.user.settings or {}) if self.user else {}
        return next_step.energy(settings) == next_step.SIMPLE


class _Answer(Exception):
    """The screen a stale, foreign or missing row answers with."""

    def __init__(self, screen: Screen):
        super().__init__("answered")
        self.screen = screen


@dataclass(frozen=True)
class Typed:
    """The answer to a typed slot, and the message whose keyboard it retires."""

    screen: Screen
    message_id: Optional[int] = None


# --- words ------------------------------------------------------------------


def _esc(text) -> str:
    return html.escape(str(text), quote=False)


def _with_notice(screen: Screen, notice: Optional[str]) -> Screen:
    if not notice:
        return screen
    return replace(screen, text=f"{notice}\n\n{screen.text}")


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _names(names: list) -> str:
    """рис, курица и овощи."""
    if len(names) < 2:
        return "".join(names)
    return f"{', '.join(names[:-1])} и {names[-1]}"


def meal_day(meal: Meal, user: Optional[User]) -> date:
    return local_time(meal.meal_time, user_zone(user)).date()


def _when(meal: Meal, zone) -> str:
    day = local_time(meal.meal_time, zone).date()
    return f"{stats_screen._card_title(day)}, {_clock(meal, zone)}"


def _sent_line(meal: Meal, zone) -> Optional[str]:
    if meal.sent_at is None:
        return None
    sent = local_time(meal.sent_at, zone)
    if abs(sent - local_time(meal.meal_time, zone)) <= timedelta(minutes=15):
        return None
    return f"<i>Отправлено: {stats_screen._card_title(sent.date())}, {plain_time(sent)}.</i>"


def _totals(meal: Meal) -> bool:
    return any(getattr(meal, key) is not None for key in meal_items.FIELDS)


def _source_line(meal: Meal) -> Optional[str]:
    tag = meal_items.numbers_tag(meal)
    if tag:
        return f"<i>{_capital(tag)}</i>"
    source = meal.resolution_source
    if source == "barcode_off":
        whole = all(getattr(meal, key) is not None for key in meal_items.FIELDS)
        told = "точно" if whole else "неполные данные"
        return f"<i>📦 По штрих-коду ({told})</i>"
    if source in _SOURCE_LINE:
        return f"<i>{_SOURCE_LINE[source]}</i>"
    if source == "vision" or meal.photos:
        return "<i>📷 Оценка по фото</i>"
    return None


def _words(item: MealItem) -> list:
    """What a line adds to the name: the person's words, then the quantity.

    ×1 writes nothing, and model grams wait inside open numbers.
    """
    parts = [item.amount_text] if item.amount_text else []
    if item.grams:
        parts.append(f"{num(item.grams)} г")
    elif item.factor in _PORTION:
        parts.append(_PORTION[item.factor])
    return parts


def _dish_line(item: MealItem) -> str:
    line = _esc(item.name)
    if item.amount_text:
        line += f", {_esc(item.amount_text)}"
    rest = _words(item)[1 if item.amount_text else 0 :]
    return " · ".join([line, *map(_esc, rest)])


def _meal_names(db, meal: Meal) -> list:
    names = [item.name for item in crud_meal_item.live(db, meal.id)]
    if names:
        return names
    return [meal.description] if meal.description else []


def _what(db, meal: Meal) -> str:
    return _esc(_cut(_names(_meal_names(db, meal)), NAMES_MAX))


# --- numbers ----------------------------------------------------------------


def _mark(exact: bool) -> str:
    return "" if exact else "≈ "


def _sum_line(values: dict, exact: bool) -> Optional[str]:
    parts = []
    if values.get("calories") is not None:
        parts.append(f"🔥 {_mark(exact)}{num(values['calories'], 0)} ккал")
    for label, key in (("Б", "proteins"), ("Ж", "fats"), ("У", "carbohydrates")):
        if values.get(key) is not None:
            parts.append(f"{label} {num(values[key], 0)}")
    return " · ".join(parts) or None


def _is_exact(item: MealItem) -> bool:
    return item.numbers_source in _EXACT


def _grams_part(item: MealItem) -> Optional[str]:
    grams = meal_items._item_grams(item)
    if not grams:
        return None
    return f"{_mark(bool(item.grams) or _is_exact(item))}{num(grams, 0)} г"


def _counted(item: MealItem) -> bool:
    return item.numbers_source != "plate_share" and meal_items._has_numbers(item)


def _blockquote(lines: list) -> Optional[str]:
    if not lines:
        return None
    return "<blockquote expandable>" + "\n".join(lines) + "</blockquote>"


def _meal_block(meal: Meal, items: list) -> Optional[str]:
    counted = [item for item in items if _counted(item)]
    exact = (
        bool(counted)
        and meal.numbers_basis == "items"
        and all(_is_exact(item) for item in counted)
    )
    totals = {key: getattr(meal, key) for key in meal_items.FIELDS}
    lines = [line for line in (_sum_line(totals, exact),) if line]
    for item in counted:
        parts = [_esc(item.name)]
        grams = _grams_part(item)
        if grams:
            parts.append(grams)
        calories = meal_items._shown(item)["calories"]
        if calories is not None:
            parts.append(f"{_mark(_is_exact(item))}{num(calories, 0)} ккал")
        lines.append(" · ".join(parts))
    missing = sum(1 for item in items if item.numbers_source == "none")
    if missing:
        lines.append(f"Не посчитано блюд: {missing}")
    return _blockquote(lines)


def _item_block(item: MealItem) -> Optional[str]:
    if not _counted(item):
        return None
    lines = [_sum_line(meal_items._shown(item), _is_exact(item)), _grams_part(item)]
    return _blockquote([line for line in lines if line])


def _dish_tag(db, item: MealItem, meal: Meal) -> Optional[str]:
    source = item.numbers_source
    if source == "barcode":
        tag = "по штрих-коду (точно)"
    elif source == "label":
        tag = "с этикетки"
    elif source == "user":
        tag = "указано вручную"
    elif source == "dish":
        dish = (
            db.get(PersonalFood, item.personal_food_id)
            if item.personal_food_id
            else None
        )
        sure = dish is not None and (
            dish.numbers_by_user or dish.numbers_quality in meal_items._VERIFIED
        )
        tag = meal_items.TAG_DISH if sure else meal_items.TAG_DISH_UNVERIFIED
    elif source == "model":
        tag = "оценка по фото" if meal.photos else "оценка по тексту"
    else:
        return None
    return f"<i>{_capital(tag)}</i>"


# --- buttons ----------------------------------------------------------------


def _button(label: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data=data)


def _undo_button(event_id: int, back: str) -> InlineKeyboardButton:
    return _button(UNDO_LABEL, f"ml:u:{event_id}:{back}")


def _back_row(t: _Tap, meal: Meal, back: str) -> list:
    if back == "r":
        return [_button("⬅️ К ответу бота", f"ml:r:{meal.id}")]
    if back.startswith("h"):
        return [_button("⬅️ Когда было", f"dh:h:{back[1:]}:0")]
    day = stats_screen._day_code(meal_day(meal, t.user))
    return [_button("⬅️ Приёмы дня", f"ml:dl:{day}:{back}")]


def _to_card(meal_id: int, back: str) -> list:
    return [_button("⬅️ К приёму", f"ml:o:{meal_id}:{back}")]


def _to_dish(item_id: int, back: str) -> list:
    return [_button("⬅️ К блюду в приёме", f"ml:i:{item_id}:{back}")]


def _dish_chips(meal: Meal, items: list, back: str, page: int) -> list:
    """Two chips and ▶️ past three dishes: the text lists them all anyway."""
    if not items:
        return []
    shown, more = items, None
    if len(items) > CHIPS_ROW:
        pages = [items[at : at + 2] for at in range(0, len(items), 2)]
        page %= len(pages)
        shown = pages[page]
        more = _button("▶️", f"ml:op:{meal.id}:{(page + 1) % len(pages)}:{back}")
    row = [
        _button(_cut(item.name, CHIP_MAX + 1), f"ml:i:{item.id}:{back}")
        for item in shown
    ]
    return [[*row, more] if more else row]


def _meal_favourite(db, meal: Meal):
    name = _favourite_name(meal).casefold()
    for fav in crud_favorite_meal.list_active(db, meal.user_id):
        if fav.source_meal_id == meal.id or fav.name.casefold() == name:
            return fav
    return None


def _favourite_name(meal: Meal) -> str:
    return favorites.favorite_name(
        caption=None,
        description=meal.description,
        foods=(
            (meal.nutrients or {}).get("foods")
            if isinstance(meal.nutrients, dict)
            else None
        ),
    )


def _meal_star(db, meal: Meal, back: str) -> InlineKeyboardButton:
    on = _meal_favourite(db, meal) is not None
    label = "✓ ⭐ Весь приём" if on else "⭐ Весь приём"
    return _button(label, f"ml:fm:{meal.id}:{0 if on else 1}:{back}")


# --- N1, the card -----------------------------------------------------------


def _unread(meal: Meal, items: list) -> bool:
    return bool(meal.photos) and not (items or meal.description or _totals(meal))


def _card(
    t: _Tap,
    meal: Meal,
    back: str,
    *,
    notice: Optional[str] = None,
    undo: Optional[int] = None,
    page: int = 0,
    opened: bool = False,
) -> Screen:
    items = crud_meal_item.live(t.db, meal.id)
    word = meal.meal_type or "Приём"
    lines = [f"<b>🍽 {_esc(word)}, {_when(meal, t.zone)}</b>"]
    shown = shows_numbers(t.user)
    open_now = shown or opened
    tools = [
        _button("🕐 Время", f"ml:t:{meal.id}:{FROM_CARD if back == 'r' else back}"),
        _button("🗑 Удалить", f"ml:d:{meal.id}:{back}"),
    ]
    add = _button("➕ Блюдо", f"ml:pk:{meal.id}:a:{back}:0")
    numbers = _button(
        toggle_label(open_now), f"ml:n:{meal.id}:{0 if open_now else 1}:{back}"
    )
    undo_button = _undo_button(undo, back) if undo else None

    if _unread(meal, items):
        lines += [
            "Фото сохранено, распознать еду пока не вышло.",
            "В дневнике запись с этим фото.",
        ]
        rows = [tools]
        if undo_button:
            rows.append([undo_button])
    elif not items:
        rows = _legacy_card(t, meal, back, lines, add, tools, numbers, undo_button)
    else:
        lines += map(_dish_line, items)
        if not t.simple:
            lines += _card_notes(meal, t.zone)
        rows = [*_dish_chips(meal, items, back, page), [add, *tools]]
        if t.simple:
            if undo_button:
                rows.append([undo_button])
        else:
            third = [] if shown else [numbers]
            third.append(_button("Ещё ›", f"ml:mo:{meal.id}:{back}"))
            if undo_button:
                third.append(undo_button)
            rows.append(third)
    if open_now and not _unread(meal, items):
        block = _meal_block(meal, items)
        if block:
            lines.append(block)
    rows.append(_back_row(t, meal, back))
    screen = Screen("\n".join(lines), InlineKeyboardMarkup(rows))
    return _with_notice(screen, notice)


def _card_notes(meal: Meal, zone) -> list:
    notes = [_source_line(meal), _sent_line(meal, zone)]
    if meal.numbers_basis == "plate":
        notes.append(PLATE_LINE)
    notes.append(HINT)
    return [note for note in notes if note]


def _legacy_card(
    t: _Tap, meal: Meal, back: str, lines: list, add, tools: list, numbers, undo_button
) -> list:
    """A meal from before items: nothing is recomputed, and ➕ opens its first line."""
    if meal.description:
        lines.append(_esc(meal.description))
    has_totals = _totals(meal)
    rows = [[add, *tools]]
    if t.simple:
        if undo_button:
            rows.append([undo_button])
        return rows
    if has_totals:
        lines.append("Цифры на всю запись, не по блюдам.")
    second = [_meal_star(t.db, meal, back)]
    if has_totals and not shows_numbers(t.user):
        second.append(numbers)
    if undo_button:
        second.append(undo_button)
    rows.append(second)
    return rows


# --- N2, the dish -----------------------------------------------------------


def _quantity_row(item: MealItem, back: str) -> list:
    current = 1.0 if item.factor is None else item.factor
    row = []
    for verb, label in (("h", "½"), ("1", "×1"), ("2", "×2")):
        on = item.grams is None and current == _QUANTITY[verb]
        row.append(
            _button(("✓ " if on else "") + label, f"ml:q:{item.id}:{verb}:{back}")
        )
    return row


def _dish(
    t: _Tap,
    item: MealItem,
    meal: Meal,
    back: str,
    *,
    notice: Optional[str] = None,
    undo: Optional[int] = None,
    opened: bool = False,
) -> Screen:
    counted = _counted(item)
    shown = shows_numbers(t.user)
    open_now = shown or opened
    where = " · ".join([f"В приёме {_when(meal, t.zone)}", *map(_esc, _words(item))])
    lines = [f"<b>🍲 {_esc(_capital(item.name))}</b>", where]
    if counted:
        tag = None if t.simple else _dish_tag(t.db, item, meal)
        if tag:
            lines.append(tag)
        lines.append(HOW)
    elif item.numbers_source == "plate_share":
        lines.append(PLATE_LINE)
    else:
        lines.append("Цифры не посчитаны.")
    if open_now and counted:
        block = _item_block(item)
        if block:
            lines.append(block)

    remove = _button("🗑 Из приёма", f"ml:x:{item.id}:{back}")
    undo_button = _undo_button(undo, back) if undo else None
    if t.simple:
        rows = [_quantity_row(item, back)] if counted else []
        rows.append([remove])
        if undo_button:
            rows.append([undo_button])
    else:
        rows = [[_button("🔄 Заменить", f"ml:pk:{item.id}:r:{back}:0"), remove]]
        if counted:
            rows.append(_quantity_row(item, back))
        on = (
            crud_favorite_meal.find_active_by_name(t.db, meal.user_id, item.name)
            is not None
        )
        rows.append(
            [
                _button("⚖️ Граммы", f"ml:g:{item.id}:{back}"),
                _button(
                    "✓ ⭐ В избранном" if on else "⭐ В избранное",
                    f"ml:fi:{item.id}:{0 if on else 1}:{back}",
                ),
            ]
        )
        fourth = []
        if counted and not shown:
            fourth.append(
                _button(
                    toggle_label(open_now),
                    f"ml:ni:{item.id}:{0 if open_now else 1}:{back}",
                )
            )
        if undo_button:
            fourth.append(undo_button)
        if fourth:
            rows.append(fourth)
    rows.append(_to_card(meal.id, back))
    screen = Screen("\n".join(lines), InlineKeyboardMarkup(rows))
    return _with_notice(screen, notice)


# --- the rows a verb stands on ----------------------------------------------


def _row(db, model, row_id: int):
    return None if row_id > _INT_MAX else db.get(model, row_id)


def _today(t: _Tap, notice: Optional[str] = None) -> Screen:
    card = stats_screen.open_view(
        t.db, t.telegram_id, stats_screen.TODAY, t.now, opened=False
    )
    return _with_notice(card, notice)


def _unknown(t: _Tap, data: str) -> Screen:
    logger.warning("meal card: unknown or foreign data %r", data)
    return _today(t)


def _stale_meal(t: _Tap) -> Screen:
    return _today(t, STALE_MEAL)


def _own_meal(t: _Tap, meal_id: int, data: str) -> Meal:
    meal = _row(t.db, Meal, meal_id)
    if meal is None or t.user is None or meal.user_id != t.user.id:
        raise _Answer(_unknown(t, data))
    if meal.deleted_at is not None:
        raise _Answer(_stale_meal(t))
    return meal


def _own_item(t: _Tap, item_id: int, back: str, data: str) -> tuple:
    item = _row(t.db, MealItem, item_id)
    if item is None:
        raise _Answer(_unknown(t, data))
    meal = _own_meal(t, item.meal_id, data)
    if item.removed_at is not None:
        raise _Answer(_card(t, meal, back, notice=STALE_DISH))
    return item, meal


def _change_stale(t: _Tap, change: meal_items.Change, meal: Meal, back: str) -> Screen:
    """A row that went between the read and the write."""
    if change.meal is None or change.meal.deleted_at is not None:
        return _stale_meal(t)
    return _card(t, meal, back, notice=STALE_DISH)


# --- the typed slot ---------------------------------------------------------


def _user(db, telegram_id: int) -> Optional[User]:
    return db.query(User).filter(User.telegram_id == telegram_id).first()


def _open_slot(t: _Tap, kind: str, back: str, *, meal_id: int, item_id=None) -> None:
    t.user.settings = {
        **(t.user.settings or {}),
        ASK: {
            "kind": kind,
            "meal_id": meal_id,
            "item_id": item_id,
            "food_id": None,
            "day": None,
            "message_id": t.message_id,
            "at": t.now.isoformat(),
            "back": back,
        },
    }
    t.db.commit()


def _close_slot(db, user: Optional[User]) -> Optional[dict]:
    """What was asked. Nothing is written when nothing was."""
    ask = (user.settings or {}).get(ASK) if user else None
    if ask is None:
        return None
    user.settings = {k: v for k, v in user.settings.items() if k != ASK}
    db.commit()
    return ask


def has_slot(db, telegram_id: int) -> bool:
    user = _user(db, telegram_id)
    return bool(user and (user.settings or {}).get(ASK))


def leave_slot(db, telegram_id: int) -> None:
    _close_slot(db, _user(db, telegram_id))


def parse_grams(text: Optional[str]) -> Optional[float]:
    """A number from 1 to 5000 with an optional «г»; anything else is not an answer."""
    match = _GRAMS_TEXT.fullmatch(text or "")
    if match is None:
        return None
    grams = float(match.group(1).replace(",", "."))
    return grams if GRAMS_MIN <= grams <= GRAMS_MAX else None


def parse_time(text: Optional[str]) -> Optional[tuple]:
    """(yesterday?, clock) from «13:20», «13.20», «вчера 23:10»; anything else is not
    an answer."""
    match = _TIME_TEXT.fullmatch(text or "")
    if match is None:
        return None
    hour, minute = int(match.group(2)), int(match.group(3))
    if hour > 23 or minute > 59:
        return None
    return bool(match.group(1)), clock_time(hour, minute)


def slot_sent(db, telegram_id: int, message_id: int) -> None:
    """The answer holds the live keyboard now, so the next answer retires that one."""
    user = _user(db, telegram_id)
    ask = (user.settings or {}).get(ASK) if user else None
    if ask is None:
        return
    user.settings = {**user.settings, ASK: {**ask, "message_id": message_id}}
    db.commit()


def _expired(ask: dict, now: datetime) -> bool:
    try:
        asked = datetime.fromisoformat(ask["at"])
    except (KeyError, TypeError, ValueError):
        return True
    if asked.tzinfo is None:
        asked = asked.replace(tzinfo=UTC)
    return now - asked > ASK_TTL


def take_typed(
    db, telegram_id: int, text: Optional[str], now: Optional[datetime] = None
) -> Optional[Typed]:
    """The answer to the open slot, or None: the message goes on down the router.

    A wait never eats food. Grams and a time take only what parses, a name takes any
    text, and whatever the outcome the slot is closed, until the time screen, which
    goes on offering to type, opens it again. ``text`` is None for anything that is
    not an answer to any kind: a photo, a command, a menu button.
    """
    now = now or datetime.now(UTC)
    user = _user(db, telegram_id)
    ask = _close_slot(db, user)
    if ask is None or text is None or _expired(ask, now):
        return None
    t = _Tap(db, user, telegram_id, now)
    typed = _TYPED.get(ask.get("kind"))
    if typed is None:
        return None
    try:
        screen = typed(t, ask, text)
    except _Answer as answer:
        screen = answer.screen
    if screen is None:
        return None
    return Typed(screen, ask.get("message_id"))


def _typed_grams(t: _Tap, ask: dict, text: str) -> Optional[Screen]:
    grams = parse_grams(text)
    if grams is None:
        return None
    back = ask.get("back") or "r"
    return _apply_grams(t, ask["item_id"], grams, back, "slot")


def _apply_grams(t: _Tap, item_id: int, grams: float, back: str, data: str) -> Screen:
    item, meal = _own_item(t, item_id, back, data)
    change = meal_items.set_grams(t.db, t.user.id, item.id, grams)
    if change.status == meal_items.STALE:
        return _change_stale(t, change, meal, back)
    if change.status == meal_items.SAME:
        return _dish(t, item, meal, back, notice=NOTHING_SAME)
    notice = f"<i>✓ {num(grams)} г</i>"
    if not change.numbers_changed:
        notice = f"<i>✓ {num(grams)} г, цифры не менялись</i>"
    return _dish(t, item, meal, back, notice=notice, undo=change.event.id)


def _typed_add(t: _Tap, ask: dict, text: str) -> Optional[Screen]:
    back = ask.get("back") or "r"
    meal = _own_meal(t, ask["meal_id"], "slot")
    change = meal_items.add_item(t.db, t.user.id, meal.id, name=text)
    if change.status != meal_items.DONE:
        return None if change.meal is not None else _stale_meal(t)
    return _added(t, change, meal, back)


def _typed_replace(t: _Tap, ask: dict, text: str) -> Optional[Screen]:
    back = ask.get("back") or "r"
    item, meal = _own_item(t, ask["item_id"], back, "slot")
    return _replace(t, item, meal, back, name=text)


_TYPED: dict[str, Callable] = {
    GRAMS: _typed_grams,
    DISH_ADD: _typed_add,
    DISH_REPLACE: _typed_replace,
}


# --- changes ----------------------------------------------------------------


def _added(t: _Tap, change: meal_items.Change, meal: Meal, back: str) -> Screen:
    notice = f"<i>✓ Добавлено: {_esc(change.items[0].name)}</i>"
    return _card(t, meal, back, notice=notice, undo=change.event.id)


def _kept_note(item: MealItem) -> str:
    return f". {PLATE_KEPT}" if item.numbers_source == "plate_share" else ""


def _replace(
    t: _Tap, item: MealItem, meal: Meal, back: str, *, food_id=None, name=None
) -> Optional[Screen]:
    old, note = item.name, _kept_note(item)
    change = meal_items.replace_item(
        t.db, t.user.id, item.id, food_id=food_id, name=name
    )
    if change.status == meal_items.SAME:
        if food_id is not None:
            return Screen("", None, toast=meal_items.SAME_TOAST)
        return _dish(t, item, meal, back, notice=NOTHING_SAME)
    if change.status != meal_items.DONE:
        return None if change.meal is not None else _stale_meal(t)
    notice = f"<i>✓ Заменено: {_esc(old)} → {_esc(change.items[0].name)}{note}</i>"
    return _card(t, meal, back, notice=notice, undo=change.event.id)


# --- verbs ------------------------------------------------------------------


def _v_line(t: _Tap, data: str, mid: str) -> Screen:
    meal = _own_meal(t, int(mid), data)
    items = crud_meal_item.live(t.db, meal.id)
    what = _what(t.db, meal)
    text = f"Записано: {what}." if what else "Записано."
    fix = f"ml:i:{items[0].id}:r" if len(items) == 1 else f"ml:o:{meal.id}:r"
    return Screen(
        text,
        InlineKeyboardMarkup(
            [
                [
                    _button("✏️ Исправить", fix),
                    _button("🕐 Время", f"ml:t:{meal.id}:r"),
                ]
            ]
        ),
    )


def _v_open(t: _Tap, data: str, mid: str, back: str) -> Screen:
    return _card(t, _own_meal(t, int(mid), data), back)


def _v_page(t: _Tap, data: str, mid: str, page: str, back: str) -> Screen:
    return _card(t, _own_meal(t, int(mid), data), back, page=int(page))


def _v_numbers(t: _Tap, data: str, mid: str, want: str, back: str) -> Screen:
    return _card(t, _own_meal(t, int(mid), data), back, opened=want == "1")


def _more(t: _Tap, meal: Meal, back: str, *, toast: Optional[str] = None) -> Screen:
    items = crud_meal_item.live(t.db, meal.id)
    lines = [f"<b>🍽 Ещё: приём {_when(meal, t.zone)}</b>", _what(t.db, meal)]
    rows = []
    if len(items) >= 2:
        if _meal_favourite(t.db, meal) is not None:
            lines.append("Весь приём в избранном.")
        rows.append([_meal_star(t.db, meal, back)])
    rows.append(_to_card(meal.id, back))
    return Screen("\n".join(lines), InlineKeyboardMarkup(rows), toast=toast)


def _v_more(t: _Tap, data: str, mid: str, back: str) -> Screen:
    return _more(t, _own_meal(t, int(mid), data), back)


def _v_dish(t: _Tap, data: str, iid: str, back: str) -> Screen:
    item, meal = _own_item(t, int(iid), back, data)
    return _dish(t, item, meal, back)


def _v_dish_numbers(t: _Tap, data: str, iid: str, want: str, back: str) -> Screen:
    item, meal = _own_item(t, int(iid), back, data)
    return _dish(t, item, meal, back, opened=want == "1")


def _v_quantity(t: _Tap, data: str, iid: str, verb: str, back: str) -> Screen:
    item, meal = _own_item(t, int(iid), back, data)
    change = meal_items.set_quantity(t.db, t.user.id, item.id, _QUANTITY[verb])
    if change.status == meal_items.SAME:
        return Screen("", None, toast=meal_items.SAME_TOAST)
    if change.status == meal_items.NOT_APPLICABLE:
        return _dish(t, item, meal, back)
    if change.status != meal_items.DONE:
        return _change_stale(t, change, meal, back)
    # ×1 is the way back, so this screen gets no ↩️ of its own.
    return _dish(t, item, meal, back, notice=_DONE_NOTICE[verb])


def _v_grams(t: _Tap, data: str, iid: str, back: str) -> Screen:
    item, meal = _own_item(t, int(iid), back, data)
    _open_slot(t, GRAMS, back, meal_id=meal.id, item_id=item.id)
    return Screen(
        f"<b>⚖️ {_esc(_capital(item.name))}</b>\n" "Напиши граммы числом, например 150.",
        InlineKeyboardMarkup([_to_dish(item.id, back)]),
    )


def _removed_screen(t: _Tap, meal: Meal, event_id: int, back: str) -> Screen:
    """N6: items and totals stay, so the undo restores exactly."""
    what = _what(t.db, meal)
    when = _capital(_when(meal, t.zone))
    if back == "r":
        leave = [_button("📊 Карточка дня", "st:t")]
    else:
        leave = _back_row(t, meal, back)
    return Screen(
        "\n".join(
            [
                "<b>🗑 Приём удалён</b>",
                f"{when}: {what}." if what else f"{when}.",
                "Вернуть можно и позже, пока эта кнопка на экране.",
            ]
        ),
        InlineKeyboardMarkup([[_undo_button(event_id, back)], leave]),
    )


def _v_remove(t: _Tap, data: str, iid: str, back: str) -> Screen:
    item, meal = _own_item(t, int(iid), back, data)
    name, note = item.name, _kept_note(item)
    change = meal_items.remove_item(t.db, t.user.id, item.id)
    if change.status == meal_items.DELETED:
        return _removed_screen(t, meal, change.event.id, back)
    if change.status != meal_items.DONE:
        return _change_stale(t, change, meal, back)
    notice = f"<i>✓ Убрано: {_esc(name)}{note}</i>"
    return _card(t, meal, back, notice=notice, undo=change.event.id)


def _v_delete(t: _Tap, data: str, mid: str, back: str) -> Screen:
    meal = _own_meal(t, int(mid), data)
    change = meal_items.delete_meal(t.db, t.user.id, meal.id)
    if change.status != meal_items.DELETED:
        return _stale_meal(t)
    return _removed_screen(t, meal, change.event.id, back)


def _stamp(moment: Optional[datetime]) -> float:
    if moment is None:
        return 0.0
    if moment.tzinfo is None:  # SQLite drops it on read-back; the column is UTC
        moment = moment.replace(tzinfo=UTC)
    return moment.timestamp()


def _pick_list(t: _Tap, without: Optional[int]) -> list:
    """(dish, starred): ⭐ first, then the most recent."""
    dishes = t.db.scalars(
        select(PersonalFood).where(PersonalFood.user_id == t.user.id)
    ).all()
    stars = {
        fav.name.casefold() for fav in crud_favorite_meal.list_active(t.db, t.user.id)
    }
    rows = [
        (dish, dish.canonical_name.casefold() in stars)
        for dish in dishes
        if dish.id != without
    ]
    rows.sort(key=lambda row: (not row[1], -_stamp(row[0].last_used_at), -row[0].id))
    return rows


def _pick_label(dish: PersonalFood, star: bool) -> str:
    mark = "⭐ " if star else ""
    return mark + _cut(dish.canonical_name, PICK_LABEL_MAX - len(mark))


def _picker(
    t: _Tap,
    title: str,
    choose: Callable,
    turn: Callable,
    back_row: list,
    without: Optional[int],
    page: int,
    notice: Optional[str] = None,
) -> Screen:
    found = _pick_list(t, without)
    pages = max(1, (len(found) + PICK_PAGE - 1) // PICK_PAGE)
    page %= pages
    shown = found[page * PICK_PAGE : (page + 1) * PICK_PAGE]
    rows = [
        [
            _button(_pick_label(dish, star), choose(dish.id))
            for dish, star in shown[at : at + 2]
        ]
        for at in range(0, len(shown), 2)
    ]
    if pages > 1:
        rows.append(
            [
                _button("◀️", turn((page - 1) % pages)),
                _button(f"{page + 1}/{pages}", NOP),
                _button("▶️", turn((page + 1) % pages)),
            ]
        )
    rows.append(back_row)
    screen = Screen(
        f"<b>{title}</b>\nНажми или напиши название.", InlineKeyboardMarkup(rows)
    )
    return _with_notice(screen, notice)


def _add_picker(
    t: _Tap, meal: Meal, back: str, page: int, notice: Optional[str] = None
) -> Screen:
    _open_slot(t, DISH_ADD, back, meal_id=meal.id)
    return _picker(
        t,
        f"➕ Блюдо в приём {_when(meal, t.zone)}",
        lambda food: f"ml:pa:{meal.id}:{food}:{back}",
        lambda pg: f"ml:pk:{meal.id}:a:{back}:{pg}",
        _to_card(meal.id, back),
        None,
        page,
        notice,
    )


def _replace_picker(
    t: _Tap,
    item: MealItem,
    meal: Meal,
    back: str,
    page: int,
    notice: Optional[str] = None,
) -> Screen:
    _open_slot(t, DISH_REPLACE, back, meal_id=meal.id, item_id=item.id)
    return _picker(
        t,
        f"🔄 Вместо «{_esc(item.name)}»",
        lambda food: f"ml:pr:{item.id}:{food}:{back}",
        lambda pg: f"ml:pk:{item.id}:r:{back}:{pg}",
        _to_dish(item.id, back),
        item.personal_food_id,
        page,
        notice,
    )


def _v_pick(t: _Tap, data: str, row: str, kind: str, back: str, page: str) -> Screen:
    if kind == "a":
        return _add_picker(t, _own_meal(t, int(row), data), back, int(page))
    item, meal = _own_item(t, int(row), back, data)
    return _replace_picker(t, item, meal, back, int(page))


def _picked_dish(t: _Tap, food_id: int, data: str) -> Optional[PersonalFood]:
    """The dish, or None when it is gone. A foreign one is unknown data."""
    dish = _row(t.db, PersonalFood, food_id)
    if dish is not None and dish.user_id != t.user.id:
        raise _Answer(_unknown(t, data))
    return dish


def _v_pick_add(t: _Tap, data: str, mid: str, fid: str, back: str) -> Screen:
    meal = _own_meal(t, int(mid), data)
    dish = _picked_dish(t, int(fid), data)
    if dish is None:
        return _add_picker(t, meal, back, 0, STALE_PICK)
    change = meal_items.add_item(t.db, t.user.id, meal.id, food_id=dish.id)
    if change.status != meal_items.DONE:
        return (
            _stale_meal(t)
            if change.meal is None
            else _add_picker(t, meal, back, 0, STALE_PICK)
        )
    return _added(t, change, meal, back)


def _v_pick_replace(t: _Tap, data: str, iid: str, fid: str, back: str) -> Screen:
    item, meal = _own_item(t, int(iid), back, data)
    dish = _picked_dish(t, int(fid), data)
    if dish is None:
        return _replace_picker(t, item, meal, back, 0, STALE_PICK)
    screen = _replace(t, item, meal, back, food_id=dish.id)
    return screen or _replace_picker(t, item, meal, back, 0, STALE_PICK)


def _v_star_dish(t: _Tap, data: str, iid: str, want: str, back: str) -> Screen:
    """⭐ a dish: its portion comes from this item (US-O5)."""
    item, meal = _own_item(t, int(iid), back, data)
    existing = crud_favorite_meal.find_active_by_name(t.db, meal.user_id, item.name)
    if (want == "1") == (existing is not None):
        return Screen("", None, toast=meal_items.SAME_TOAST)
    if want == "0":
        crud_favorite_meal.set_removed(t.db, existing, True)
        screen = _dish(t, item, meal, back)
        return replace(screen, toast="Убрано из избранного")
    numbers = meal_items._shown(item)
    nutrients = {
        "foods": [item.name],
        "calories": numbers["calories"],
        "protein": numbers["proteins"],
        "fats": numbers["fats"],
        "carbs": numbers["carbohydrates"],
    }
    groups = meal_items._tag_rows(meal).get(item.name.casefold())
    if groups:
        rows = [{"name": item.name, "groups": groups}]
        nutrients |= {
            "food_items": rows,
            "food_groups": food_groups.from_items(rows),
            "food_groups_by_user": False,
        }
    crud_favorite_meal.create_from_numbers(
        t.db,
        user_id=meal.user_id,
        name=item.name,
        numbers=numbers,
        nutrients=nutrients,
        source_meal_id=meal.id,
    )
    return _dish(t, item, meal, back)


def _v_star_meal(t: _Tap, data: str, mid: str, want: str, back: str) -> Screen:
    meal = _own_meal(t, int(mid), data)
    existing = _meal_favourite(t.db, meal)
    if (want == "1") == (existing is not None):
        return Screen("", None, toast=meal_items.SAME_TOAST)
    toast = None
    if want == "0":
        crud_favorite_meal.set_removed(t.db, existing, True)
        toast = "Убрано из избранного"
    else:
        favorites.save_from_meal(t.db, meal, _favourite_name(meal))
    if crud_meal_item.live(t.db, meal.id):
        return _more(t, meal, back, toast=toast)
    return replace(_card(t, meal, back), toast=toast)


def _after_undo(
    t: _Tap, meal: Meal, event: RecordEvent, back: str, **shown: object
) -> Screen:
    """Grams came from the dish's screen and a time from its own, so the way back
    stays there."""
    if event.kind == TIME:
        return _time_screen(t, meal, back, **shown)
    if event.kind == "grams":
        before = event.before["items"]
        for key, state in (event.after or {}).get("items", {}).items():
            item = _row(t.db, MealItem, int(key))
            if (
                before.get(key) != state
                and item is not None
                and item.removed_at is None
            ):
                return _dish(t, item, meal, back, **shown)
    return _card(t, meal, back, **shown)


def _v_undo(t: _Tap, data: str, eid: str, back: str) -> Screen:
    event = _row(t.db, RecordEvent, int(eid))
    if (
        event is None
        or t.user is None
        or event.user_id != t.user.id
        or event.meal_id is None
    ):
        return _unknown(t, data)
    done = t.db.scalars(
        select(RecordEvent.id).where(RecordEvent.undoes_event_id == event.id)
    ).first()
    meal = _row(t.db, Meal, event.meal_id)
    if meal is None:
        return _unknown(t, data)
    if back == FROM_CARD and event.kind != TIME:
        back = "r"
    if done is not None:
        # A second tap: the state is what the first left.
        if meal.deleted_at:
            return _stale_meal(t)
        return (
            _time_screen(t, meal, back) if event.kind == TIME else _card(t, meal, back)
        )
    change = meal_items.undo(t.db, t.user.id, event.id)
    if change.status == meal_items.REFUSED:
        if meal.deleted_at:
            return _stale_meal(t)
        return _after_undo(t, meal, event, back, notice=meal_items.UNDO_REFUSED)
    if change.status != meal_items.DONE:
        return _unknown(t, data)
    if meal.deleted_at:
        return _removed_screen(t, meal, change.event.id, back)
    return _after_undo(t, meal, event, back, notice=UNDONE, undo=change.event.id)


# --- when it was eaten ------------------------------------------------------


def _time_back(meal: Meal, back: str) -> list:
    if back == "r":
        return [_button("⬅️ К ответу бота", f"ml:r:{meal.id}")]
    return _to_card(meal.id, "r" if back == FROM_CARD else back)


def _noon(day: date, zone) -> datetime:
    return datetime.combine(day, clock_time(12), tzinfo=zone)


def _yesterday_of(t: _Tap) -> date:
    """Counted from today, never from the meal: the label and the write share this."""
    return local_time(t.now, t.zone).date() - timedelta(days=1)


def _time_chip(meal: Meal, label: str, moment: datetime, kind: str, back: str):
    return _button(label, f"ml:ts:{meal.id}:{int(moment.timestamp())}:{kind}:{back}")


def _time_screen(
    t: _Tap,
    meal: Meal,
    back: str,
    *,
    notice: Optional[str] = None,
    undo: Optional[int] = None,
) -> Screen:
    """N5. The text offers typing, so whichever way it is drawn the slot is open."""
    _open_slot(t, TIME, back, meal_id=meal.id)
    zone = t.zone
    moment = local_time(meal.meal_time, zone)
    day = _yesterday_of(t)
    label = f"Вчера, {day.day} {stats_screen._MONTHS[day.month - 1]}"
    yesterday = _time_chip(
        meal,
        label,
        moment.replace(year=day.year, month=day.month, day=day.day),
        "a",
        back,
    )
    undo_row = [_undo_button(undo, back)] if undo else []
    if meal.time_precision == DAY_ONLY:
        rows = [[yesterday, *undo_row]]
    else:
        rows = [
            [
                _time_chip(meal, "−15 мин", moment - timedelta(minutes=15), "e", back),
                _time_chip(meal, "−1 ч", moment - timedelta(hours=1), "e", back),
                yesterday,
            ],
            [
                _time_chip(meal, "Не помню", _noon(moment.date(), zone), "d", back),
                *undo_row,
            ],
        ]
    rows.append(_time_back(meal, back))
    text = "\n".join(
        [
            "<b>🕐 Время приёма</b>",
            f"Записано на: {_when(meal, zone)}.",
            "Другое время можно написать, например 12:40 или вчера 23:10.",
        ]
    )
    return _with_notice(Screen(text, InlineKeyboardMarkup(rows)), notice)


def _time_notice(t: _Tap, meal: Meal, was: date) -> str:
    zone = t.zone
    day = meal_day(meal, t.user)
    if meal.time_precision == DAY_ONLY:
        title = stats_screen._card_title(day)
        return f"<i>✓ Время не указано. Приём остаётся в {title}, без времени.</i>"
    word = "Время примерное" if meal.time_precision == APPROX else "Время"
    if day != was:
        return f"<i>✓ {word}: {_when(meal, zone)}. Приём теперь в этом дне.</i>"
    return f"<i>✓ {word}: {_clock(meal, zone)}</i>"


def _apply_time(
    t: _Tap, meal: Meal, moment: datetime, precision: str, back: str
) -> Optional[Screen]:
    """The screen after the write, or None when the meal is already there. The noon
    of ``day_only`` claims no clock, so only a real time can be ahead of now."""
    if precision != DAY_ONLY and moment > t.now:
        return _time_screen(t, meal, back, notice=TIME_FUTURE)
    was = meal_day(meal, t.user)
    change = meal_items.set_time(t.db, t.user.id, meal.id, moment, precision)
    if change.status == meal_items.SAME:
        return None
    if change.status != meal_items.DONE:
        return _stale_meal(t)
    return _time_screen(
        t, meal, back, notice=_time_notice(t, meal, was), undo=change.event.id
    )


def _typed_time(t: _Tap, ask: dict, text: str) -> Optional[Screen]:
    parsed = parse_time(text)
    if parsed is None:
        return None
    yesterday, clock = parsed
    back = ask.get("back") or "r"
    meal = _own_meal(t, ask["meal_id"], "slot")
    day = _yesterday_of(t) if yesterday else meal_day(meal, t.user)
    moment = datetime.combine(day, clock, tzinfo=t.zone)
    screen = _apply_time(t, meal, moment, EXACT, back)
    return screen or _time_screen(t, meal, back, notice=NOTHING_SAME)


_TYPED[TIME] = _typed_time


def _v_time(t: _Tap, data: str, mid: str, back: str) -> Screen:
    return _time_screen(t, _own_meal(t, int(mid), data), back)


def _v_time_set(
    t: _Tap, data: str, mid: str, stamp: str, kind: str, back: str
) -> Screen:
    meal = _own_meal(t, int(mid), data)
    precision = _PRECISION_OF[kind]
    if precision == DAY_ONLY:
        # The meal's present day, not the button's: it may have moved since it was drawn.
        moment = meal.meal_time
        if meal.time_precision != DAY_ONLY:
            moment = _noon(meal_day(meal, t.user), t.zone)
    else:
        try:
            moment = datetime.fromtimestamp(int(stamp), UTC)
        except (ValueError, OverflowError, OSError):
            return _unknown(t, data)
    screen = _apply_time(t, meal, moment, precision, back)
    if screen is not None:
        return screen
    # The tap closed the slot, and the screen on show goes on offering to type.
    _open_slot(t, TIME, back, meal_id=meal.id)
    return Screen("", None, toast=meal_items.SAME_TOAST)


# --- the day's meals --------------------------------------------------------


def _day_label(day: date) -> str:
    return f"{stats_screen._WEEKDAYS[day.weekday()]} {day.day}"


def _sort_key(meal: Meal, zone) -> tuple:
    """Without a time a meal sits at 12:00, where it reads as «some time that day»."""
    moment = local_time(meal.meal_time, zone)
    if meal.time_precision == "day_only":
        return (12, 0, moment)
    return (moment.hour, moment.minute, moment)


def _day_line_name(db, meal: Meal) -> str:
    names = _meal_names(db, meal)
    if not names:
        return "📷 фото сохранено" if meal.photos else "запись"
    return _esc(_cut(_names(names), LINE_NAME_MAX))


def _day_chips(meals: list, zone, origin: str, code: str, page: int) -> list:
    seen: dict = {}
    chips = []
    for meal in meals:
        clock = _clock(meal, zone)
        seen[clock] = seen.get(clock, 0) + 1
        label = clock if seen[clock] == 1 else f"{clock} · {seen[clock]}"
        chips.append(_button(label, f"ml:o:{meal.id}:{origin}"))
    if len(chips) > DAY_CHIPS:
        per_page = DAY_CHIPS - 1
        pages = (len(chips) + per_page - 1) // per_page
        page %= pages
        shown = chips[page * per_page : (page + 1) * per_page]
        following = (page + 1) % pages
        chips = [*shown, _button("▶️", f"ml:dl:{code}:{origin}:{following}")]
    return [chips[at : at + CHIPS_ROW] for at in range(0, len(chips), CHIPS_ROW)]


def _day_list(t: _Tap, day: date, origin: str, page: int = 0) -> Screen:
    """Lines of what, chips of when. Writes nothing."""
    zone = t.zone
    today = local_time(t.now, zone).date()
    meals = sorted(
        stats_screen._meals_on(t.db, t.user, day, zone),
        key=lambda meal: _sort_key(meal, zone),
    )
    lines = [f"<b>🍽 Приёмы, {stats_screen._card_title(day)}</b>"]
    if meals:
        lines += [f"{_clock(m, zone)} · {_day_line_name(t.db, m)}" for m in meals]
        if not t.simple:
            lines.append("<i>Нажми на время, чтобы открыть приём и поправить.</i>")
    else:
        lines.append("В этом дне записей нет.")
    code = stats_screen._day_code(day)
    rows = _day_chips(meals, zone, origin, code, page)
    earlier, later = day - timedelta(days=1), day + timedelta(days=1)
    walk = [
        _button(
            f"‹ {_day_label(earlier)}",
            f"ml:dl:{stats_screen._day_code(earlier)}:{origin}",
        )
    ]
    if later <= today:
        walk.append(
            _button(
                f"{_day_label(later)} ›",
                f"ml:dl:{stats_screen._day_code(later)}:{origin}",
            )
        )
    rows.append(walk)
    rows.append([_button(_DAY_BACK[origin], stats_screen.CALLBACK_PREFIX + origin)])
    return Screen("\n".join(lines), InlineKeyboardMarkup(rows))


def _v_day(t: _Tap, data: str, code: str, origin: str, page: Optional[str]) -> Screen:
    try:
        day = datetime.strptime(code, "%Y%m%d").date()
    except ValueError:
        return _unknown(t, data)
    return _day_list(t, day, origin, int(page or 0))


# --- the table --------------------------------------------------------------

_ID = r"(\d{1,18})"
_BACK = r"(r|t|y|w|h\d{1,18})"
_BACK_TIME = r"(r|c|t|y|w|h\d{1,18})"
_VERBS: tuple = (
    (re.compile(rf"ml:r:{_ID}"), _v_line),
    (re.compile(rf"ml:o:{_ID}:{_BACK}"), _v_open),
    (re.compile(rf"ml:op:{_ID}:(\d{{1,2}}):{_BACK}"), _v_page),
    (re.compile(rf"ml:mo:{_ID}:{_BACK}"), _v_more),
    (re.compile(rf"ml:n:{_ID}:([01]):{_BACK}"), _v_numbers),
    (re.compile(rf"ml:i:{_ID}:{_BACK}"), _v_dish),
    (re.compile(rf"ml:ni:{_ID}:([01]):{_BACK}"), _v_dish_numbers),
    (re.compile(rf"ml:q:{_ID}:(h|1|2):{_BACK}"), _v_quantity),
    (re.compile(rf"ml:g:{_ID}:{_BACK}"), _v_grams),
    (re.compile(rf"ml:x:{_ID}:{_BACK}"), _v_remove),
    (re.compile(rf"ml:d:{_ID}:{_BACK}"), _v_delete),
    (re.compile(rf"ml:u:{_ID}:{_BACK_TIME}"), _v_undo),
    (re.compile(rf"ml:t:{_ID}:{_BACK_TIME}"), _v_time),
    (re.compile(rf"ml:ts:{_ID}:(\d{{1,12}}):(e|a|d):{_BACK_TIME}"), _v_time_set),
    (re.compile(rf"ml:pk:{_ID}:(a|r):{_BACK}:(\d{{1,2}})"), _v_pick),
    (re.compile(rf"ml:pa:{_ID}:{_ID}:{_BACK}"), _v_pick_add),
    (re.compile(rf"ml:pr:{_ID}:{_ID}:{_BACK}"), _v_pick_replace),
    (re.compile(rf"ml:fi:{_ID}:([01]):{_BACK}"), _v_star_dish),
    (re.compile(rf"ml:fm:{_ID}:([01]):{_BACK}"), _v_star_meal),
    (re.compile(r"ml:dl:(\d{8}):(t|y|w)(?::(\d{1,2}))?"), _v_day),
)


def is_known_shape(data: str) -> bool:
    return data == NOP or any(p.fullmatch(data) for p, _verb in _VERBS)


def handle_callback(
    db,
    telegram_id: int,
    data: str,
    now: Optional[datetime] = None,
    message_id: Optional[int] = None,
) -> Screen:
    """The screen a tap leads to. Any tap but the pager's middle closes the slot."""
    if data == NOP:
        return Screen("", None)
    user = _user(db, telegram_id)
    _close_slot(db, user)
    t = _Tap(db, user, telegram_id, now or datetime.now(UTC), message_id)
    for pattern, verb in _VERBS:
        match = pattern.fullmatch(data)
        if match is None:
            continue
        try:
            return verb(t, data, *match.groups())
        except _Answer as answer:
            return answer.screen
    return _unknown(t, data)
