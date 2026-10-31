"""🧺 Надёжная еда and ✋ Не предлагать (stage 5, slice G, G5-R12, G5-R13).

    ⚙️ Настройки → <b>🧺 Надёжная еда</b>: the list, [➕ Добавить] [➖ Убрать]
    ➖ → <b>🧺 Что убрать?</b>: one button each → the list, [↩️ Вернуть «…»]
    ⚙️ Настройки → <b>✋ Не предлагать</b>: the list, [✖️ …] each; text adds to it

The reliable list seeds itself from ⭐ favourites and meals repeated in the records;
ready-made food counts like any other, and a repeat is never a variety problem. What
the user removes is remembered and not seeded again, until it is brought back or
typed. Both lists are plain names in ``User.settings``, which ``next_step`` reads.
A removal applies at once and can be undone, and it is no question and no reason.
Callback data carries a hash of the name, never the name. Telegram-free apart from
the keyboard, like ``settings_screen``.
"""

from __future__ import annotations

import hashlib
import html
import logging
import re
from collections import Counter
from datetime import UTC, datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.crud.crud_favorite_meal import crud_favorite_meal
from app.crud.crud_meal import crud_meal
from app.crud.crud_meal_item import crud_meal_item
from app.models.user import User
from app.services.favorites import Screen
from app.services.next_step import NO_SUGGEST, RELIABLE
from app.services.week_card import _key

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "fl:"
NAME_MAX = 40
MAX_FOODS = 12
REPEATED_AT_LEAST = 3  # the project's guess at «often»; no study gives a number
SEED_DAYS = 30

RELIABLE_OPEN = CALLBACK_PREFIX + "rel"
ADD = CALLBACK_PREFIX + "add"
REMOVE_OPEN = CALLBACK_PREFIX + "rm"
NEVER_OPEN = CALLBACK_PREFIX + "nev"
_REMOVE, _RESTORE = "rx", "ru"
_NEVER_REMOVE, _NEVER_RESTORE = "nx", "nu"
# These leave a screen that takes the next text message.
ASK_PATTERN = f"^{CALLBACK_PREFIX}(add|n.*)$"
_SETTINGS = "set:open"

_OUT = {RELIABLE: "reliable_out", NO_SUGGEST: "no_suggest_out"}
# «Лаваш с сыром и зеленью» is one dish; «грибы и молоко» are two refusals, and as one
# entry «молоко» would only count next to «грибы».
_SPLIT = {RELIABLE: r"[;\n]+", NO_SUGGEST: r"[,;\n]+|\s+и\s+"}
_GONE = "Этого в списке уже нет"


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)


def _user(db, telegram_id: int) -> User | None:
    return db.query(User).filter(User.telegram_id == telegram_id).first()


def _names(user: User | None, key: str) -> list[str]:
    value = (user.settings or {}).get(key) if user else None
    if value is None:
        return []
    if not isinstance(value, list):
        logger.warning("food lists: expected a list at %s", key)
        return []
    return [entry for entry in value if isinstance(entry, str)]


def _save(db, telegram_id: int, **values) -> None:
    user = _user(db, telegram_id)
    if user is None:
        user = User(telegram_id=telegram_id)
        db.add(user)
    # A new dict: SQLAlchemy does not see a JSON value changed in place.
    user.settings = {**(user.settings or {}), **values}
    db.commit()


def _token(name: str) -> str:
    return hashlib.sha256(_key(name).encode()).hexdigest()[:8]


def _find(names: list[str], token: str) -> str | None:
    return next((name for name in names if _token(name) == token), None)


def _clean(text: str) -> str:
    return " ".join(text.split())


def _fits(name: str) -> bool:
    return 0 < len(name) <= NAME_MAX and bool(re.search(r"\w", name))


def counts(user: User | None) -> tuple[int, int]:
    return len(_names(user, RELIABLE)), len(_names(user, NO_SUGGEST))


def _written(db, meals: list) -> list:
    """Each meal's names, once per meal: its live dishes, or the description of a
    meal that has none. A blank one names nothing."""
    dishes = crud_meal_item.live_names(db, [meal.id for meal in meals])
    written = []
    for meal in meals:
        once = {}
        for name in dishes.get(meal.id) or [meal.description or ""]:
            name = _clean(name)
            if name:
                once.setdefault(_key(name), name)
        written += once.values()
    return written


def seed(db, user: User, now: datetime | None = None) -> None:
    """Favourites first, then what repeats in the records, up to ``MAX_FOODS``. What
    is on the list stays, whatever happens to its favourite: the user edits it here."""
    now = now or datetime.now(UTC)
    meals = crud_meal.get_user_meals_between(
        db, user.id, now - timedelta(days=SEED_DAYS), now
    )
    written = _written(db, meals)
    repeats = Counter(_key(name) for name in written)
    spelled = {_key(name): name for name in reversed(written)}
    found = [fav.name for fav in crud_favorite_meal.list_active(db, user.id)]
    found += [
        spelled[key]
        for key, times in repeats.most_common()
        if times >= REPEATED_AT_LEAST
    ]
    listed = _names(user, RELIABLE)
    closed = {_key(name) for name in [*listed, *_names(user, _OUT[RELIABLE])]}
    added = list(listed)
    for name in found:
        if len(added) >= MAX_FOODS:
            break
        if _key(name) not in closed and _fits(name):
            closed.add(_key(name))
            added.append(name)
    if added != listed:
        # A new dict: SQLAlchemy does not see a JSON value changed in place.
        user.settings = {**(user.settings or {}), RELIABLE: added}
        db.commit()


