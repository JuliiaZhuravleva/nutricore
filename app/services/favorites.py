"""⭐ Избранное: a dish to log again with one tap (section 6 of the spec, O9 to O11b).

A row of the list logs at once, and its pencil opens the favourite. A log copies the
stored numbers, with what a person corrected on the dish over them, and calls no model.
The way back is an undo, not a question.

A ⭐ goes by its dish: the name it shows and logs is the dish's, so a rename or a merge
reaches it. The portion typed here is the dish's one number, and that handler lives in
``dishes``, which holds the dish's events.

Every button's data carries the whole step, so a tap works after a restart. A ⭐ removed
meanwhile gets the current list with a notice; a foreign or malformed id gets what
unknown data gets everywhere: today's card and a warning.

``meal_card`` imports this module for ``Screen``, so what is borrowed back from it is
imported at call time.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.crud.crud_favorite_meal import crud_favorite_meal
from app.crud.crud_meal_item import crud_meal_item
from app.models.favorite_meal import FavoriteMeal
from app.models.meal import Meal
from app.models.meal_item import MealItem
from app.models.meal_message import MealMessage
from app.models.personal_food import PersonalFood
from app.models.user import User
from app.schemas.meal import MealCreate
from app.services import meal_items
from app.services.close_row import ok_row
from app.services.numbers import shows_numbers, toggle_label

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "fav:"
NOP = "fav:x"
PAGE_SIZE = 4
NAME_MAX = 60
BUTTON_NAME_MAX = 30
ROW_LABEL_MAX = 34  # a row with its number, the spec's own example
PAIR_LABEL_MAX = 20  # a name beside its pencil
_INT_MAX = 2**31 - 1
# meals.resolution_source for a meal logged from a favorite.
FAVORITE_SOURCE = "favorite"
# The typed slot of O11a; ``dishes`` registers its answer.
PORTION = "portion"

_HEADER = "<b>⭐ Избранное</b>"
_HINT = "Нажатие на название записывает сразу. Кнопка ✏️: порция и другие настройки."
_EMPTY = "Пока пусто. ⭐ ставится кнопкой «⭐ В избранное» у блюда."
STALE = "<i>Этого блюда уже нет в избранном. Вот актуальный список.</i>"
_AS_THAT_MEAL = "Порция: как в той записи."
_NOT_COUNTED = "Цифры не посчитаны."
_ALREADY_UNDONE = "<i>Эта запись уже отменена.</i>"
_TO_LIST = "⬅️ Избранное"
LOG_TOAST = "Записано"
LOG_AGAIN_TOAST = "Уже записано"


@dataclass
class Screen:
    text: str  # HTML
    markup: InlineKeyboardMarkup | None
    toast: str | None = None  # answerCallbackQuery text, seen only by the tapper
    picture: dict | None = None  # the card for the renderer; ``text`` is its twin
    meal_id: int | None = None  # a reply to this message means this meal
    view: str | None = None  # how the message maps: meal_messages.view, or no meal
    fill: object | None = None  # a new name's numbers, in the background
    suggest_dish: int | None = None  # a dish to guess a portion for, in the background
    retouch: object | None = None  # the meal's original line, edited silently
    read: object | None = None  # a read to start once this screen is on the message
    split: object | None = None  # an album split: each photo's line and read to start
    gone: tuple = ()  # message ids of lines the change took back
    files: tuple = ()  # (kind, file_id) to send again, silently, before the answer
    retouch_too: object | None = None  # a second line the change reached


def _card():
    from app.services import meal_card

    return meal_card


# --- naming and numbers -----------------------------------------------------


def favorite_name(
    *, caption: str | None, description: str | None, foods: list | None
) -> str:
    """What the favorite is called: the user's own words first.

    A photo's caption ("самса веган с нефаршем") beats the model's food list
    ("savory pastry (likely filled)"), which is English and a guess.
    """
    for candidate in (caption, description, ", ".join(map(str, foods or []))):
        first_line = (candidate or "").strip().split("\n", 1)[0]
        text = " ".join(first_line.split())
        if text:
            break
    else:
        text = "Блюдо"
    if len(text) > NAME_MAX:
        text = text[: NAME_MAX - 1].rstrip() + "…"
    return text


def _num(value) -> str | None:
    if value is None:
        return None
    try:
        return f"{round(float(value), 1):g}"
    except (TypeError, ValueError):
        return None


def _kcal(value, dish: PersonalFood | None, grams: float | None) -> str:
    """A number nobody checked says so."""
    sure = dish is not None and meal_items.numbers_checked(dish, grams)
    return f"{'' if sure else '≈ '}{round(float(value))} ккал"


def _shown_name(db, fav: FavoriteMeal) -> str:
    dish = crud_favorite_meal.dish_if_any(db, fav)
    return dish.canonical_name if dish is not None else fav.name


def _numbers(db, fav: FavoriteMeal, dish: PersonalFood | None) -> tuple[dict, float]:
    """What a tap would log, and at how many grams."""
    if dish is None:
        return {key: getattr(fav, key) for key in meal_items.FIELDS}, fav.usual_grams
    portion = meal_items.star_portion(db, dish, fav)
    return portion.numbers or {}, portion.grams


def _macros(numbers: dict, dish: PersonalFood | None, grams: float | None) -> str:
    if numbers.get("calories") is None:
        return _NOT_COUNTED
    parts = [f"🔥 {_kcal(numbers['calories'], dish, grams)}"]
    for label, key in (("Б", "proteins"), ("Ж", "fats"), ("У", "carbohydrates")):
        shown = _num(numbers.get(key))
        if shown is not None:
            parts.append(f"{label} {shown}")
    return " · ".join(parts)


def logged_line(
    db, name: str, meal: Meal, dish: PersonalFood | None, numbers: bool
) -> str:
    """O10; with the numbers on, as a fresh line says them."""
    tail = ""
    if numbers and meal.calories is not None:
        items = crud_meal_item.live(db, meal.id)
        grams = items[0].base_grams if items else None
        tail = f" · {_kcal(meal.calories, dish, grams)}"
    return f"Записано: {_card()._esc(name)}{tail}."


def _btn(text: str, *parts) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text, callback_data=CALLBACK_PREFIX + ":".join(map(str, parts))
    )


def _to_list(page: int = 0) -> list:
    return [_btn(_TO_LIST, "p", page)]


def _plain(text: str, rows: list, **fields) -> Screen:
    """A screen that shows no one meal, so a reply to it means none."""
    return Screen(text, InlineKeyboardMarkup(rows), view=_card().NO_MEAL, **fields)


# --- screens ----------------------------------------------------------------


def _row_label(db, fav: FavoriteMeal, numbers: bool) -> str:
    card = _card()
    dish = crud_favorite_meal.dish_if_any(db, fav)
    name = card._capital(dish.canonical_name if dish is not None else fav.name)
    held, grams = _numbers(db, fav, dish)
    calories = held.get("calories")
    if not numbers or calories is None:
        return card._cut(name, PAIR_LABEL_MAX)
    tail = f" · {_kcal(calories, dish, grams)}"
    return card._cut(name, ROW_LABEL_MAX - len(tail)) + tail


def _list_screen(db, favs: list, page: int, notice, numbers: bool) -> Screen:
    """O9, or O9a. Opening it writes nothing."""
    top = f"{notice}\n\n{_HEADER}" if notice else _HEADER
    if not favs:
        return Screen(f"{top}\n{_EMPTY}", None, view=_card().NO_MEAL)
    pages = (len(favs) + PAGE_SIZE - 1) // PAGE_SIZE
    page = min(page, pages - 1)
    shown = favs[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    rows = [
        [
            _btn(_row_label(db, fav, numbers), "l", fav.id, "n"),
            _btn("✏️", "o", fav.id, page),
        ]
        for fav in shown
    ]
    if pages > 1:
        pager = [_btn(f"{page + 1}/{pages}", "x")]
        if page > 0:
            pager.insert(0, _btn("◀️", "p", page - 1))
        if page < pages - 1:
            pager.append(_btn("▶️", "p", page + 1))
        rows.append(pager)
    return _plain(f"{top}\n{_HINT}", rows)


def open_list(db, telegram_id: int, page: int = 0, notice: str | None = None) -> Screen:
    user = _user(db, telegram_id)
    favs = crud_favorite_meal.list_active(db, user.id) if user else []
    return _list_screen(db, favs, page, notice, shows_numbers(user))


def assumed_portion_line(dish: PersonalFood, grams: float) -> str:
    """A weight nobody gave says so: «примерно» and its unit when it is the model's."""
    if dish.suggested_grams and grams == dish.suggested_grams:
        unit = "мл" if dish.suggested_unit == "ml" else "г"
        return f"Порция не указана: считаю примерно {_num(grams)} {unit}."
    return f"Порция не указана: считаю {_num(grams)} г."


