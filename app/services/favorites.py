"""Favorites: save a confirmed meal, then log it again with one tap.

The flow (Julia's pick, 2026-10-27 — «тап — и записано»):

    ⭐ Избранное → [Самса веган · 420 ккал] → Когда? [Сейчас] [Завтрак] [Обед] [Ужин]
    → ✅ Записано … [↩️ Отменить]

Every step edits the same message (no chat clutter) and is driven by inline
buttons whose callback data carries everything the step needs, so a tap works
after a bot restart too. Logging copies the stored numbers: no OpenAI call.
Undo instead of "are you sure?" (sweet-home message design, P6).

This module is Telegram-free apart from building the keyboards: ``handle_callback``
takes the callback data and the tapping user's id and returns the screen, so the
whole flow is testable without a bot.
"""

from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.crud.crud_favorite_meal import crud_favorite_meal
from app.crud.crud_meal import crud_meal
from app.models.favorite_meal import FavoriteMeal
from app.models.meal import Meal
from app.models.user import User
from app.schemas.meal import MealCreate

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "fav:"
PAGE_SIZE = 4
NAME_MAX = 60
BUTTON_NAME_MAX = 30
# meals.resolution_source for a meal logged from a favorite.
FAVORITE_SOURCE = "favorite"

# Chip code → meals.meal_type. "Сейчас" stores no label, as in the typed flow.
SLOTS = {"n": None, "b": "Завтрак", "l": "Обед", "d": "Ужин"}
_SLOT_CHIPS = (("n", "Сейчас"), ("b", "Завтрак"), ("l", "Обед"), ("d", "Ужин"))

_HEADER = "<b>⭐ Избранное</b>"
_STALE = "<i>Этого блюда уже нет в избранном. Вот актуальный список.</i>"


@dataclass
class Screen:
    text: str  # HTML
    markup: InlineKeyboardMarkup | None
    toast: str | None = None  # answerCallbackQuery text, seen only by the tapper


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


def _kcal(value) -> str:
    try:
        return f"{round(float(value))} ккал"
    except (TypeError, ValueError):
        return "? ккал"


def macro_line(fav: FavoriteMeal) -> str:
    parts = [f"🔥 {_kcal(fav.calories)}"]
    for label, value in (
        ("Б", fav.proteins),
        ("Ж", fav.fats),
        ("У", fav.carbohydrates),
    ):
        shown = _num(value)
        if shown is not None:
            parts.append(f"{label} {shown}")
    return " · ".join(parts)


def _button_label(fav: FavoriteMeal) -> str:
    name = fav.name
    if len(name) > BUTTON_NAME_MAX:
        name = name[: BUTTON_NAME_MAX - 1].rstrip() + "…"
    return f"{name} · {_kcal(fav.calories)}"


def _btn(text: str, *parts) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text, callback_data=CALLBACK_PREFIX + ":".join(map(str, parts))
    )


# --- screens ----------------------------------------------------------------


def list_screen(
    favs: list[FavoriteMeal], page: int, notice: str | None = None
) -> Screen:
    head = _HEADER + (f"\n{notice}" if notice else "")
    if not favs:
        return Screen(
            head + "\n\nПока пусто. Блюдо попадает сюда кнопкой "
            "«⭐ Да, и в избранное» после анализа еды.",
            None,
        )
    pages = (len(favs) + PAGE_SIZE - 1) // PAGE_SIZE
    page = min(max(page, 0), pages - 1)
    shown = favs[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    rows = [[_btn(_button_label(f), "o", f.id, page)] for f in shown]
    if pages > 1:
        pager = []
        if page > 0:
            pager.append(_btn("◀️", "p", page - 1))
        pager.append(_btn(f"{page + 1}/{pages}", "x"))
        if page < pages - 1:
            pager.append(_btn("▶️", "p", page + 1))
        rows.append(pager)
    return Screen(
        head + "\n\nЧто записать? Без анализа, сразу.", InlineKeyboardMarkup(rows)
    )


def item_screen(fav: FavoriteMeal, page: int, notice: str | None = None) -> Screen:
    text = f"<b>⭐ {html.escape(fav.name)}</b>\n{macro_line(fav)}"
    if notice:
        text += f"\n{notice}"
    text += "\n\nКогда?"
    rows = [
        [_btn(label, "l", fav.id, code) for code, label in _SLOT_CHIPS],
        [_btn("⬅️ Назад", "p", page), _btn("✖️ Убрать", "r", fav.id, page)],
    ]
    return Screen(text, InlineKeyboardMarkup(rows))


def logged_screen(fav: FavoriteMeal, meal: Meal) -> Screen:
    text = f"✅ <b>Записано:</b> {html.escape(fav.name)} · {_kcal(fav.calories)}"
    if meal.meal_type:
        text += f"\n🕐 {meal.meal_type}"
    return Screen(
        text, InlineKeyboardMarkup([[_btn("↩️ Отменить", "u", meal.id)]]), "Записано"
    )


def _back_to_list_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[_btn("⬅️ К избранному", "p", 0)]])