# --- screens ----------------------------------------------------------------


def _rows(buttons: list) -> list:
    """Telegram cuts a label with «…» at about 20 characters with two in a row."""
    longest = max((len(b.text) for b in buttons), default=0)
    width = 2 if longest <= 20 else 1
    return [buttons[i : i + width] for i in range(0, len(buttons), width)]


def reliable_screen(
    user: User | None, notice: str | None = None, undo: str | None = None
) -> Screen:
    names = _names(user, RELIABLE)
    lines = [f"<i>{notice}</i>", ""] if notice else []
    lines += ["<b>🧺 Надёжная еда</b>", ""]
    lines += [
        "То, что выручает, когда сил мало: в режимах «мало сил» и «минимум сил» шаг "
        "сначала ищется среди этой еды.",
        "",
    ]
    if names:
        lines += [html.escape(name) for name in names]
        lines += ["", "<i>Собрано из ⭐ Избранного и частых записей.</i>"]
    else:
        lines.append(
            "Пока пусто. Сюда попадёт ⭐ Избранное и то, что часто есть в записях."
        )
    rows = []
    if undo:
        restore = f"{CALLBACK_PREFIX}{_RESTORE}:{_token(undo)}"
        rows.append([_btn(f"↩️ Вернуть «{undo}»", restore)])
    actions = []
    if len(names) < MAX_FOODS:
        actions.append(_btn("➕ Добавить", ADD))
    if names:
        actions.append(_btn("➖ Убрать", REMOVE_OPEN))
    if actions:
        rows.append(actions)
    rows.append([_btn("⬅️ Настройки", _SETTINGS)])
    return Screen("\n".join(lines), InlineKeyboardMarkup(rows))


def _add_screen() -> Screen:
    return Screen(
        "<b>🧺 Добавить в надёжную еду</b>\n\n"
        "Напиши одним сообщением, например «лаваш с сыром».",
        InlineKeyboardMarkup([[_btn("⬅️ Надёжная еда", RELIABLE_OPEN)]]),
    )


def _remove_screen(user: User | None) -> Screen:
    buttons = [
        _btn(name, f"{CALLBACK_PREFIX}{_REMOVE}:{_token(name)}")
        for name in _names(user, RELIABLE)
    ]
    rows = [*_rows(buttons), [_btn("⬅️ Надёжная еда", RELIABLE_OPEN)]]
    return Screen(
        "<b>🧺 Что убрать?</b>\n\nНажми на еду — она уйдёт из списка, вернуть можно "
        "сразу.",
        InlineKeyboardMarkup(rows),
    )


def never_screen(
    user: User | None, notice: str | None = None, undo: str | None = None
) -> Screen:
    names = _names(user, NO_SUGGEST)
    lines = [f"<i>{notice}</i>", ""] if notice else []
    lines += ["<b>✋ Не предлагать</b>", ""]
    if names:
        lines += [
            "Этого не будет в шагах:",
            "",
            " · ".join(html.escape(name) for name in names),
        ]
    else:
        lines.append("Пока пусто.")
    lines.append("")
    if len(names) < MAX_FOODS:
        lines.append("Чтобы добавить — напиши одним сообщением, например «рыба».")
    else:
        lines.append(f"В списке уже {MAX_FOODS}: чтобы добавить, сначала убери одно.")
    rows = []
    if undo:
        restore = f"{CALLBACK_PREFIX}{_NEVER_RESTORE}:{_token(undo)}"
        rows.append([_btn(f"↩️ Вернуть «{undo}»", restore)])
    buttons = [
        _btn(f"✖️ {name}", f"{CALLBACK_PREFIX}{_NEVER_REMOVE}:{_token(name)}")
        for name in names
    ]
    rows += _rows(buttons)
    rows.append([_btn("⬅️ Настройки", _SETTINGS)])
    return Screen("\n".join(lines), InlineKeyboardMarkup(rows))


_SCREENS = {RELIABLE: reliable_screen, NO_SUGGEST: never_screen}


# --- operations -------------------------------------------------------------


def _remove(db, telegram_id: int, kind: str, token: str) -> Screen:
    user = _user(db, telegram_id)
    names, out = _names(user, kind), _names(user, _OUT[kind])
    show = _SCREENS[kind]
    name = _find(names, token)
    if name is None:
        # A double tap: the second one must not take the undo button away.
        name = _find(out, token)
        if name is None:
            screen = show(user)
            screen.toast = _GONE
            return screen
    else:
        _save(
            db,
            telegram_id,
            **{
                kind: [n for n in names if n != name],
                _OUT[kind]: [*out, name],
            },
        )
    return show(_user(db, telegram_id), f"Убрано: {html.escape(name)}", undo=name)