def _portion_line(
    dish: PersonalFood, grams: float | None, shown: bool, assumed: bool = False
) -> str | None:
    """A typed portion always shows. Any other only with the numbers open, since it is
    a detail nobody asked for."""
    if dish.usual_grams_by_user and dish.usual_grams:
        return f"Обычная порция: {_num(dish.usual_grams)} г."
    if not shown:
        return None
    if assumed and grams:
        return assumed_portion_line(dish, grams)
    return f"Обычная порция: {_num(grams)} г." if grams else _AS_THAT_MEAL


def item_screen(
    t,
    fav: FavoriteMeal,
    page: int = 0,
    notice: str | None = None,
    opened: bool = False,
    undo: int | None = None,
) -> Screen:
    """O11. ``undo``: the event a portion typed just now wrote."""
    card = _card()
    dish = crud_favorite_meal.dish_if_any(t.db, fav)
    if dish is None:  # a row an old container wrote: the button below needs its dish
        dish = crud_favorite_meal.dish_of(t.db, fav)
        t.db.commit()
    numbers_on = shows_numbers(t.user)
    shown = numbers_on or opened
    numbers, grams = _numbers(t.db, fav, dish)
    lines = [f"<b>⭐ {card._esc(card._capital(dish.canonical_name))}</b>"]
    assumed = meal_items.star_portion(t.db, dish, fav).assumed
    portion = _portion_line(dish, grams, shown, assumed)
    if portion:
        lines.append(portion)
    if shown:
        lines.append(_macros(numbers, dish, grams))
    text = "\n".join(lines)
    middle = [_btn("⚖️ Порция", "g", fav.id)]
    if not numbers_on:
        flip = [] if opened else [1]
        middle.append(_btn(toggle_label(opened), "o", fav.id, page, *flip))
    rows = [
        [_btn("🍽 Записать", "l", fav.id, "n")],
        middle,
        [
            InlineKeyboardButton(
                "🍲 Всё о блюде", callback_data=f"dh:o:{dish.id}:f{fav.id}"
            ),
            _btn("🗑 Из избранного", "r", fav.id, page),
        ],
    ]
    if undo is not None:
        rows.append(
            [
                InlineKeyboardButton(
                    card.UNDO_LABEL, callback_data=f"dh:u:{undo}:f{fav.id}"
                )
            ]
        )
    rows.append(_to_list(page))
    return _plain(f"{notice}\n\n{text}" if notice else text, rows)


