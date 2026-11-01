"""🍲 Блюда: every dish the record knows, the ``dh:`` verbs (section 6 of the spec).

Sits above ``meal_card``: it borrows the tap, the screens' words and the typed slot,
and registers its own kinds of answer. Nothing here calls a model.

A dish is one row of ``personal_foods``. What was eaten stays in the meals: a rename, a
merge or new numbers reach future meals only, and every change writes an event its
``↩️ Вернуть`` reads. Names are compared in Python (SQLite's lower() is ASCII only), and
no query here selects a JSON column for counting, since Postgres cannot compare one.
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import select
from telegram import InlineKeyboardMarkup

from app.crud.crud_favorite_meal import crud_favorite_meal
from app.crud.crud_personal_food import _key, crud_personal_food
from app.crud.crud_record_event import crud_record_event
from app.models.favorite_meal import FavoriteMeal
from app.models.meal import Meal
from app.models.meal_item import MealItem
from app.models.personal_food import PersonalFood
from app.models.record_event import RecordEvent
from app.schemas.meal import MealCreate
from app.services import favorites, food_groups, meal_card, meal_items, stats_screen
from app.services.day_card import local_time
from app.services.favorites import BUTTON_NAME_MAX, FAVORITE_SOURCE, Screen
from app.services.meal_card import (
    NO_MEAL,
    STALE_PICK,
    UNDO_LABEL,
    UNDONE,
    _Answer,
    _button,
    _capital,
    _close_slot,
    _cut,
    _esc,
    _open_slot,
    _Tap,
    _user,
    _when,
    _with_notice,
)
from app.services.numbers import shows_numbers, toggle_label

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "dh:"
NOP = "dh:nop"
PAGE = 4
FOUND_MORE = "Показаны {shown} из {found}. Напиши точнее."
HISTORY_PAGE = 9
CHIPS_ROW = 3
ROW_LABEL_MAX = 34
PAIR_LABEL_MAX = 20
DEFAULT_BACK = "l0"
_INT_MAX = 2**31 - 1
_DIGITS = re.compile(r"\d{1,5}(?:[.,]\d{1,2})?")

DISH_NAME = "dish_name"
DISH_NUMBERS = "dish_numbers"
DISH_PORTION = "dish_portion"
DISH_FIND = "dish_find"
MERGE_FIND = "merge_find"

TITLE = "<b>🍲 Блюда</b>"
RECENT_FIRST = "Сначала недавние."
EMPTY = "Пока пусто. Блюда появятся сами, когда еда будет записываться."
FIND_TITLE = "<b>🔍 Найти блюдо</b>"
FIND_ASK = "Напиши название."
ALL = "⬅️ Все блюда"
TO_DISH = "⬅️ К блюду"
EARLY = "Ранние записи могут здесь не показываться: в них блюда не разделены."
NO_MEALS = "Пока без приёмов."
ESTIMATE = "Это оценка, цифры примерные."
NO_NUMBERS = "Цифр пока нет."
MERGE_KEEPS = "Прошлые приёмы и их цифры останутся как есть."
MERGED_ALREADY = "<i>Это блюдо уже объединено.</i>"
NAME_IS_THIS_DISH = "<i>Это название уже ведёт к этому блюду.</i>"
NUMBERS_NOTICE = "<i>✓ Для будущих приёмов. Прошлые остаются как были.</i>"
LOG_TOAST = "Записано"
LOG_AGAIN_TOAST = "Уже записано"
STAR_OFF_TOAST = "Убрано из избранного"

_NUMBER_COLUMNS = (
    "per_100g_calories",
    "per_100g_proteins",
    "per_100g_fats",
    "per_100g_carbs",
)
_NUMBER_STATE = ("numbers_by_user", "numbers_quality", "numbers_basis")
_PORTION_STATE = ("usual_grams", "usual_grams_by_user")
_UNDOABLE = ("rename", "numbers", "portion", "food_merge", "food_hide")


# --- words ------------------------------------------------------------------


def _times(count: int) -> str:
    """раз / раза, by the last digits. «1 раз», «3 раза», «12 раз», «22 раза»."""
    if count % 100 in (12, 13, 14):
        return "раз"
    return "раза" if count % 10 in (2, 3, 4) else "раз"


def _tail(back: str) -> str:
    """The table's shape holds for the default origin; any other travels on."""
    return "" if back == DEFAULT_BACK else f":{back}"


def _screen(lines: list, rows: list, **fields) -> Screen:
    return Screen(
        "\n".join(lines),
        InlineKeyboardMarkup(rows) if rows else None,
        view=fields.pop("view", NO_MEAL),
        **fields,
    )