# --- operations ---------------------------------------------------------------


def save_from_meal(db, meal: Meal, name: str) -> tuple[FavoriteMeal, bool]:
    """Add the meal to favorites; (favorite, created). A same-named active
    favorite is reused rather than duplicated."""
    existing = crud_favorite_meal.find_active_by_name(db, meal.user_id, name)
    if existing is not None:
        return existing, False
    return crud_favorite_meal.create_from_meal(db, meal, name), True


def _user_id(db, telegram_id: int) -> int | None:
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    return user.id if user else None


def open_list(db, telegram_id: int, page: int = 0, notice: str | None = None) -> Screen:
    user_id = _user_id(db, telegram_id)
    favs = crud_favorite_meal.list_active(db, user_id) if user_id else []
    return list_screen(favs, page, notice)


def _active(db, user_id: int | None, fav_id: int) -> FavoriteMeal | None:
    if user_id is None:
        return None
    fav = crud_favorite_meal.get_for_user(db, fav_id, user_id)
    return fav if fav is not None and fav.removed_at is None else None


def _log(db, user_id: int, fav: FavoriteMeal, slot: str) -> Meal:
    meal_in = MealCreate(
        description=fav.name,
        meal_time=datetime.now(UTC),
        meal_type=SLOTS[slot],
        calories=fav.calories,
        proteins=fav.proteins,
        fats=fav.fats,
        carbohydrates=fav.carbohydrates,
        nutrients=fav.nutrients,
        resolution_source=FAVORITE_SOURCE,
        resolution_signals={"favorite_id": fav.id},
    )
    meal = crud_meal.create(db, meal_in, user_id)
    crud_favorite_meal.mark_used(db, fav)
    return meal


def handle_callback(db, telegram_id: int, data: str) -> Screen:
    """The screen a tap on a favorites button leads to.

    Every id in ``data`` is checked against the tapping user: callback data comes
    from the client, so a crafted one must not reach another user's rows. A stale
    button (the dish was removed, the record already undone) gets the current
    screen with a notice, never an error (sweet-home P5).
    """
    parts = data[len(CALLBACK_PREFIX) :].split(":")
    verb, args = parts[0], parts[1:]
    try:
        nums = [int(a) if a.lstrip("-").isdigit() else a for a in args]
    except ValueError:
        nums = []
    user_id = _user_id(db, telegram_id)

    if verb == "x":
        return Screen("", None)  # the pager's page counter: nothing to do
    if verb == "p" and len(nums) == 1:
        return open_list(db, telegram_id, nums[0])
    if verb == "o" and len(nums) == 2:
        fav = _active(db, user_id, nums[0])
        if fav is None:
            return open_list(db, telegram_id, nums[1], _STALE)
        return item_screen(fav, nums[1])
    if verb == "l" and len(nums) == 2 and nums[1] in SLOTS:
        fav = _active(db, user_id, nums[0])
        if fav is None:
            return open_list(db, telegram_id, 0, _STALE)
        return logged_screen(fav, _log(db, user_id, fav, nums[1]))
    if verb == "u" and len(nums) == 1:
        meal = crud_meal.get(db, nums[0]) if user_id else None
        if (
            meal is None
            or meal.user_id != user_id
            or meal.resolution_source != FAVORITE_SOURCE
        ):
            return Screen("<i>Эта запись уже отменена.</i>", _back_to_list_markup())
        name = meal.description or "Блюдо"
        crud_meal.remove(db, meal.id)
        return Screen(
            f"↩️ Отменено: {html.escape(name)} не записано.",
            _back_to_list_markup(),
            "Отменено",
        )
    if verb == "r" and len(nums) == 2:
        fav = _active(db, user_id, nums[0])
        if fav is None:
            return open_list(db, telegram_id, nums[1], _STALE)
        crud_favorite_meal.set_removed(db, fav, True)
        rows = [
            [_btn("↩️ Вернуть", "b", fav.id, nums[1])],
            [_btn("⬅️ К избранному", "p", nums[1])],
        ]
        return Screen(
            f"✖️ «{html.escape(fav.name)}» убрано из избранного.",
            InlineKeyboardMarkup(rows),
        )
    if verb == "b" and len(nums) == 2:
        fav = crud_favorite_meal.get_for_user(db, nums[0], user_id) if user_id else None
        if fav is None:
            return open_list(db, telegram_id, nums[1], _STALE)
        crud_favorite_meal.set_removed(db, fav, False)
        return item_screen(fav, nums[1], "<i>↩️ Снова в избранном.</i>")

    logger.warning("favorites: unknown callback data %r", data)
    return open_list(db, telegram_id, 0)