# --- operations ---------------------------------------------------------------


def save_from_meal(db, meal: Meal, name: str) -> tuple[FavoriteMeal, bool]:
    """Add the meal to favorites; (favorite, created). A same-named active
    favorite is reused rather than duplicated."""
    existing = crud_favorite_meal.find_active_by_name(db, meal.user_id, name)
    if existing is not None:
        return existing, False
    return crud_favorite_meal.create_from_meal(db, meal, name), True


def _user(db, telegram_id: int) -> User | None:
    return db.query(User).filter(User.telegram_id == telegram_id).first()


def just_logged(db, user_id: int, message_id, dish_id: int, now: datetime) -> bool:
    """A log is not a target state, so a double tap needs the clock and the message."""
    if message_id is None:
        return False
    created = db.scalars(
        select(Meal.created_at)
        .join(MealMessage, MealMessage.meal_id == Meal.id)
        .join(MealItem, MealItem.meal_id == Meal.id)
        .where(
            MealMessage.message_id == message_id,
            Meal.user_id == user_id,
            Meal.deleted_at.is_(None),
            Meal.resolution_source == FAVORITE_SOURCE,
            MealItem.personal_food_id == dish_id,
        )
        .order_by(Meal.id.desc())
    ).first()
    if created is None:
        return False
    return abs(now - meal_items._utc(created)) <= _card().REPEAT_WINDOW