def _pages(count: int, size: int, page: int) -> tuple:
    """(pages, the page, clamped): a page past the end is the last one."""
    pages = max((count + size - 1) // size, 1)
    return pages, min(page, pages - 1)


def _unknown(t: _Tap, data: str) -> Screen:
    logger.warning("dishes: unknown or foreign data %r", data)
    return meal_card._today(t)


# --- what the record says about dishes --------------------------------------


def _live_items(t: _Tap):
    return (
        select(MealItem.personal_food_id, Meal.meal_time)
        .join(Meal, Meal.id == MealItem.meal_id)
        .where(
            Meal.user_id == t.user.id,
            Meal.deleted_at.is_(None),
            Meal.is_draft.is_(False),
            MealItem.removed_at.is_(None),
            MealItem.personal_food_id.is_not(None),
        )
    )


def _eaten(t: _Tap) -> dict:
    """Dish id → (live items, the latest of their moments). Plain columns only."""
    eaten: dict = {}
    for food_id, moment in t.db.execute(_live_items(t)):
        count, latest = eaten.get(food_id, (0, 0.0))
        eaten[food_id] = (count + 1, max(latest, meal_card._stamp(moment)))
    return eaten


def _stars(t: _Tap) -> set:
    """The dishes with an active ⭐, by the link. An old row gets its link here."""
    stars = set()
    for fav in crud_favorite_meal.list_active(t.db, t.user.id):
        stars.add(crud_favorite_meal.dish_of(t.db, fav).id)
    t.db.commit()
    return stars


def _recent_first(dishes: list, eaten: dict) -> list:
    def key(dish: PersonalFood) -> tuple:
        latest = eaten.get(dish.id, (0, 0.0))[1]
        return (-latest, -meal_card._stamp(dish.last_used_at), -dish.id)

    return sorted(dishes, key=key)


def _legacy(t: _Tap, dish: PersonalFood) -> tuple:
    """(equal, may_miss): meals with no items that say exactly this dish's name, and
    whether one mentions it among other words.

    Plain columns, and no join on a JSON field: a description is compared in Python.
    """
    has_items = select(MealItem.id).where(MealItem.meal_id == Meal.id).exists()
    rows = t.db.execute(
        select(Meal.description, Meal.meal_time).where(
            Meal.user_id == t.user.id,
            Meal.deleted_at.is_(None),
            Meal.is_draft.is_(False),
            ~has_items,
        )
    )
    names = {
        name
        for name, owner in crud_personal_food.names_of(t.db, t.user.id).items()
        if owner.id == dish.id
    }
    equal, may_miss = [], False
    for description, moment in rows:
        said = _key(description or "")
        if said in names:
            equal.append(moment)
        elif any(name and name in said for name in names):
            may_miss = True
    return equal, may_miss


def _how_often(t: _Tap, dish: PersonalFood, *, together: bool = False) -> str:
    moments = list(
        t.db.scalars(
            select(Meal.meal_time)
            .join(MealItem, MealItem.meal_id == Meal.id)
            .where(
                Meal.user_id == t.user.id,
                Meal.deleted_at.is_(None),
                Meal.is_draft.is_(False),
                MealItem.removed_at.is_(None),
                MealItem.personal_food_id == dish.id,
            )
        )
    )
    equal, may_miss = _legacy(t, dish)
    moments += equal
    last = ""
    if moments:
        latest = max(moments, key=meal_card._stamp)
        day = local_time(meal_items._utc(latest), t.zone).date()
        last = stats_screen._card_title(day)
    if t.simple:
        return f"Последний раз {last}." if last else NO_MEALS
    if not moments:
        return EARLY if may_miss else NO_MEALS
    count = len(moments)
    was = "Вместе было" if together else "Было"
    if may_miss:
        return (
            f"{was} {count} {_times(count)} или больше: "
            "не все ранние записи здесь считаются."
        )
    return f"{was} {count} {_times(count)}, последний раз {last}."


def _numbers_block(t: _Tap, dish: PersonalFood) -> list:
    """The numbers' lines: on 100 g, or a portion that has no weight."""
    exact = dish.numbers_quality in meal_items._VERIFIED
    per_100 = meal_items._per_100(dish)
    portion = meal_items.dish_portion(t.db, dish)
    numbers = portion.numbers or {}
    if per_100 is None and portion.grams and numbers.get("calories") is not None:
        per_100 = meal_items._scaled(numbers, 100.0 / portion.grams)
    if per_100 is not None:
        line = meal_card._sum_line(per_100, exact)
        block = [meal_card._blockquote([f"На 100 г: {line}"])]
        return block if exact else [*block, ESTIMATE]
    if numbers.get("calories") is not None:
        kcal = meal_card.num(numbers["calories"], 0)
        return [meal_card._blockquote([f"Цифры на порцию, без веса: ≈ {kcal} ккал."])]
    return [meal_card._blockquote([NO_NUMBERS])]


def _usual_grams(t: _Tap, dish: PersonalFood, open_now: bool) -> Optional[float]:
    if dish.usual_grams and dish.usual_grams_by_user:
        return dish.usual_grams
    return meal_items.dish_portion(t.db, dish).grams if open_now else None


def _asks_portion(t: _Tap, dish: PersonalFood, screen: Screen) -> Screen:
    """The screen, and the dish it leaves wanting a guessed portion, if it does: the
    model is asked once the screen is out, never in the way of it."""
    if not meal_items.wants_suggestion(t.db, dish):
        return screen
    return replace(screen, suggest_dish=dish.id)


def _hide_of(t: _Tap, dish: PersonalFood) -> Optional[RecordEvent]:
    """What hid it, if it is hidden by a button: the way back stays in reach."""
    if dish.hidden_at is None:
        return None
    event = t.db.scalars(
        select(RecordEvent)
        .where(RecordEvent.kind == "food_hide", RecordEvent.food_id == dish.id)
        .order_by(RecordEvent.id.desc())
    ).first()
    if event is None or (event.after or {}).get("hidden_at") is None:
        return None
    return event


# --- screens ----------------------------------------------------------------


def _back_row(back: str) -> list:
    if back.startswith("f"):
        return [_button("⬅️ Избранное", "fav:p:0")]
    if back.startswith("i"):
        item, _, origin = back[1:].partition("-")
        return [_button("⬅️ К блюду в приёме", f"ml:i:{item}:{origin or 'r'}")]
    return [_button(ALL, f"dh:l:{back[1:]}")]


def _to_dish(dish_id: int, back: str) -> list:
    return [_button(TO_DISH, f"dh:o:{dish_id}:{back}")]


def _row_label(dish: PersonalFood, star: bool, count: int, simple: bool) -> str:
    mark = "⭐ " if star else ""
    tail = f" · {count} {_times(count)}" if count and not simple else ""
    room = min(BUTTON_NAME_MAX, ROW_LABEL_MAX - len(mark) - len(tail))
    return f"{mark}{_cut(_capital(dish.canonical_name), room)}{tail}"


def _dish_rows(t: _Tap, dishes: list, eaten: dict, back: str) -> list:
    stars = _stars(t)
    return [
        [
            _button(
                _row_label(
                    dish, dish.id in stars, eaten.get(dish.id, (0,))[0], t.simple
                ),
                f"dh:o:{dish.id}:{back}",
            )
        ]
        for dish in dishes
    ]


def _list(t: _Tap, page: int = 0, notice: Optional[str] = None) -> Screen:
    """O2, O2a, O2h."""
    dishes = crud_personal_food.list_live(t.db, t.user.id) if t.user else []
    if not dishes:
        return _with_notice(_screen([TITLE, EMPTY], []), notice)
    eaten = _eaten(t)
    dishes = _recent_first(dishes, eaten)
    pages, page = _pages(len(dishes), PAGE, page)
    shown = dishes[page * PAGE : (page + 1) * PAGE]
    rows = _dish_rows(t, shown, eaten, f"l{page}")
    pager = [_button("🔍 Найти", "dh:f")]
    if page > 0:
        pager.insert(0, _button("◀️", f"dh:l:{page - 1}"))
    if page < pages - 1:
        pager.append(_button("▶️", f"dh:l:{page + 1}"))
    rows.append(pager)
    head = RECENT_FIRST
    if pages > 1:
        head += f" Страница {page + 1} из {pages}."
    return _with_notice(_screen([TITLE, head], rows), notice)


def open_list(db, telegram_id: int) -> Screen:
    """«🍲 Блюда» in the menu."""
    user = _user(db, telegram_id)
    return _list(_Tap(db, user, telegram_id, datetime.now(UTC)))


def _card(
    t: _Tap,
    dish: PersonalFood,
    back: str,
    *,
    notice: Optional[str] = None,
    undo: Optional[int] = None,
    opened: bool = False,
    together: bool = False,
    toast: Optional[str] = None,
) -> Screen:
    """O3, O3a, O3b, O3c, O3h, and O7a's card."""
    fid = dish.id
    shown = shows_numbers(t.user)
    open_now = (shown or opened) and not t.simple
    lines = [f"<b>🍲 {_esc(_capital(dish.canonical_name))}</b>"]
    grams = None if t.simple else _usual_grams(t, dish, open_now)
    if grams:
        assumed = open_now and meal_items.dish_portion(t.db, dish).assumed
        lines.append(
            favorites.assumed_portion_line(dish, grams)
            if assumed and not dish.usual_grams_by_user
            else f"Обычная порция: {meal_card.num(grams, 0)} г."
        )
    lines.append(_how_often(t, dish, together=together))
    if open_now:
        lines.extend(_numbers_block(t, dish))

    tail = _tail(back)
    star = fid in _stars(t)
    first = [
        _button("🍽 Записать", f"dh:log:{fid}{tail}"),
        _button(
            "✓ ⭐ В избранном" if star else "⭐ В избранное",
            f"dh:fv:{fid}:{0 if star else 1}{tail}",
        ),
    ]
    if undo is None:
        hide = _hide_of(t, dish)
        undo = hide.id if hide else None
    undo_row = [_button(UNDO_LABEL, f"dh:u:{undo}{tail}")] if undo else []
    if t.simple:
        rows = [first, *([undo_row] if undo_row else [])]
    else:
        second = []
        if _has_history(t, dish):
            second.append(_button("🕘 Когда было", f"dh:h:{fid}:0{tail}"))
        if not shown:
            second.append(
                _button(
                    toggle_label(open_now),
                    f"dh:n:{fid}:{0 if open_now else 1}{tail}",
                )
            )
        # A hidden dish's rare verbs only answer with this card, so none is offered.
        third = [] if dish.hidden_at else [_button("Ещё ›", f"dh:mo:{fid}:{back}")]
        if open_now and not dish.hidden_at:
            edit = _button("✏️ Править цифры", f"dh:nm:{fid}{tail}")
            if len(second) < 2:
                second.append(edit)
            else:
                third.insert(0, edit)
        rows = [first, second]
        if undo_row and len(third) == 1:
            rows.append([*third, *undo_row])
        else:
            rows.append(third)
            if undo_row:
                rows.append(undo_row)
        rows = [row for row in rows if row]
    rows.append(_back_row(back))
    screen = _screen(lines, rows, toast=toast)
    return _with_notice(screen, notice)


def _has_history(t: _Tap, dish: PersonalFood) -> bool:
    return t.db.execute(_history_query(t, dish).limit(1)).first() is not None


def _history_query(t: _Tap, dish: PersonalFood):
    return (
        select(MealItem, Meal)
        .join(Meal, Meal.id == MealItem.meal_id)
        .where(
            Meal.user_id == t.user.id,
            Meal.deleted_at.is_(None),
            Meal.is_draft.is_(False),
            MealItem.removed_at.is_(None),
            MealItem.personal_food_id == dish.id,
        )
        .order_by(Meal.meal_time.desc(), Meal.id.desc(), MealItem.id.desc())
    )


# --- the rows a verb stands on ----------------------------------------------


def _row(t: _Tap, food_id: str, data: str) -> PersonalFood:
    """The tapper's own row, in any state. Foreign or impossible: today's card. Gone:
    the list says so."""
    number = int(food_id)
    if t.user is None or number > _INT_MAX:
        raise _Answer(_unknown(t, data))
    dish = t.db.get(PersonalFood, number)
    if dish is None:
        raise _Answer(_list(t, 0, STALE_PICK))
    if dish.user_id != t.user.id:
        raise _Answer(_unknown(t, data))
    return dish


def _merged_notice(target: PersonalFood) -> str:
    return f"<i>Это блюдо объединено с «{_esc(target.canonical_name)}».</i>"


def _standing(t: _Tap, food_id: str, data: str, back: str) -> PersonalFood:
    """A merged dish answers as the one it went into."""
    dish = _row(t, food_id, data)
    if dish.merged_into_id is not None:
        target = crud_personal_food.live_of(t.db, dish)
        raise _Answer(_card(t, target, back, notice=_merged_notice(target)))
    return dish


def _shown_in_lists(t: _Tap, food_id: str, data: str, back: str) -> PersonalFood:
    """The rare verbs stay off a hidden dish: its card has the way back."""
    dish = _standing(t, food_id, data, back)
    if dish.hidden_at is not None:
        raise _Answer(_card(t, dish, back))
    return dish


def _mergeable(t: _Tap, food_id: str, data: str, back: str) -> PersonalFood:
    dish = _row(t, food_id, data)
    if dish.merged_into_id is not None or dish.hidden_at is not None:
        live = crud_personal_food.live_of(t.db, dish)
        raise _Answer(_card(t, live, back, notice=MERGED_ALREADY))
    return dish


# --- the list and the search ------------------------------------------------


def _v_list(t: _Tap, data: str, page: str) -> Screen:
    return _list(t, int(page))


def _ask_find(t: _Tap, kind: str, back: str, food_id: Optional[int], rows: list):
    _open_slot(t, kind, back, food_id=food_id)
    return _screen([FIND_TITLE, FIND_ASK], rows)


def _v_find(t: _Tap, data: str) -> Screen:
    if t.user is None:
        return _unknown(t, data)
    return _ask_find(t, DISH_FIND, DEFAULT_BACK, None, [[_button(ALL, "dh:l:0")]])


def _found(t: _Tap, text: str, without: Optional[int] = None) -> list:
    """Any name a dish answers to, a merged dish's too. A hidden one stays out: a search
    is a list, and only typing it as food brings it back."""
    needle = " ".join(text.split()).casefold()
    answering = crud_personal_food.names_of(t.db, t.user.id)
    live = {dish.id for dish in crud_personal_food.list_live(t.db, t.user.id)}
    dishes = {
        dish.id: dish
        for name, dish in answering.items()
        if needle in name.casefold() and dish.id in live and dish.id != without
    }
    return _recent_first(list(dishes.values()), _eaten(t))


def _missed(t: _Tap, ask: dict, text: str, rows: list) -> Screen:
    """O2d: the slot stays, since the next word is still an answer."""
    _open_slot(t, ask["kind"], ask.get("back"), food_id=ask.get("food_id"))
    said = _esc(" ".join(text.split()))
    return _screen([FIND_TITLE, f"По «{said}» ничего не нашлось. Напиши иначе."], rows)


def _more_found(t: _Tap, ask: dict, found: list) -> list:
    """Lines under a search cut to its rows. The slot stays: a narrower word is next."""
    if len(found) <= PAGE:
        return []
    _open_slot(t, ask["kind"], ask.get("back"), food_id=ask.get("food_id"))
    return [FOUND_MORE.format(shown=PAGE, found=len(found))]


def _typed_find(t: _Tap, ask: dict, text: str) -> Screen:
    found = _found(t, text)
    back = [[_button(ALL, "dh:l:0")]]
    if not found:
        return _missed(t, ask, text, back)
    said = _esc(" ".join(text.split()))
    rows = _dish_rows(t, found[:PAGE], _eaten(t), DEFAULT_BACK)
    lines = [TITLE, f"Нашлось по «{said}»:", *_more_found(t, ask, found)]
    return _screen(lines, [*rows, *back])


# --- the card and its small verbs -------------------------------------------


def _v_open(t: _Tap, data: str, fid: str, back: str) -> Screen:
    return _card(t, _standing(t, fid, data, back), back)


def _v_numbers(t: _Tap, data: str, fid: str, want: str, back: Optional[str]) -> Screen:
    back = back or DEFAULT_BACK
    return _card(t, _standing(t, fid, data, back), back, opened=want == "1")


def _v_more(t: _Tap, data: str, fid: str, back: str) -> Screen:
    """O14."""
    dish = _shown_in_lists(t, fid, data, back)
    tail = _tail(back)
    rows = [
        [
            _button("✏️ Название", f"dh:rn:{dish.id}{tail}"),
            _button("🔗 Объединить", f"dh:jl:{dish.id}:0{tail}"),
        ],
        [
            _button("⚖️ Порция", f"dh:pg:{dish.id}{tail}"),
            _button("🏷 Группы", f"dh:gr:{dish.id}{tail}"),
        ],
        [_button("🗑 Из списков", f"dh:x:{dish.id}{tail}")],
        _to_dish(dish.id, back),
    ]
    return _screen(
        [
            f"<b>🍲 Ещё: {_esc(dish.canonical_name)}</b>",
            "🗑 Из списков: блюда не будет в «Блюдах» и при выборе блюда, "
            "прошлые приёмы останутся.",
        ],
        rows,
    )


def _v_star(t: _Tap, data: str, fid: str, want: str, back: Optional[str]) -> Screen:
    back = back or DEFAULT_BACK
    dish = _standing(t, fid, data, back)
    _stars(t)  # links an old row, so the check below sees it
    existing = crud_favorite_meal.active_of_dish(t.db, dish.id)
    if (want == "1") == (existing is not None):
        return Screen("", None, toast=meal_items.SAME_TOAST)
    if want == "0":
        crud_favorite_meal.set_removed(t.db, existing, True)
        return _card(t, dish, back, toast=STAR_OFF_TOAST)
    portion = meal_items.dish_portion(t.db, dish)
    numbers = portion.numbers or {key: None for key in meal_items.FIELDS}
    crud_favorite_meal.create_from_numbers(
        t.db,
        user_id=t.user.id,
        name=dish.canonical_name,
        numbers=numbers,
        nutrients={
            "foods": [dish.canonical_name],
            "calories": numbers["calories"],
            "protein": numbers["proteins"],
            "fats": numbers["fats"],
            "carbs": numbers["carbohydrates"],
        },
        source_meal_id=dish.meal_id,
        usual_grams=None if portion.assumed else portion.grams,
    )
    return _card(t, dish, back)


# --- O6 when it was ---------------------------------------------------------


def _v_history(t: _Tap, data: str, fid: str, page: str, back: Optional[str]) -> Screen:
    back = back or DEFAULT_BACK
    dish = _standing(t, fid, data, back)
    rows_of = t.db.execute(_history_query(t, dish)).all()
    if not rows_of:
        return _card(t, dish, back)
    pages, current = _pages(len(rows_of), HISTORY_PAGE, int(page))
    shown = rows_of[current * HISTORY_PAGE : (current + 1) * HISTORY_PAGE]
    lines = [f"<b>🕘 {_esc(_capital(dish.canonical_name))}</b>"]
    chips, seen = [], {}
    for item, meal in shown:
        lines.append(_history_line(item, meal, t))
        day = meal_card.meal_day(meal, t.user)
        label = f"{day.day} {stats_screen._MONTHS[day.month - 1]}"
        seen[label] = seen.get(label, 0) + 1
        if seen[label] > 1:
            label = f"{label} · {seen[label]}"
        chips.append(_button(label, f"ml:o:{meal.id}:h{dish.id}"))
    equal, may_miss = _legacy(t, dish)
    if equal or may_miss:
        lines.append(EARLY)
    rows = [chips[i : i + CHIPS_ROW] for i in range(0, len(chips), CHIPS_ROW)]
    if pages > 1:
        tail = _tail(back)
        pager = [_button(f"{current + 1}/{pages}", NOP)]
        if current > 0:
            pager.insert(0, _button("◀️", f"dh:h:{dish.id}:{current - 1}{tail}"))
        if current < pages - 1:
            pager.append(_button("▶️", f"dh:h:{dish.id}:{current + 1}{tail}"))
        rows.append(pager)
    rows.append(_to_dish(dish.id, back))
    return _screen(lines, rows)


def _history_line(item: MealItem, meal: Meal, t: _Tap) -> str:
    """The day, the time or that there is none, and how much only if it was not one."""
    if meal.time_precision == meal_card.DAY_ONLY:
        day = meal_card.meal_day(meal, t.user)
        when = f"{stats_screen._card_title(day)}, без времени"
    else:
        when = _when(meal, t.zone)
    much = meal_card._PORTION.get(item.factor)
    return f"{when} · {much}" if much else when


# --- O10 logged from the dish -----------------------------------------------


def _log(t: _Tap, dish: PersonalFood) -> Meal:
    """One item, no model, and the source ``fav:u`` asks for. The ⭐ holds the portion
    when there is one, as a favourite logged from its own list."""
    fav = crud_favorite_meal.active_of_dish(t.db, dish.id)
    if fav is None:
        meal = meal_items.save_exact(t.db, t.user.id, [dish], meal_time=t.now)
        meal.resolution_source = FAVORITE_SOURCE
        t.db.commit()
        return meal
    fields = meal_items.star_meal_fields(t.db, dish, fav)
    grams = fields.pop("grams")
    assumed = fields.pop("assumed")
    meal = meal_items.save_meal(
        t.db,
        MealCreate(
            description=dish.canonical_name,
            meal_time=t.now,
            **fields,
            resolution_source=FAVORITE_SOURCE,
            resolution_signals={
                "favorite_id": fav.id,
                "portion_grams": grams,
                **({"portion_assumed": True} if assumed else {}),
            },
        ),
        t.user.id,
        names=[dish.canonical_name],
        numbers_source="dish",
    )
    crud_favorite_meal.mark_used(t.db, fav)
    return meal


def _v_log(t: _Tap, data: str, fid: str, back: Optional[str]) -> Screen:
    back = back or DEFAULT_BACK
    dish = _standing(t, fid, data, back)
    if favorites.just_logged(t.db, t.user.id, t.message_id, dish.id, t.now):
        return Screen("", None, toast=LOG_AGAIN_TOAST)
    _stars(t)
    meal = _log(t, dish)
    t.db.refresh(dish)
    rows = [
        [
            _button("↩️ Отменить", f"{favorites.CALLBACK_PREFIX}u:{meal.id}"),
            _button("🕐 Время", f"ml:t:{meal.id}:r"),
        ],
        _to_dish(dish.id, back),
    ]
    return Screen(
        favorites.logged_line(
            t.db, dish.canonical_name, meal, dish, shows_numbers(t.user)
        ),
        InlineKeyboardMarkup(rows),
        toast=LOG_TOAST,
        meal_id=meal.id,
    )


# --- O4 the name ------------------------------------------------------------


def _v_rename(t: _Tap, data: str, fid: str, back: Optional[str]) -> Screen:
    back = back or DEFAULT_BACK
    dish = _shown_in_lists(t, fid, data, back)
    _open_slot(t, DISH_NAME, back, food_id=dish.id)
    return _screen(
        [
            f"<b>✏️ Название: {_esc(dish.canonical_name)}</b>",
            "Напиши новое название.",
        ],
        [_to_dish(dish.id, back)],
    )


def _names_state(dish: PersonalFood) -> dict:
    return {"name": dish.canonical_name, "aliases": list(dish.aliases or [])}


def _typed_name(t: _Tap, ask: dict, text: str) -> Optional[Screen]:
    back = ask.get("back") or DEFAULT_BACK
    fid = str(ask.get("food_id"))
    dish = _shown_in_lists(t, fid, f"dh:rn:{fid}", back)
    name = meal_items._clean_name(text)
    if not name:
        return None
    if name == dish.canonical_name:
        return _card(t, dish, back)
    holder = crud_personal_food.holder_of_name(t.db, t.user.id, name, without=dish.id)
    if holder is not None:
        live = crud_personal_food.live_of(t.db, holder)
        if live.id == dish.id:
            return _card(t, dish, back, notice=NAME_IS_THIS_DISH)
        return _taken(dish, live, back)
    before = _names_state(dish)
    crud_personal_food.rename(t.db, dish, name)
    event = crud_record_event.add(
        t.db,
        user_id=t.user.id,
        kind="rename",
        food_id=dish.id,
        before=before,
        after=_names_state(dish),
    )
    t.db.commit()
    notice = f"<i>✓ Теперь: {_esc(name)}.</i>"
    return _asks_portion(t, dish, _card(t, dish, back, notice=notice, undo=event.id))


def _taken(dish: PersonalFood, holder: PersonalFood, back: str) -> Screen:
    """O4a."""
    tail = _tail(back)
    return _screen(
        [
            "<b>✏️ Такое блюдо уже есть</b>",
            f"«{_esc(_capital(holder.canonical_name))}» уже есть в блюдах. "
            "Объединить их? Прошлые приёмы останутся как есть.",
        ],
        [
            [_button("🔗 Объединить", f"dh:j:{dish.id}:{holder.id}{tail}")],
            _to_dish(dish.id, back),
        ],
    )


# --- O5 the numbers ---------------------------------------------------------


def _v_edit_numbers(t: _Tap, data: str, fid: str, back: Optional[str]) -> Screen:
    back = back or DEFAULT_BACK
    dish = _shown_in_lists(t, fid, data, back)
    _open_slot(t, DISH_NUMBERS, back, food_id=dish.id)
    return _screen(
        [
            f"<b>✏️ Цифры: {_esc(dish.canonical_name)}</b>",
            "На 100 г, четыре числа: ккал, белки, жиры, углеводы. "
            "Например: 150 10 8 2",
        ],
        [_to_dish(dish.id, back)],
    )


def parse_numbers(text: str) -> Optional[list]:
    """Four numbers apart by spaces or commas, a comma or a dot in a number. The digits
    are bounded by what the column holds: more is no answer, and not an overflow."""
    tokens = [token.rstrip(",") for token in (text or "").split()]
    if len(tokens) == 1:
        tokens = tokens[0].split(",")
    if len(tokens) != 4 or not all(_DIGITS.fullmatch(token) for token in tokens):
        return None
    return [float(token.replace(",", ".")) for token in tokens]


def _numbers_state(dish: PersonalFood) -> dict:
    state = {}
    for column in _NUMBER_COLUMNS:
        value = getattr(dish, column)
        state[column] = None if value is None else float(value)  # Numeric: Decimal
    for column in _NUMBER_STATE:
        state[column] = getattr(dish, column)
    return state


def _set_numbers_state(dish: PersonalFood, state: dict) -> None:
    for column in (*_NUMBER_COLUMNS, *_NUMBER_STATE):
        setattr(dish, column, state[column])


def _typed_numbers(t: _Tap, ask: dict, text: str) -> Optional[Screen]:
    numbers = parse_numbers(text)
    if numbers is None:
        return None  # a wait never eats food
    back = ask.get("back") or DEFAULT_BACK
    fid = str(ask.get("food_id"))
    dish = _shown_in_lists(t, fid, f"dh:nm:{fid}", back)
    before = _numbers_state(dish)
    after = {
        **dict(zip(_NUMBER_COLUMNS, numbers)),
        "numbers_by_user": True,
        "numbers_quality": "user",
        "numbers_basis": "per_100",
    }
    _set_numbers_state(dish, after)
    event = crud_record_event.add(
        t.db,
        user_id=t.user.id,
        kind="numbers",
        food_id=dish.id,
        before=before,
        after=after,
    )
    t.db.commit()
    return _asks_portion(
        t, dish, _card(t, dish, back, notice=NUMBERS_NOTICE, undo=event.id, opened=True)
    )


# --- O14a the usual portion -------------------------------------------------


def _v_portion(t: _Tap, data: str, fid: str, back: Optional[str]) -> Screen:
    back = back or DEFAULT_BACK
    dish = _shown_in_lists(t, fid, data, back)
    _open_slot(t, DISH_PORTION, back, food_id=dish.id)
    return _screen(
        [
            f"<b>⚖️ Порция: {_esc(dish.canonical_name)}</b>",
            "Напиши граммы или миллилитры числом, например 200.",
        ],
        [_to_dish(dish.id, back)],
    )


def _portion_state(dish: PersonalFood) -> dict:
    return {column: getattr(dish, column) for column in _PORTION_STATE}


def _set_portion_state(dish: PersonalFood, state: dict) -> None:
    for column in _PORTION_STATE:
        setattr(dish, column, state[column])


def _write_portion(t: _Tap, dish: PersonalFood, grams: float) -> Optional[int]:
    """A dish's one number, from either door. The event, or None when it was so."""
    before = _portion_state(dish)
    after = {"usual_grams": grams, "usual_grams_by_user": True}
    if before == after:
        return None
    _set_portion_state(dish, after)
    event = crud_record_event.add(
        t.db,
        user_id=t.user.id,
        kind="portion",
        food_id=dish.id,
        before=before,
        after=after,
    )
    t.db.commit()
    return event.id


def _typed_portion(t: _Tap, ask: dict, text: str) -> Optional[Screen]:
    grams = meal_card.parse_grams(text)
    if grams is None:
        return None  # a wait never eats food
    back = ask.get("back") or DEFAULT_BACK
    fid = str(ask.get("food_id"))
    dish = _shown_in_lists(t, fid, f"dh:pg:{fid}", back)
    event_id = _write_portion(t, dish, grams)
    if event_id is None:
        return _card(t, dish, back, notice=meal_card.NOTHING_SAME)
    return _card(t, dish, back, notice=NUMBERS_NOTICE, undo=event_id)


def _typed_star_portion(t: _Tap, ask: dict, text: str) -> Optional[Screen]:
    """O11a. The dish's number, so the card and the ⭐ cannot disagree. The ⭐'s own
    grams stay: they say what its copy of the numbers is for, and that is how it scales.
    """
    grams = meal_card.parse_grams(text)
    if grams is None:
        return None  # a wait never eats food
    fav = crud_favorite_meal.get_for_user(t.db, int(ask["back"]), t.user.id)
    if fav is None or fav.removed_at is not None:
        return favorites.open_list(t.db, t.telegram_id, 0, favorites.STALE)
    dish = crud_favorite_meal.dish_of(t.db, fav)
    event_id = _write_portion(t, dish, grams)
    if event_id is None:
        t.db.commit()  # dish_of may have linked an old row
        return favorites.item_screen(t, fav, notice=meal_card.NOTHING_SAME)
    return favorites.item_screen(t, fav, notice=NUMBERS_NOTICE, undo=event_id)


# --- O14b the groups --------------------------------------------------------


def _groups_screen(t: _Tap, dish: PersonalFood, back: str) -> Screen:
    """Opening it writes nothing. As on a meal's M14, the same tap again is the way
    back, so there is no ↩️."""
    on = meal_items._groups_of_dish(t.db, dish.id, dish.canonical_name) or []
    tail = _tail(back)
    buttons = [
        _button(
            f"{'✓ ' if key in on else ''}{mark} {label}",
            f"dh:gt:{dish.id}:{key}:{0 if key in on else 1}{tail}",
        )
        for key, (mark, label, _word) in food_groups.GROUPS.items()
    ]
    rows = [buttons[i : i + CHIPS_ROW] for i in range(0, len(buttons), CHIPS_ROW)]
    return _screen(
        [
            f"<b>🏷 Группы: {_esc(dish.canonical_name)}</b>",
            "Это видно только на карточках дня и недели.",
            "Для будущих приёмов. Прошлые остаются как были.",
        ],
        [*rows, _to_dish(dish.id, back)],
    )


def _v_groups(t: _Tap, data: str, fid: str, back: Optional[str]) -> Screen:
    back = back or DEFAULT_BACK
    return _groups_screen(t, _shown_in_lists(t, fid, data, back), back)


def _v_group(
    t: _Tap, data: str, fid: str, key: str, want: str, back: Optional[str]
) -> Screen:
    back = back or DEFAULT_BACK
    if key not in food_groups.GROUPS:
        return _unknown(t, data)
    dish = _shown_in_lists(t, fid, data, back)
    on = set(meal_items._groups_of_dish(t.db, dish.id, dish.canonical_name) or [])
    if (want == "1") == (key in on):
        return Screen("", None, toast=meal_items.SAME_TOAST)
    before = dish.food_groups
    dish.food_groups = food_groups.ordered(on ^ {key})
    crud_record_event.add(
        t.db,
        user_id=t.user.id,
        kind="groups",
        food_id=dish.id,
        before={"food_groups": before},
        after={"food_groups": dish.food_groups},
    )
    t.db.commit()
    return _groups_screen(t, dish, back)


# --- O7 merging -------------------------------------------------------------


def _share_a_word(one: str, other: str) -> bool:
    """«сыром» and «сыр» are one word: a case ending must not hide the likely target.
    Short words («с», «и») say nothing."""
    words = [word for word in one.casefold().split() if len(word) >= 3]
    return any(
        word.startswith(near) or near.startswith(word)
        for word in words
        for near in other.casefold().split()
        if len(near) >= 3
    )


def _targets(t: _Tap, dish: PersonalFood) -> list:
    """A word in common first, then the most recent."""
    others = [
        other
        for other in crud_personal_food.list_live(t.db, t.user.id)
        if other.id != dish.id
    ]
    recent = _recent_first(others, _eaten(t))
    name = dish.canonical_name
    return sorted(recent, key=lambda it: not _share_a_word(name, it.canonical_name))


def _target_rows(dish: PersonalFood, shown: list, back: str) -> list:
    tail = _tail(back)
    chips = [
        _button(
            _cut(_capital(other.canonical_name), PAIR_LABEL_MAX),
            f"dh:j:{dish.id}:{other.id}{tail}",
        )
        for other in shown
    ]
    return [chips[i : i + 2] for i in range(0, len(chips), 2)]


def _merge_title(dish: PersonalFood) -> str:
    return f"<b>🔗 {_esc(_capital(dish.canonical_name))}: с чем объединить?</b>"


def _v_merge_list(t: _Tap, data: str, fid: str, page: str, back: Optional[str]):
    back = back or DEFAULT_BACK
    dish = _mergeable(t, fid, data, back)
    targets = _targets(t, dish)
    pages, current = _pages(len(targets), PAGE, int(page))
    shown = targets[current * PAGE : (current + 1) * PAGE]
    tail = _tail(back)
    pager = [_button("🔍 Найти", f"dh:jf:{dish.id}{tail}")]
    if current > 0:
        pager.insert(0, _button("◀️", f"dh:jl:{dish.id}:{current - 1}{tail}"))
    if current < pages - 1:
        pager.append(_button("▶️", f"dh:jl:{dish.id}:{current + 1}{tail}"))
    keeps = MERGE_KEEPS
    if pages > 1:
        keeps += f" Страница {current + 1} из {pages}."
    return _screen(
        [_merge_title(dish), keeps],
        [*_target_rows(dish, shown, back), pager, _to_dish(dish.id, back)],
    )


def _v_merge_find(t: _Tap, data: str, fid: str, back: Optional[str]) -> Screen:
    back = back or DEFAULT_BACK
    dish = _mergeable(t, fid, data, back)
    return _ask_find(t, MERGE_FIND, back, dish.id, [_to_dish(dish.id, back)])


def _typed_merge_find(t: _Tap, ask: dict, text: str) -> Screen:
    back = ask.get("back") or DEFAULT_BACK
    fid = str(ask.get("food_id"))
    dish = _mergeable(t, fid, f"dh:jf:{fid}", back)
    found = _found(t, text, without=dish.id)
    if not found:
        return _missed(t, ask, text, [_to_dish(dish.id, back)])
    said = _esc(" ".join(text.split()))
    return _screen(
        [_merge_title(dish), f"Нашлось по «{said}»:", *_more_found(t, ask, found)],
        [*_target_rows(dish, found[:PAGE], back), _to_dish(dish.id, back)],
    )


def _merge(
    t: _Tap, source: PersonalFood, target: PersonalFood, undoes: Optional[int] = None
) -> RecordEvent:
    """The source's live items and ⭐ go to the target. Their names, numbers and
    ``original_food_id`` stay: the past is what it was."""
    items = list(
        t.db.scalars(select(MealItem).where(MealItem.personal_food_id == source.id))
    )
    for item in items:
        item.personal_food_id = target.id
    moved_stars, removed_stars = [], []
    keeps_own = crud_favorite_meal.active_of_dish(t.db, target.id) is not None
    for fav in _active_stars(t, source):
        if keeps_own:
            fav.removed_at = t.now
            removed_stars.append(fav.id)
        else:
            fav.personal_food_id = target.id
            moved_stars.append(fav.id)
    target_hidden = target.hidden_at
    target.hidden_at = None
    source.merged_into_id = target.id
    event = crud_record_event.add(
        t.db,
        user_id=t.user.id,
        kind="food_merge",
        food_id=source.id,
        before={
            "merged_into": None,
            "target_hidden": meal_card._stamp(target_hidden) if target_hidden else None,
            "target_used": target.times_used,
        },
        after={
            "merged_into": target.id,
            "items": [item.id for item in items],
            "stars_moved": moved_stars,
            "stars_removed": removed_stars,
        },
        undoes_event_id=undoes,
    )
    t.db.commit()
    return event


def _active_stars(t: _Tap, dish: PersonalFood) -> list:
    return list(
        t.db.scalars(
            select(FavoriteMeal).where(
                FavoriteMeal.personal_food_id == dish.id,
                FavoriteMeal.removed_at.is_(None),
            )
        )
    )


def _merged_screen(
    t: _Tap, source: PersonalFood, target: PersonalFood, event_id: int, back: str
) -> Screen:
    """O7a."""
    notice = (
        f"<i>✓ «{_esc(_capital(source.canonical_name))}» теперь в блюде "
        f"«{_esc(target.canonical_name)}». Прошлые приёмы и их цифры на месте.</i>"
    )
    return _card(t, target, back, notice=notice, undo=event_id, together=True)


def _v_join(t: _Tap, data: str, fid: str, tid: str, back: Optional[str]) -> Screen:
    back = back or DEFAULT_BACK
    source = _row(t, fid, data)
    target = _row(t, tid, data)
    source = _mergeable(t, fid, data, back)
    target = crud_personal_food.live_of(t.db, target)
    if target.id == source.id:
        return _card(t, source, back)
    _stars(t)
    event = _merge(t, source, target)
    return _merged_screen(t, source, target, event.id, back)


def _unmerge(t: _Tap, source: PersonalFood, event: RecordEvent, back: str) -> Screen:
    """Only what this merge moved: what was eaten under the target since stays there."""
    after = event.after or {}
    target = t.db.get(PersonalFood, after.get("merged_into") or 0)
    if source.merged_into_id != after.get("merged_into") or target is None:
        return _refused(t, source, back)
    live = crud_personal_food.live_of(t.db, target)
    moved = after.get("items") or []
    if moved:
        items = t.db.scalars(select(MealItem).where(MealItem.id.in_(moved)))
        for item in items:
            if item.personal_food_id == live.id:
                item.personal_food_id = source.id
    for fav_id in after.get("stars_moved") or []:
        fav = t.db.get(FavoriteMeal, fav_id)
        if fav is not None and fav.personal_food_id == live.id:
            fav.personal_food_id = source.id
    for fav_id in after.get("stars_removed") or []:
        fav = t.db.get(FavoriteMeal, fav_id)
        if fav is not None:
            fav.removed_at = None
    before = event.before or {}
    hidden = before.get("target_hidden")
    # A dish eaten or starred since the merge was asked for again, so it stays.
    in_use = (
        before.get("target_used") != target.times_used
        or crud_favorite_meal.active_of_dish(t.db, target.id) is not None
    )
    if hidden and target.hidden_at is None and not in_use:
        target.hidden_at = datetime.fromtimestamp(hidden, UTC)
    source.merged_into_id = None
    new = crud_record_event.add(
        t.db,
        user_id=t.user.id,
        kind="food_merge",
        food_id=source.id,
        before={"merged_into": target.id},
        after={"merged_into": None},
        undoes_event_id=event.id,
    )
    t.db.commit()
    return _card(t, source, back, notice=UNDONE, undo=new.id)


def _merge_again(
    t: _Tap, source: PersonalFood, event: RecordEvent, back: str
) -> Screen:
    """The undo of an undo."""
    target = t.db.get(PersonalFood, (event.before or {}).get("merged_into") or 0)
    if source.merged_into_id is not None or source.hidden_at or target is None:
        return _refused(t, source, back)
    live = crud_personal_food.live_of(t.db, target)
    if live.id == source.id:
        return _refused(t, source, back)
    new = _merge(t, source, live, undoes=event.id)
    return _merged_screen(t, source, live, new.id, back)


# --- O8 out of the lists ----------------------------------------------------


def _hide(t: _Tap, dish: PersonalFood, back: str, undoes: Optional[int] = None):
    """O8. No question first: the undo is the answer to a wrong tap."""
    stars = _active_stars(t, dish)
    for fav in stars:
        fav.removed_at = t.now
    dish.hidden_at = t.now
    ids = [fav.id for fav in stars]
    event = crud_record_event.add(
        t.db,
        user_id=t.user.id,
        kind="food_hide",
        food_id=dish.id,
        before={"hidden_at": None, "favourites": ids},
        after={"hidden_at": meal_card._stamp(t.now), "favourites": ids},
        undoes_event_id=undoes,
    )
    t.db.commit()
    page = back[1:] if back.startswith("l") else "0"
    return _screen(
        [
            f"<b>🗑 Убрано из списков: {_esc(dish.canonical_name)}</b>",
            "Прошлые приёмы на месте. Блюдо вернётся, если снова его записать.",
            "Вернуть можно и позже, пока эта кнопка на экране.",
        ],
        [
            [_button(UNDO_LABEL, f"dh:u:{event.id}{_tail(back)}")],
            [_button(ALL, f"dh:l:{page}")],
        ],
    )


def _v_hide(t: _Tap, data: str, fid: str, back: Optional[str]) -> Screen:
    back = back or DEFAULT_BACK
    dish = _standing(t, fid, data, back)
    if dish.hidden_at is not None:
        return Screen("", None, toast=meal_items.SAME_TOAST)
    _stars(t)
    return _hide(t, dish, back)


def _unhide(t: _Tap, dish: PersonalFood, event: RecordEvent, back: str) -> Screen:
    if dish.hidden_at is None:
        return _card(t, dish, back)  # brought back by eating it: nothing to undo
    after = event.after or {}
    if abs(meal_card._stamp(dish.hidden_at) - after["hidden_at"]) > 0.001:
        return _refused(t, dish, back)
    dish.hidden_at = None
    ids = after.get("favourites") or []
    if crud_favorite_meal.active_of_dish(t.db, dish.id) is None:
        for fav_id in ids:  # one made since is the dish's one star
            fav = t.db.get(FavoriteMeal, fav_id)
            if fav is not None:
                fav.removed_at = None
    new = crud_record_event.add(
        t.db,
        user_id=t.user.id,
        kind="food_hide",
        food_id=dish.id,
        before=after,
        after={"hidden_at": None, "favourites": ids},
        undoes_event_id=event.id,
    )
    t.db.commit()
    return _card(t, dish, back, notice=UNDONE, undo=new.id)


# --- ↩️ Вернуть ---------------------------------------------------------------


def _refused(t: _Tap, dish: PersonalFood, back: str) -> Screen:
    live = crud_personal_food.live_of(t.db, dish)
    return _card(t, live, back, notice=meal_items.UNDO_REFUSED)


def _undo_rename(t: _Tap, dish: PersonalFood, event: RecordEvent, back: str) -> Screen:
    before, after = event.before or {}, event.after or {}
    holder = crud_personal_food.holder_of_name(
        t.db, t.user.id, before.get("name") or "", without=dish.id
    )
    if _names_state(dish) != after or holder is not None:
        return _refused(t, dish, back)
    crud_personal_food.forget_suggestion(dish, dish.canonical_name, before["name"])
    dish.canonical_name = before["name"]
    dish.aliases = list(before.get("aliases") or [])
    new = crud_record_event.add(
        t.db,
        user_id=t.user.id,
        kind="rename",
        food_id=dish.id,
        before=after,
        after=before,
        undoes_event_id=event.id,
    )
    t.db.commit()
    return _asks_portion(t, dish, _card(t, dish, back, notice=UNDONE, undo=new.id))


def _undo_numbers(t: _Tap, dish: PersonalFood, event: RecordEvent, back: str) -> Screen:
    before, after = event.before or {}, event.after or {}
    if _numbers_state(dish) != after:
        return _refused(t, dish, back)
    _set_numbers_state(dish, before)
    new = crud_record_event.add(
        t.db,
        user_id=t.user.id,
        kind="numbers",
        food_id=dish.id,
        before=after,
        after=before,
        undoes_event_id=event.id,
    )
    t.db.commit()
    return _asks_portion(t, dish, _card(t, dish, back, notice=UNDONE, undo=new.id))


def _undo_portion(t: _Tap, dish: PersonalFood, event: RecordEvent, back: str) -> Screen:
    before, after = event.before or {}, event.after or {}
    if _portion_state(dish) != after:
        return _refused(t, dish, back)
    _set_portion_state(dish, before)
    new = crud_record_event.add(
        t.db,
        user_id=t.user.id,
        kind="portion",
        food_id=dish.id,
        before=after,
        after=before,
        undoes_event_id=event.id,
    )
    t.db.commit()
    return _asks_portion(t, dish, _card(t, dish, back, notice=UNDONE, undo=new.id))


def _v_undo(t: _Tap, data: str, eid: str, back: Optional[str]) -> Screen:
    back = back or DEFAULT_BACK
    event = None if int(eid) > _INT_MAX else t.db.get(RecordEvent, int(eid))
    if (
        event is None
        or t.user is None
        or event.user_id != t.user.id
        or event.kind not in _UNDOABLE
        or event.food_id is None
    ):
        return _unknown(t, data)
    dish = t.db.get(PersonalFood, event.food_id)
    if dish is None:
        return _list(t, 0, STALE_PICK)
    done = t.db.scalars(
        select(RecordEvent.id).where(RecordEvent.undoes_event_id == event.id)
    ).first()
    if done is not None:
        return Screen("", None, toast=meal_items.SAME_TOAST)  # a second tap
    if event.kind == "rename":
        return _undo_rename(t, dish, event, back)
    if event.kind == "numbers":
        return _undo_numbers(t, dish, event, back)
    if event.kind == "portion":
        return _undo_portion(t, dish, event, back)
    if event.kind == "food_merge":
        if (event.after or {}).get("merged_into") is not None:
            return _unmerge(t, dish, event, back)
        return _merge_again(t, dish, event, back)
    if (event.after or {}).get("hidden_at") is not None:
        return _unhide(t, dish, event, back)
    if dish.hidden_at is not None or dish.merged_into_id is not None:
        return _refused(t, dish, back)
    return _hide(t, dish, back, undoes=event.id)


# --- routing ----------------------------------------------------------------

_ID = r"(\d{1,18})"
_PAGE = r"(\d{1,4})"
_B = r"(l\d{1,4}|f\d{1,18}|i\d{1,18}(?:-(?:t|y|w|h\d{1,18}))?)"
_OPT = rf"(?::{_B})?"
_VERBS: tuple = (
    (re.compile(rf"dh:l:{_PAGE}"), _v_list),
    (re.compile(r"dh:f"), _v_find),
    (re.compile(rf"dh:o:{_ID}:{_B}"), _v_open),
    (re.compile(rf"dh:mo:{_ID}:{_B}"), _v_more),
    (re.compile(rf"dh:rn:{_ID}{_OPT}"), _v_rename),
    (re.compile(rf"dh:nm:{_ID}{_OPT}"), _v_edit_numbers),
    (re.compile(rf"dh:pg:{_ID}{_OPT}"), _v_portion),
    (re.compile(rf"dh:gr:{_ID}{_OPT}"), _v_groups),
    (re.compile(rf"dh:gt:{_ID}:([a-z0-9]{{1,8}}):([01]){_OPT}"), _v_group),
    (re.compile(rf"dh:n:{_ID}:([01]){_OPT}"), _v_numbers),
    (re.compile(rf"dh:h:{_ID}:{_PAGE}{_OPT}"), _v_history),
    (re.compile(rf"dh:fv:{_ID}:([01]){_OPT}"), _v_star),
    (re.compile(rf"dh:log:{_ID}{_OPT}"), _v_log),
    (re.compile(rf"dh:x:{_ID}{_OPT}"), _v_hide),
    (re.compile(rf"dh:u:{_ID}{_OPT}"), _v_undo),
    (re.compile(rf"dh:jl:{_ID}:{_PAGE}{_OPT}"), _v_merge_list),
    (re.compile(rf"dh:jf:{_ID}{_OPT}"), _v_merge_find),
    (re.compile(rf"dh:j:{_ID}:{_ID}{_OPT}"), _v_join),
)

meal_card._TYPED[DISH_NAME] = _typed_name
meal_card._TYPED[DISH_NUMBERS] = _typed_numbers
meal_card._TYPED[DISH_PORTION] = _typed_portion
meal_card._TYPED[favorites.PORTION] = _typed_star_portion
meal_card._TYPED[DISH_FIND] = _typed_find
meal_card._TYPED[MERGE_FIND] = _typed_merge_find


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