def _restore(db, telegram_id: int, kind: str, token: str) -> Screen:
    user = _user(db, telegram_id)
    names, out = _names(user, kind), _names(user, _OUT[kind])
    show = _SCREENS[kind]
    name = _find(out, token)
    if name is None:
        # A double tap, or an old button: what is on the list stays as it is.
        screen = show(user)
        screen.toast = None if _find(names, token) else _GONE
        return screen
    if len(names) >= MAX_FOODS:
        screen = show(user)
        screen.toast = f"В списке уже {MAX_FOODS}: сначала убери одно"
        return screen
    _save(
        db,
        telegram_id,
        **{kind: [*names, name], _OUT[kind]: [n for n in out if n != name]},
    )
    return show(_user(db, telegram_id), f"✓ Вернулось: {html.escape(name)}")


def _tail(data: str) -> tuple[str, str]:
    verb, _, token = data[len(CALLBACK_PREFIX) :].partition(":")
    return verb, token


def ask_food(
    db, telegram_id: int, data: str, now: datetime | None = None
) -> tuple[Screen, str, bool]:
    """A tap that may leave a screen taking text → (the screen, which list, wait?)."""
    if data == ADD:
        user = _user(db, telegram_id)
        if len(_names(user, RELIABLE)) >= MAX_FOODS:
            screen = reliable_screen(user)
            screen.toast = f"В списке уже {MAX_FOODS}: сначала убери одно"
            return screen, RELIABLE, False
        return _add_screen(), RELIABLE, True
    verb, token = _tail(data)
    if verb == _NEVER_REMOVE:
        screen = _remove(db, telegram_id, NO_SUGGEST, token)
    elif verb == _NEVER_RESTORE:
        screen = _restore(db, telegram_id, NO_SUGGEST, token)
    else:
        screen = never_screen(_user(db, telegram_id))
    return screen, NO_SUGGEST, True


def submit_food(
    db, telegram_id: int, kind: str, text: str | None
) -> tuple[Screen, bool]:
    """Typed text → (the next screen, keep waiting for text?). Nothing is saved from
    a message that does not fit, so it is simply written again."""
    # Here: weight_screen imports stats_screen, which imports this module.
    from app.services.weight_screen import KG_PATTERN

    typed = (text or "").strip()
    if KG_PATTERN.match(typed):
        # A bare number is a weigh-in everywhere else, and it must stay one.
        return Screen("Это похоже на вес. Напиши название еды.", None), True
    entries = [_clean(part) for part in re.split(_SPLIT[kind], typed)]
    entries = [entry for entry in entries if entry]
    if not entries or not all(_fits(entry) for entry in entries):
        return (
            Screen(f"Название еды — до {NAME_MAX} знаков. Напиши другое.", None),
            True,
        )
    user = _user(db, telegram_id)
    names, out = _names(user, kind), _names(user, _OUT[kind])
    show = _SCREENS[kind]
    keep_waiting = kind == NO_SUGGEST
    have = {_key(name) for name in names}
    fresh = []
    for entry in entries:
        if _key(entry) not in have:
            have.add(_key(entry))
            fresh.append(entry)
    if not fresh:
        return show(user, "Уже в списке"), keep_waiting
    if len(names) + len(fresh) > MAX_FOODS:
        notice = f"В списке уже {len(names)} из {MAX_FOODS}: сначала убери что-то"
        if len(names) >= MAX_FOODS:
            notice = f"В списке уже {MAX_FOODS}: сначала убери одно"
        return show(user, notice), keep_waiting
    typed_keys = {_key(entry) for entry in fresh}
    _save(
        db,
        telegram_id,
        **{
            kind: [*names, *fresh],
            _OUT[kind]: [name for name in out if _key(name) not in typed_keys],
        },
    )
    added = ", ".join(html.escape(entry) for entry in fresh)
    return show(_user(db, telegram_id), f"✓ Добавлено: {added}"), keep_waiting


def handle_callback(
    db, telegram_id: int, data: str, now: datetime | None = None
) -> Screen:
    user = _user(db, telegram_id)
    if data == ADD or data.startswith(CALLBACK_PREFIX + "n"):
        # Only reaches here from a step that keeps its state: a meal being entered.
        screen = reliable_screen(user)
        screen.toast = "Сначала заверши текущий шаг или нажми /cancel"
        return screen
    verb, token = _tail(data)
    if verb == _REMOVE and token:
        return _remove(db, telegram_id, RELIABLE, token)
    if verb == _RESTORE and token:
        return _restore(db, telegram_id, RELIABLE, token)
    if verb == "rm":
        return _remove_screen(user)
    if verb != "rel":
        logger.warning("food lists: unknown callback data %r", data)
    if user is not None:
        seed(db, user, now)
    return reliable_screen(_user(db, telegram_id))