def _log(t, fav: FavoriteMeal, dish: PersonalFood | None, name: str) -> Meal:
    return log_at(t, fav, dish, name, meal_time=t.now, precision="exact", source="sent")


def log_at(
    t,
    fav: FavoriteMeal,
    dish: PersonalFood | None,
    name: str,
    *,
    meal_time: datetime,
    precision: str,
    source: str,
    extra_signals: dict | None = None,
) -> Meal:
    """A ⭐ logged at ``meal_time``: now from the list, a reminder's day from 🔔. Still
    a favourite's meal either way, so the double-tap guard and the undo know it."""
    # What a person corrected on the dish goes over the favourite's copy.
    fields = meal_items.star_meal_fields(t.db, dish, fav)
    grams = fields.pop("grams")
    signals = {
        "favorite_id": fav.id,
        **({"portion_grams": grams} if grams else {}),
        **({"portion_assumed": True} if fields.pop("assumed") else {}),
        **(extra_signals or {}),
    }
    meal_in = MealCreate(
        description=name,
        meal_time=meal_time,
        sent_at=t.now,
        time_precision=precision,
        time_source=source,
        **fields,
        resolution_source=FAVORITE_SOURCE,
        resolution_signals=signals,
    )
    # A favourite is one dish, whatever its plate held. Its numbers stay with it: the
    # dish in the base gets a link, and none of them.
    meal = meal_items.save_meal(
        t.db, meal_in, t.user.id, names=[name], numbers_source="dish"
    )
    crud_favorite_meal.mark_used(t.db, fav)
    return meal


# --- the verbs --------------------------------------------------------------


def _unknown(t, data: str) -> Screen:
    logger.warning("favorites: unknown or foreign data %r", data)
    return _card()._today(t)


def _stale(t, page: int = 0):
    return _card()._Answer(open_list(t.db, t.telegram_id, page, STALE))


def _own(t, data: str, fid: str, page: int = 0) -> FavoriteMeal:
    """Theirs, removed or not. One that is gone is a stale button; another's is not."""
    row = t.db.get(FavoriteMeal, int(fid)) if int(fid) <= _INT_MAX else None
    if int(fid) > _INT_MAX or t.user is None or (row and row.user_id != t.user.id):
        raise _card()._Answer(_unknown(t, data))
    if row is None:
        raise _stale(t, page)
    return row


def _active(t, data: str, fid: str, page: int = 0) -> FavoriteMeal:
    fav = _own(t, data, fid, page)
    if fav.removed_at is not None:
        raise _stale(t, page)
    return fav


def _v_list(t, data: str, page: str) -> Screen:
    return open_list(t.db, t.telegram_id, int(page))


def _v_open(t, data: str, fid: str, page: str, flag: str | None) -> Screen:
    fav = _active(t, data, fid, int(page))
    return item_screen(t, fav, int(page), opened=flag is not None)


def _v_log(t, data: str, fid: str, chip: str) -> Screen:
    """``chip``: ``n``. An old message's chips for a label still log, with no label."""
    fav = _active(t, data, fid)
    dish = crud_favorite_meal.dish_if_any(t.db, fav)
    if dish and just_logged(t.db, t.user.id, t.message_id, dish.id, t.now):
        return Screen("", None, toast=LOG_AGAIN_TOAST)
    name = dish.canonical_name if dish is not None else fav.name
    meal = _log(t, fav, dish, name)
    rows = [
        ok_row(),
        [
            _btn("↩️ Отменить", "u", meal.id),
            InlineKeyboardButton("🕐 Время", callback_data=f"ml:t:{meal.id}:r"),
        ],
        _to_list(),
    ]
    return Screen(
        logged_line(t.db, name, meal, dish, shows_numbers(t.user)),
        InlineKeyboardMarkup(rows),
        toast=LOG_TOAST,
        meal_id=meal.id,
    )


def _v_undo(t, data: str, mid: str) -> Screen:
    meal = t.db.get(Meal, int(mid)) if int(mid) <= _INT_MAX else None
    if int(mid) > _INT_MAX or t.user is None or (meal and meal.user_id != t.user.id):
        return _unknown(t, data)
    if (
        meal is None
        or meal.deleted_at is not None
        or meal.resolution_source != FAVORITE_SOURCE
    ):
        return _plain(_ALREADY_UNDONE, [_to_list()])
    name = meal.description or "Блюдо"
    # Soft, as every delete: a pull holding this row learns of it (3.3).
    meal_items.delete_meal(t.db, t.user.id, meal.id)
    return _plain(
        f"↩️ Запись отменена: {_card()._esc(name)}.", [_to_list()], toast="Отменено"
    )


def _v_portion(t, data: str, fid: str) -> Screen:
    """O11a. Whatever this tap meets, the slot open before it is over."""
    card = _card()
    card._close_slot(t.db, t.user)
    fav = _active(t, data, fid)
    dish = crud_favorite_meal.dish_of(t.db, fav)
    t.db.commit()
    card._open_slot(t, PORTION, str(fav.id), food_id=dish.id)
    return _plain(
        f"<b>⚖️ Порция: {card._esc(dish.canonical_name)}</b>\n"
        "Напиши граммы или миллилитры числом, например 200.",
        [[_btn("⬅️ К избранному блюду", "o", fav.id, 0)]],
    )


def _v_remove(t, data: str, fid: str, page: str) -> Screen:
    fav = _active(t, data, fid, int(page))
    name = _shown_name(t.db, fav)
    crud_favorite_meal.set_removed(t.db, fav, True)
    return _plain(
        f"<b>🗑 Убрано из избранного: {_card()._esc(name)}</b>\n"
        "Само блюдо на месте.\n"
        "Вернуть можно и позже, пока эта кнопка на экране.",
        [[_btn(_card().UNDO_LABEL, "b", fav.id, page)], _to_list(int(page))],
    )


def _v_restore(t, data: str, fid: str, page: str) -> Screen:
    """A ⭐ asks for its dish, so a hidden one comes back. A dish has one ⭐: one made
    since is the one this button opens."""
    fav = _own(t, data, fid, int(page))
    dish = crud_favorite_meal.dish_if_any(t.db, fav)
    held = crud_favorite_meal.active_of_dish(t.db, dish.id) if dish else None
    if held is not None:
        return item_screen(t, held, int(page))
    if fav.removed_at is None:
        return item_screen(t, fav, int(page))
    if dish is not None:
        dish.hidden_at = None
        fav.personal_food_id = dish.id  # it may have merged while this was off
    crud_favorite_meal.set_removed(t.db, fav, False)
    return item_screen(t, fav, int(page), notice=_card().UNDONE)


_ID = r"(\d{1,12})"
_PAGE = r"(\d{1,5})"
_VERBS = (
    (re.compile(rf"fav:p:{_PAGE}"), _v_list),
    (re.compile(rf"fav:o:{_ID}:{_PAGE}(?::(1|n))?"), _v_open),
    (re.compile(rf"fav:l:{_ID}:([nbld])"), _v_log),
    (re.compile(rf"fav:u:{_ID}"), _v_undo),
    (re.compile(rf"fav:g:{_ID}"), _v_portion),
    (re.compile(rf"fav:r:{_ID}:{_PAGE}"), _v_remove),
    (re.compile(rf"fav:b:{_ID}:{_PAGE}"), _v_restore),
)


def handle_callback(
    db,
    telegram_id: int,
    data: str,
    now: datetime | None = None,
    message_id: int | None = None,
) -> Screen:
    """The screen a tap on a favorites button leads to.

    Every id in ``data`` is checked against the tapping user: callback data comes from
    the client, so a crafted one must not reach another user's rows.
    """
    if data == NOP:
        return Screen("", None)  # the pager's page counter: nothing to do
    card = _card()
    user = _user(db, telegram_id)
    t = card._Tap(db, user, telegram_id, now or datetime.now(UTC), message_id)
    for pattern, verb in _VERBS:
        match = pattern.fullmatch(data)
        if match is None:
            continue
        try:
            return verb(t, data, *match.groups())
        except card._Answer as answer:
            return answer.screen
    return _unknown(t, data)
