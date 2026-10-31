"""What the person sends: the router of section 5.1 and the lines M1-M3.

The decisions and the writes, with no Telegram call: ``telegram.capture_message`` sends
what is decided here and reads in the background. A source and its meal are committed
before any download or model call, so whatever breaks afterwards the record stands.
No text, transcript or message body goes to a log or to ``meal_readings.error``.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Optional

import httpx
import openai
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from telegram import InlineKeyboardMarkup
from telegram.error import BadRequest, NetworkError

from app.crud.crud_meal import crud_meal
from app.crud.crud_meal_item import crud_meal_item
from app.crud.crud_personal_food import crud_personal_food
from app.models.inbound_message import InboundMessage
from app.models.meal import Meal
from app.models.meal_message import MealMessage
from app.models.meal_reading import MealReading
from app.models.meal_source import MealSource
from app.models.user import User
from app.schemas.meal import MealCreate
from app.services import (
    ai_errors,
    food_groups,
    meal_card,
    meal_items,
    stats_screen,
    weight_screen,
)
from app.services.day_card import local_time
from app.services.meal_card import _button, _cut, _what, _when
from app.services.meal_draft import num
from app.services.numbers import shows_numbers
from app.services.settings_screen import user_zone

logger = logging.getLogger(__name__)

# --- the copy (section 5.5) ---------------------------------------------------------

M1_PENDING = "Фото в дневнике, читаю…"
M2_PENDING = "{count} фото в дневнике, читаю…"
M1B_HINT = "На этом всё. Поправить можно кнопками или ответом на это сообщение."
FORWARD_WORDS = "(время пересланного сообщения)"
M3A_NO_FOOD = "Еды здесь не видно, в дневник не записано."
M3B_QUESTION = "Вопросы здесь пока не разбираются, в дневник не записано."
M4_PENDING = "Голосовое в дневнике, слушаю…"
M4D_FAILED = (
    "Голосовое сохранено, разобрать сейчас не вышло.\n"
    "В дневнике запись с этим голосовым."
)
M4F_TOO_BIG = (
    "Файл больше 20 МБ, Telegram такие боту не передаёт. В дневник не записано."
)
M5A_DELETED = "Тот приём был удалён раньше, поэтому это отдельная запись."
M5F_NO_MEAL = (
    "Похоже на время, но не ясно, к какому приёму. В дневнике ничего не поменялось. "
    "Время можно указать ответом на нужную запись."
)
M6A_NOT_ASKED = "Можно не отвечать, ничего не изменится."
M7_FAILED = (
    "Фото сохранено, распознать еду сейчас не вышло.\nВ дневнике запись с этим фото."
)
M7A_FAILED = (
    "Текст сохранён, разобрать еду сейчас не вышло.\n"
    "В дневнике запись с этим текстом."
)
M7D_NO_FOOD = "Фото сохранено, еды на нём не видно.\nВ дневнике запись с этим фото."
M7F_REFUSED = "Записать сейчас не вышло, сервер не отвечает. Пришли ещё раз."
M9_UNREADABLE = (
    "Такое пока не читается, в дневник не записано. "
    "Подойдёт фото, текст или голосовое."
)
M9A_NEW_MEAL = (
    "<b>🍽 Новый приём</b>\n"
    "Пришли фото, голосовое или напиши, что было. "
    "Время и остальное можно поправить потом."
)
M9B_STALE = "Это кнопка старого меню, в дневник не записано. Новое меню внизу."

FIX = "✏️ Исправить"
WRITE = "✏️ Написать"
WRITE_OTHER = "✏️ Написать иначе"
AGAIN = "🔁 Ещё раз"
DELETE = "🗑 Удалить"
TIME = "🕐 Время"
UNDO = "↩️ Вернуть"
SPLIT = "➖ Отделить"
IT_IS_FOOD = "🍽 Это еда"
WHOLE_MEAL = "🍽 Весь приём"
SEPARATE = "➕ Отдельный приём"

# --- the rules ------------------------------------------------------------------------

STALE_LABELS = frozenset(("Сейчас", "Завтрак", "Обед", "Ужин", "Назад", "Отмена"))
STOP_WORDS = frozenset(("привет", "спасибо", "ок", "окей", "да", "нет", "пока"))
FILE_MAX = 20 * 1024 * 1024  # what Telegram hands a bot
CORRECTION_WINDOW = timedelta(hours=3)  # ASSUMED, section 5.1
TIME_WINDOW = timedelta(hours=24)  # ASSUMED, M5e

_COLON_CLOCK = re.compile(r"\s*(вчера\s+)?\d{1,2}:\d{2}\s*", re.IGNORECASE)
_ADD_WORDS = re.compile(
    r"(?:(?:[иа]\s+)?ещ[её]|плюс|добавь|забыл[аио]?)\s+(.+)",
    re.IGNORECASE | re.DOTALL,
)
_SECOND_HELPING = frozenset(("еще порция", "добавка", "вторая порция", "еще одна"))
_IT_WAS = re.compile(r"(?:это\s+был[аои]?|нет,)\s+(.+)", re.IGNORECASE | re.DOTALL)
_NOT_BUT = re.compile(r"не\s+(.+?),\s*а\s+(.+)", re.IGNORECASE | re.DOTALL)
_REMOVE_WORDS = re.compile(r"убер(?:и|ите)\s+(.+)|убрать\s+(.+)", re.IGNORECASE)

ADD, REPEAT, CHANGE, REMOVE = "add", "repeat", "replace", "remove"

# What the handler does with a message.
LINE, WEIGHT, TICK, SILENT, NEW_MEAL = "line", "weight", "tick", "silent", "new_meal"

# The read that a meal's line waits for.
IMAGE, TEXT, VOICE = "image", "text", "voice"
_READING_KIND = {IMAGE: "vision", TEXT: "text", VOICE: "transcript"}

# What a reading that did not end says about the next try, section 5.2.
FAILED = "failed"  # the same answer again: a parse error, a dead file, a dead model
RETRY = "retry"  # passes by itself: a timeout, a dropped connection, a 5xx, a 429
NO_CREDIT = "no_credit"  # passes only after a top-up
NO_LIVE_READ = "NoLiveRead"  # no task holds this meal, the process went down in it
NO_BALANCE = "NoBalance"  # not asked: the last answer said the balance is out

# The reads nobody asked for. ``read_attempts`` counts them, and is the index here.
RETRY_AFTER = (timedelta(minutes=5), timedelta(hours=1), timedelta(hours=6))
RETRY_WINDOW = timedelta(hours=24)
RETRIES_PER_SWEEP = 5
STUCK_AFTER = timedelta(minutes=5)


class NoVoiceReader(Exception):
    """A voice note is kept; nothing listens to it yet."""


def _now() -> datetime:
    return datetime.now(UTC)


# --- what came ------------------------------------------------------------------------


@dataclass(frozen=True)
class Incoming:
    telegram_id: int
    username: Optional[str]
    chat_id: int
    message_id: int
    kind: Optional[str]  # MealSource.KINDS; None for what cannot be read
    text: Optional[str]  # the text, or a caption
    file_id: Optional[str]
    file_unique_id: Optional[str]
    file_size: Optional[int]
    duration_s: Optional[int]
    media_group_id: Optional[str]
    reply_to: Optional[int]
    sent_at: datetime
    forward_date: Optional[datetime]


def incoming(message, user) -> Incoming:
    kind = text = file = None
    if message.photo:
        kind, file, text = "photo", message.photo[-1], message.caption
    elif message.document and (message.document.mime_type or "").startswith("image/"):
        kind, file, text = "document_photo", message.document, message.caption
    elif message.voice:
        kind, file, text = "voice", message.voice, message.caption
    elif message.text:
        kind, text = "text", message.text
    origin = message.forward_origin
    reply = message.reply_to_message
    return Incoming(
        telegram_id=user.id,
        username=user.username,
        chat_id=message.chat_id,
        message_id=message.message_id,
        kind=kind,
        text=text,
        file_id=file.file_id if file else None,
        file_unique_id=file.file_unique_id if file else None,
        file_size=file.file_size if file else None,
        duration_s=getattr(file, "duration", None),
        media_group_id=message.media_group_id,
        reply_to=reply.message_id if reply else None,
        sent_at=message.date,
        forward_date=origin.date if origin else None,
    )


def too_big(came: Incoming) -> bool:
    return bool(came.file_size and came.file_size > FILE_MAX)


def seen(db, came: Incoming) -> bool:
    """A replayed update: the source is already there."""
    stmt = select(MealSource.id).where(
        MealSource.chat_id == came.chat_id,
        MealSource.message_id == came.message_id,
        MealSource.kind == came.kind,
    )
    return db.scalar(stmt) is not None


def _user(db, came: Incoming) -> User:
    user = db.scalar(select(User).where(User.telegram_id == came.telegram_id))
    if user is None:
        user = User(telegram_id=came.telegram_id, username=came.username)
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def _source(came: Incoming, user: User, meal_id: Optional[int]) -> MealSource:
    return MealSource(
        user_id=user.id,
        meal_id=meal_id,
        origin_meal_id=meal_id,
        kind=came.kind,
        chat_id=came.chat_id,
        message_id=came.message_id,
        media_group_id=came.media_group_id,
        reply_to_message_id=came.reply_to,
        forward_date=came.forward_date,
        sent_at=came.sent_at,
        text=came.text,
        file_id=came.file_id,
        file_unique_id=came.file_unique_id,
        duration_s=came.duration_s,
    )


def _keep(db, came: Incoming, user: User, meal_id: Optional[int] = None) -> MealSource:
    source = _source(came, user, meal_id)
    db.add(source)
    db.commit()
    return source


# --- the mapping ----------------------------------------------------------------------


def map_message(db, chat_id: int, message_id: int, meal_id: int, view: str) -> None:
    row = db.scalar(
        select(MealMessage).where(
            MealMessage.chat_id == chat_id, MealMessage.message_id == message_id
        )
    )
    if row is None:
        db.add(
            MealMessage(
                chat_id=chat_id, message_id=message_id, meal_id=meal_id, view=view
            )
        )
    else:
        row.meal_id, row.view = meal_id, view
    db.commit()


def tapped(db, chat_id: int, message_id: Optional[int]) -> None:
    """A button replaced the line with a screen: a late read leaves that screen alone."""
    if message_id is None:
        return
    row = db.scalar(
        select(MealMessage).where(
            MealMessage.chat_id == chat_id,
            MealMessage.message_id == message_id,
            MealMessage.view == "pending",
        )
    )
    if row is not None:
        row.view = "card"
        db.commit()


def _claim_view(db, job: "Read") -> bool:
    """Whether the line is still the read's to edit; from here on it is a plain line."""
    if job.line_id is None:
        return False
    row = db.scalar(
        select(MealMessage).where(
            MealMessage.chat_id == job.chat_id,
            MealMessage.message_id == job.line_id,
        )
    )
    if row is None or row.view != "pending":
        return False
    row.view = "line"
    db.commit()
    return True


# --- the lines ------------------------------------------------------------------------


@dataclass
class Line:
    text: str
    markup: Optional[InlineKeyboardMarkup] = None
    fill: Optional[meal_card.Fill] = None
    meal_id: Optional[int] = None  # the sent line gets a mapping to this meal
    menu: bool = False  # goes out with the main keyboard


def _take_hint(db, user: User) -> bool:
    """M1b, once in a person's life with the bot."""
    if (user.settings or {}).get("capture_hint_seen"):
        return False
    user.settings = {**(user.settings or {}), "capture_hint_seen": True}
    db.commit()
    return True


def _record_text(
    db,
    meal: Meal,
    user: User,
    *,
    hint: bool = False,
    note: Optional[str] = None,
    cost: Optional[str] = None,
) -> str:
    what = _what(db, meal)
    if what and shows_numbers(user) and meal.calories is not None:
        what += f" · ≈ {num(meal.calories)} ккал"
    photos = len(meal.photos or [])
    head = f"Записано по {photos} фото" if photos > 1 else "Записано"
    if meal.time_source == "forward":
        head = f"Записано на {_when(meal, user_zone(user))} {FORWARD_WORDS}"
    elif meal.time_source == "said":
        head = f"Записано на {_when(meal, user_zone(user))}"
    first = f"{head}: {what}." if what else f"{head}."
    if note:
        first += f" {note}"
    return "\n".join(line for line in (first, M1B_HINT if hint else None, cost) if line)


def _reading_markup(meal_id: int) -> InlineKeyboardMarkup:
    """While the items are not there yet the fix opens the meal."""
    return InlineKeyboardMarkup(
        [[_button(FIX, f"ml:o:{meal_id}:r"), _button(TIME, f"ml:t:{meal_id}:r")]]
    )


def _undo_markup(meal_id: int, event_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[_button(UNDO, f"ml:u:{event_id}:r"), _button(FIX, f"ml:o:{meal_id}:r")]]
    )


def _fix_markup(meal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[_button(FIX, f"ml:o:{meal_id}:r")]])


def _split_markup(source_id: int, meal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[_button(SPLIT, f"ml:z:{source_id}:r"), _button(FIX, f"ml:o:{meal_id}:r")]]
    )


def _it_is_food(source_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[_button(IT_IS_FOOD, f"ml:nf:{source_id}")]])


def _failed_line(kind: str, meal_id: int) -> Line:
    write = WRITE_OTHER if kind == TEXT else WRITE
    text = {IMAGE: M7_FAILED, TEXT: M7A_FAILED, VOICE: M4D_FAILED}[kind]
    markup = InlineKeyboardMarkup(
        [[_button(AGAIN, f"ml:rr:{meal_id}:r"), _button(write, f"ml:w:{meal_id}:r")]]
    )
    return Line(text, markup, meal_id=meal_id)


def _no_food_photo_line(meal_id: int) -> Line:
    markup = InlineKeyboardMarkup(
        [[_button(WRITE, f"ml:w:{meal_id}:r"), _button(DELETE, f"ml:d:{meal_id}:r")]]
    )
    return Line(M7D_NO_FOOD, markup, meal_id=meal_id)


# --- rows 4, 7, 8: changes to a meal that is there ------------------------------------


def _is_new_name(db, user: User, name: str) -> bool:
    found = crud_personal_food.find_by_name(db, user.id, meal_items._clean_name(name))
    return found is None


def _fill_of(change: meal_items.Change, new: bool) -> Optional[meal_card.Fill]:
    """A name nobody has seen gets its numbers in the background (N4)."""
    item = change.items[0] if change.items else None
    if not new or item is None or item.numbers_source != "none":
        return None
    return meal_card.Fill(item.id, change.meal.rev, change.event.id, item.name)


def _moment(meal: Optional[Meal], user: User, yesterday: bool, clock) -> datetime:
    """Of a replied meal, its own day. A bare clock: today, or yesterday if it is ahead."""
    zone, now = user_zone(user), _now()
    today = local_time(now, zone).date()
    if yesterday:
        day = today - timedelta(days=1)
    elif meal is not None:
        day = meal_card.meal_day(meal, user)
    else:
        day = today
    moment = datetime.combine(day, clock, tzinfo=zone)
    if meal is None and not yesterday and moment > now:
        moment -= timedelta(days=1)
    return moment


def _set_time(
    db, came: Incoming, user: User, meal: Meal, moment: datetime
) -> Optional[Line]:
    change = meal_items.set_time(db, user.id, meal.id, moment, meal_card.EXACT)
    if change.status == meal_items.STALE:
        return None
    _keep(db, came, user, meal.id)
    db.refresh(meal)
    what = _what(db, meal)
    text = f"Время приёма теперь {_when(meal, user_zone(user))}: {what}."
    if change.status == meal_items.SAME:
        return Line(text, _fix_markup(meal.id), meal_id=meal.id)
    return Line(text, _undo_markup(meal.id, change.event.id), meal_id=meal.id)


def _set_grams(db, came: Incoming, user: User, meal: Meal, grams: float) -> Line:
    items = crud_meal_item.live(db, meal.id)
    when = _when(meal, user_zone(user))
    if len(items) != 1:
        rows = [
            _button(_cut(i.name, 14), f"ml:ga:{i.id}:{num(grams)}:r") for i in items
        ]
        chips = [rows[i : i + 3] for i in range(0, len(rows), 3)]
        back = [_button("⬅️ К ответу бота", f"ml:r:{meal.id}")]
        _keep(db, came, user)
        return Line(
            f"<b>{num(grams)} г: у какого блюда?</b>\n{M6A_NOT_ASKED}\n"
            f"Приём {when}: {_what(db, meal)}.",
            InlineKeyboardMarkup([*chips, back]),
            meal_id=meal.id,
        )
    change = meal_items.set_grams(db, user.id, items[0].id, grams)
    _keep(db, came, user, meal.id)
    name = meal_card._esc(meal_card._capital(items[0].name))
    text = f"{name} в приёме {when}: {num(grams)} г."
    if change.event is None:
        return Line(text, _fix_markup(meal.id), meal_id=meal.id)
    return Line(text, _undo_markup(meal.id, change.event.id), meal_id=meal.id)


def _add(db, came: Incoming, user: User, meal: Meal, name: str) -> Optional[Line]:
    new = _is_new_name(db, user, name)
    change = meal_items.add_item(db, user.id, meal.id, name=name)
    if change.status != meal_items.DONE:
        return None
    source = _keep(db, came, user, meal.id)
    return Line(
        f"Добавлено к приёму {_when(meal, user_zone(user))}: "
        f"{meal_card._esc(change.items[0].name)}.",
        _split_markup(source.id, meal.id),
        fill=_fill_of(change, new),
        meal_id=meal.id,
    )


def _chooser(
    db,
    came: Incoming,
    user: User,
    meal: Meal,
    items: list,
    title: str,
    *,
    whole: bool = False,
) -> Line:
    """M6a: nothing applied, the source kept, and ignoring it changes nothing."""
    source = _keep(db, came, user)
    chips = [_button(_cut(i.name, 14), f"ml:ka:{source.id}:{i.id}") for i in items]
    rows = [chips[i : i + 2] for i in range(0, len(chips), 2)]
    if whole:
        rows.append([_button(WHOLE_MEAL, f"ml:kw:{source.id}")])
    rows.append([_button(SEPARATE, f"ml:kn:{source.id}")])
    words = meal_card._esc(came.text or "")
    return Line(
        f"<b>{title}</b>\n{M6A_NOT_ASKED}\n"
        f"«{words}», приём {_when(meal, user_zone(user))}",
        InlineKeyboardMarkup(rows),
        meal_id=meal.id,
    )


def _plain(word: str) -> str:
    return word.casefold().replace("ё", "е")


def _stem(word: str) -> str:
    word = _plain(word)
    return word[: max(3, len(word) - 2)]


def _matching(items: list, phrase: str) -> list:
    """The dishes all of whose named words are there, up to a Russian ending."""
    stems = [_stem(word) for word in phrase.split()]
    found = []
    for item in items:
        words = [_plain(word) for word in item.name.split()]
        if stems and all(any(w.startswith(s) for w in words) for s in stems):
            found.append(item)
    return found


def _correction(text: str) -> Optional[tuple]:
    """(what, name, old name) from the start of the text; section 5.4."""
    body = text.strip()
    if _plain(body).rstrip(".!") in _SECOND_HELPING:
        return REPEAT, None, None
    match = _NOT_BUT.fullmatch(body)
    if match:
        return CHANGE, match.group(2).strip(" ."), match.group(1).strip()
    match = _IT_WAS.fullmatch(body)
    if match:
        return CHANGE, match.group(1).strip(" ."), None
    match = _REMOVE_WORDS.fullmatch(body)
    if match:
        return REMOVE, (match.group(1) or match.group(2)).strip(" ."), None
    match = _ADD_WORDS.fullmatch(body)
    if match:
        return ADD, match.group(1).strip(" ."), None
    return None


def _apply_correction(
    db, came: Incoming, user: User, meal: Meal, correction: tuple
) -> Optional[Line]:
    what, name, old = correction
    when = _when(meal, user_zone(user))
    items = crud_meal_item.live(db, meal.id)
    if what == ADD:
        return _add(db, came, user, meal, name)
    if not items:
        return None
    if what == REPEAT:
        if len(items) > 1:
            return _chooser(
                db,
                came,
                user,
                meal,
                items,
                "Какое блюдо добавить ещё раз?",
                whole=True,
            )
        change = meal_items.add_item(
            db, user.id, meal.id, food_id=items[0].personal_food_id
        )
        if change.status != meal_items.DONE:
            return None
        source = _keep(db, came, user, meal.id)
        return Line(
            f"Добавлено к приёму {when}: {meal_card._esc(items[0].name)}.",
            _split_markup(source.id, meal.id),
            meal_id=meal.id,
        )
    if what == REMOVE:
        found = _matching(items, name)
        if len(found) != 1:
            return _chooser(db, came, user, meal, items, "Какое блюдо это меняет?")
        gone = found[0].name
        change = meal_items.remove_item(db, user.id, found[0].id)
        if change.status != meal_items.DONE:
            return None
        _keep(db, came, user, meal.id)
        return Line(
            f"Убрано из приёма {when}: {meal_card._esc(gone)}.",
            _undo_markup(meal.id, change.event.id),
            meal_id=meal.id,
        )
    found = _matching(items, old or name)
    if len(found) != 1:
        found = items if len(items) == 1 else []
    if not found:
        return _chooser(db, came, user, meal, items, "Какое блюдо это меняет?")
    was = found[0].name
    new = _is_new_name(db, user, name)
    change = meal_items.replace_item(db, user.id, found[0].id, name=name)
    if change.status != meal_items.DONE:
        return None
    _keep(db, came, user, meal.id)
    return Line(
        f"Заменено в приёме {when}: {meal_card._esc(was)} → {meal_card._esc(name)}.",
        _undo_markup(meal.id, change.event.id),
        fill=_fill_of(change, new),
        meal_id=meal.id,
    )


def _aware(moment: datetime) -> datetime:
    """SQLite reads a stored moment back without its zone; stored is always UTC."""
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _sent(meal: Meal) -> datetime:
    return _aware(meal.sent_at or meal.created_at)


def _last_meal(db, user: User, within: timedelta) -> Optional[Meal]:
    meal = db.scalar(
        select(Meal)
        .where(
            Meal.user_id == user.id,
            Meal.deleted_at.is_(None),
            Meal.is_draft.is_(False),
        )
        .order_by(Meal.id.desc())
        .limit(1)
    )
    if meal is None or _now() - _sent(meal) >= within:
        return None
    return meal


def _replied_meal(db, came: Incoming) -> Optional[Meal]:
    if came.reply_to is None:
        return None
    row = db.scalar(
        select(MealMessage).where(
            MealMessage.chat_id == came.chat_id,
            MealMessage.message_id == came.reply_to,
        )
    )
    return db.get(Meal, row.meal_id) if row else None


# --- the router -----------------------------------------------------------------------


@dataclass
class Route:
    action: str
    line: Optional[Line] = None
    tick: Optional[str] = None
    note: Optional[str] = None


def _on_reply(db, came: Incoming, user: User, meal: Meal) -> Optional[Route]:
    """Row 4. A bare time before bare grams: «12:10» is no number."""
    text = came.text
    colon = _COLON_CLOCK.fullmatch(text)
    parsed = meal_card.parse_time(text) if colon else None
    grams = None if colon else meal_card.parse_grams(text)
    if meal.deleted_at is not None:
        if colon:
            return Route(LINE, Line(M5F_NO_MEAL))
        return Route(NEW_MEAL, note=M5A_DELETED)
    if colon:
        if parsed is None:
            return Route(LINE, Line(M5F_NO_MEAL))
        moment = _moment(meal, user, parsed[0], parsed[1])
        if moment > _now():
            return Route(LINE, Line(meal_card.TIME_FUTURE))
        line = _set_time(db, came, user, meal, moment)
        return Route(LINE, line) if line else None
    if grams is not None:
        return Route(LINE, _set_grams(db, came, user, meal, grams))
    if text.rstrip().endswith("?"):
        return None
    correction = _correction(text)
    line = (
        _apply_correction(db, came, user, meal, correction)
        if correction
        else _add(db, came, user, meal, text.strip())
    )
    return Route(LINE, line) if line else None


def _is_stop_word(text: str) -> bool:
    return text.strip().strip(".,!…)( ").casefold() in STOP_WORDS


def route_text(db, came: Incoming) -> Route:
    """Rows 3 to 10 for a text. The first two are ahead of this handler."""
    text = came.text
    if text.strip() in STALE_LABELS:
        return Route(LINE, Line(M9B_STALE, menu=True))
    user = _user(db, came)

    target = _replied_meal(db, came)
    if target is not None:
        route = _on_reply(db, came, user, target)
        if route is not None:
            return route
    if weight_screen.KG_PATTERN.match(text):
        return Route(WEIGHT)
    name = stats_screen.tick_by_name(db, came.telegram_id, text)
    if name is not None:
        return Route(TICK, tick=name)

    colon = _COLON_CLOCK.fullmatch(text)
    if colon:
        parsed = meal_card.parse_time(text)
        meal = _last_meal(db, user, TIME_WINDOW)
        if parsed is None or meal is None:
            return Route(LINE, Line(M5F_NO_MEAL))
        moment = _moment(None, user, parsed[0], parsed[1])
        line = _set_time(db, came, user, meal, moment)
        return Route(LINE, line or Line(M5F_NO_MEAL))

    correction = _correction(text)
    if correction:
        meal = _last_meal(db, user, CORRECTION_WINDOW)
        if meal is not None:
            line = _apply_correction(db, came, user, meal, correction)
            if line is not None:
                return Route(LINE, line)
        else:
            logger.info("Corrective words with no meal in the window")

    if _is_stop_word(text):
        _keep(db, came, user)
        return Route(SILENT)
    if text.rstrip().endswith("?"):
        source = _keep(db, came, user)
        return Route(LINE, Line(M3B_QUESTION, _it_is_food(source.id)))
    return Route(NEW_MEAL)


# --- row 10: the meal is born ---------------------------------------------------------


@dataclass(frozen=True)
class Read:
    """What the background read needs. No row crosses an await."""

    meal_id: int
    rev: int
    source_id: int
    telegram_id: int
    chat_id: int
    kind: str
    file_id: Optional[str]
    text: Optional[str]
    line_id: Optional[int] = None
    shown: Optional[str] = None  # what the line says now
    automatic: bool = False  # nobody asked for it: counted, capped, never a push
    hint: bool = False  # the line carries M1b: the edit keeps it
    note: Optional[str] = None
    inbound_id: Optional[int] = None


@dataclass
class Born:
    meal_id: int
    source_id: int
    rev: int
    final: Optional[Line] = None  # an exact name: nothing to read
    joined: int = 0  # the album's count when this photo joined its meal
    names: Optional[list] = None  # an exact name's dishes
    first: Optional[Line] = None
    job: Optional[Read] = None


def _kind_of_read(source_kind: str) -> str:
    if source_kind == "text":
        return TEXT
    return VOICE if source_kind == "voice" else IMAGE


def _join_album(db, came: Incoming, user: User) -> Optional[Born]:
    """Found by the database, not memory. The album's read comes with its own step."""
    if not came.media_group_id or came.kind != "photo":
        return None
    first = db.scalar(
        select(MealSource)
        .where(
            MealSource.user_id == user.id,
            MealSource.media_group_id == came.media_group_id,
            MealSource.meal_id.is_not(None),
        )
        .limit(1)
    )
    if first is None:
        return None
    meal = db.get(Meal, first.meal_id)
    meal.photos = [*(meal.photos or []), came.file_id]
    source = _source(came, user, meal.id)
    db.add(source)
    db.commit()
    return Born(meal.id, source.id, meal.rev or 0, joined=len(meal.photos))


def _born_exact(
    db, came: Incoming, user: User, dishes: list, note: Optional[str]
) -> Born:
    moment = came.forward_date or came.sent_at
    meal = meal_items.save_exact(db, user.id, dishes, meal_time=moment)
    meal.sent_at = came.sent_at
    meal.time_source = "forward" if came.forward_date else "sent"
    meal.time_precision = "approx" if came.forward_date else "exact"
    source = _source(came, user, meal.id)
    db.add(source)
    db.flush()
    db.add(
        MealReading(
            meal_id=meal.id,
            source_ids=[source.id],
            kind="exact_name",
            output={"foods": [dish.canonical_name for dish in dishes]},
            status="done",
            applied=True,
        )
    )
    db.add(
        MealMessage(
            chat_id=came.chat_id,
            message_id=came.message_id,
            meal_id=meal.id,
            view="source",
        )
    )
    db.commit()
    hint = _take_hint(db, user)
    text = _record_text(db, meal, user, hint=hint, note=note)
    line = Line(text, meal_card.line_markup(db, meal), meal_id=meal.id)
    names = [dish.canonical_name for dish in dishes]
    return Born(meal.id, source.id, meal.rev or 0, final=line, names=names)


def born(db, came: Incoming, *, note: Optional[str] = None) -> Optional[Born]:
    """Source and meal in one commit, before any download or model call. None: this
    message is already here, the unique key said so."""
    user = _user(db, came)
    try:
        joined = _join_album(db, came, user)
        if joined is not None:
            return joined
        if came.kind == "text":
            dishes = meal_items.exact_match(db, user.id, came.text)
            if dishes:
                return _born_exact(db, came, user, dishes, note)
        reading_text = came.kind == "text"
        moment = came.forward_date or came.sent_at
        meal = crud_meal.build(
            MealCreate(
                description=came.text if reading_text else None,
                meal_time=moment,
                sent_at=came.sent_at,
                photos=[] if reading_text or came.kind == "voice" else [came.file_id],
                time_precision="approx" if came.forward_date else "exact",
                time_source="forward" if came.forward_date else "sent",
                numbers_basis="none",
            ),
            user.id,
        )
        meal.reading_status = "pending"
        db.add(meal)
        db.flush()
        source = _source(came, user, meal.id)
        db.add(source)
        db.flush()
        db.add(
            MealMessage(
                chat_id=came.chat_id,
                message_id=came.message_id,
                meal_id=meal.id,
                view="source",
            )
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        return None

    kind = _kind_of_read(came.kind)
    hint = False
    if kind == TEXT:
        hint = _take_hint(db, user)
        words = _record_text(db, meal, user, hint=hint, note=note)
        first = Line(words, _reading_markup(meal.id), meal_id=meal.id)
    else:
        first = Line(M1_PENDING if kind == IMAGE else M4_PENDING, meal_id=meal.id)
    job = Read(
        meal_id=meal.id,
        rev=meal.rev or 0,
        source_id=source.id,
        telegram_id=came.telegram_id,
        chat_id=came.chat_id,
        kind=kind,
        file_id=came.file_id,
        text=came.text,
        shown=first.text,
        hint=hint,
        note=note,
    )
    return Born(meal.id, source.id, meal.rev or 0, first=first, job=job)


def album_line_id(db, meal_id: int) -> Optional[int]:
    """The line of a meal still being read, to count the album's photos on."""
    return db.scalar(
        select(MealMessage.message_id).where(
            MealMessage.meal_id == meal_id, MealMessage.view == "pending"
        )
    )


# --- the read comes back --------------------------------------------------------------


def _store_reading(
    db,
    job: Read,
    *,
    status: str,
    output: Optional[dict] = None,
    error: Optional[str] = None,
    applied: bool = False,
    model: Optional[str] = None,
) -> None:
    db.add(
        MealReading(
            meal_id=job.meal_id,
            source_ids=[job.source_id],
            kind=_READING_KIND[job.kind],
            model=model,
            output=output,
            status=status,
            error=error,
            applied=applied,
            created_at=_now(),
        )
    )
    db.commit()


def _stamped(nutrition: dict) -> dict:
    """Unstamped with no tags, so the re-tag run still finds the meal."""
    if not nutrition.get("food_items"):
        return dict(nutrition)
    groups = food_groups.from_items(nutrition.get("food_items"))
    return food_groups.stamp(nutrition, groups, by_user=False)


def _read_in(job: Read, meal: Meal, nutrition: dict, resolution) -> MealCreate:
    stored = _stamped(nutrition)
    foods = nutrition.get("foods") or []
    words = job.text if job.kind == TEXT else ", ".join(map(str, foods))
    return MealCreate(
        description=words or None,
        meal_time=meal.meal_time,
        calories=nutrition.get("calories"),
        proteins=nutrition.get("protein"),
        fats=nutrition.get("fats"),
        carbohydrates=nutrition.get("carbs"),
        nutrients=stored,
        photos=meal.photos or [],
        ai_analysis=stored,
        resolution_source=resolution.source if resolution else None,
        resolution_signals=resolution.signals if resolution else None,
    )


@dataclass
class Done:
    line: Optional[Line] = None  # None: nothing moves on screen
    learn: Optional[dict] = None  # for the learning loop, once the session is closed


def _no_food(db, job: Read, meal: Meal) -> Line:
    meal.reading_status = "no_food"
    if job.kind != TEXT:
        db.commit()
        return _no_food_photo_line(meal.id)
    # M3a: nothing goes in the diary, and the words stay.
    meal.deleted_at = _now()
    source = db.get(MealSource, job.source_id)
    source.meal_id = None
    db.commit()
    return Line(M3A_NO_FOOD, _it_is_food(source.id))


def read_done(
    db,
    job: Read,
    nutrition: dict,
    resolution=None,
    *,
    cost: Optional[str] = None,
    model: Optional[str] = None,
) -> Done:
    meal = db.get(Meal, job.meal_id)
    meal_in = _read_in(job, meal, nutrition, resolution)
    if (meal.rev or 0) != job.rev or meal.deleted_at is not None:
        # What the person did outranks a late answer. Kept, not applied.
        _store_reading(db, job, status="done", output=nutrition, model=model)
        if meal.reading_status == "pending":
            meal.reading_status = "read"
            db.commit()
        return Done()

    names = meal_items.names_read(meal_in)
    if not names:
        _store_reading(
            db, job, status="no_food", output=nutrition, applied=True, model=model
        )
        line = _no_food(db, job, meal)
        return Done(line if _claim_view(db, job) else None)

    meal_items.apply_read(db, meal, job.rev, meal_in, source_id=job.source_id)
    _store_reading(db, job, status="done", output=nutrition, applied=True, model=model)
    user = db.get(User, meal.user_id)
    learn = {
        "user_id": meal.user_id,
        "meal_id": meal.id,
        "nutrition": nutrition,
        "resolution_signals": resolution.signals if resolution else None,
        "resolution_source": resolution.source if resolution else None,
        "canonical_name": meal_items.only_dish_name(db, meal),
    }
    if not _claim_view(db, job):
        return Done(learn=learn)
    hint = job.hint or _take_hint(db, user)
    text = _record_text(db, meal, user, hint=hint, note=job.note, cost=cost)
    if text == job.shown:
        return Done(learn=learn)
    line = Line(text, meal_card.line_markup(db, meal), meal_id=meal.id)
    return Done(line, learn)


def failure_status(error: BaseException) -> str:
    """What this failure says about trying again. Only what passes by itself is
    retried, and anything unknown is not: a retry is a paid call. Along the whole
    chain, since the photo pipeline raises its own error from the model's."""
    if ai_errors.out_of_credit(error) is not None:
        return NO_CREDIT
    if ai_errors.rate_limited(error):
        return RETRY
    for link in ai_errors._chain(error):
        # A BadRequest is a NetworkError to the library: a dead file, no network.
        if isinstance(link, BadRequest):
            return FAILED
        if isinstance(
            link,
            (
                openai.APITimeoutError,
                openai.APIConnectionError,
                openai.InternalServerError,
                NetworkError,
                httpx.TransportError,
                asyncio.TimeoutError,
            ),
        ):
            return RETRY
    return FAILED


def read_failed(
    db,
    job: Read,
    error: str,
    *,
    status: str = FAILED,
    model: Optional[str] = None,
) -> Done:
    """``error`` is the exception's class: a body or a transcript never goes there."""
    meal = db.get(Meal, job.meal_id)
    meal.reading_status = "failed"
    db.commit()
    _store_reading(db, job, status=status, error=error, model=model)
    line = _failed_line(job.kind, meal.id)
    if not _claim_view(db, job) or line.text == job.shown:
        # A retry that failed again: the line says it already.
        return Done()
    return Done(line)


# --- reading again --------------------------------------------------------------------


def _line_of(db, meal_id: int) -> Optional[MealMessage]:
    """The line the capture sent, if it still is a line. A later reply's line belongs
    to the meal too, and a screen opened on it is the person's."""
    line = db.scalar(
        select(MealMessage)
        .where(MealMessage.meal_id == meal_id, MealMessage.view != "source")
        .order_by(MealMessage.id)
        .limit(1)
    )
    return line if line is not None and line.view in ("pending", "line") else None


def _job_of(db, meal: Meal, *, automatic: bool = False) -> Optional[Read]:
    source = db.scalar(
        select(MealSource)
        .where(MealSource.meal_id == meal.id)
        .order_by(MealSource.id)
        .limit(1)
    )
    if source is None:
        return None
    user = db.get(User, meal.user_id)
    line = _line_of(db, meal.id)
    kind = _kind_of_read(source.kind)
    was_failed = meal.reading_status == "failed"
    return Read(
        meal_id=meal.id,
        rev=meal.rev or 0,
        source_id=source.id,
        telegram_id=user.telegram_id,
        chat_id=source.chat_id,
        kind=kind,
        file_id=source.file_id,
        text=source.text,
        line_id=line.message_id if line else None,
        shown=_failed_line(kind, meal.id).text if was_failed else None,
        inbound_id=_inbound_of(db, user.telegram_id, source),
        automatic=automatic,
    )


def read_again(db, meal_id: int, *, automatic: bool = False) -> Optional[Read]:
    """The read for a meal that has none yet; its line opens for the edit again."""
    meal = db.get(Meal, meal_id)
    if meal is None or meal.deleted_at is not None:
        return None
    job = _job_of(db, meal, automatic=automatic)
    if job is None:
        return None
    line = _line_of(db, meal.id)
    if line is not None:
        line.view = "pending"
    meal.reading_status = "pending"
    db.commit()
    return job


def _inbound_of(db, telegram_id: int, source: MealSource) -> Optional[int]:
    """The diagnostic row of this source, so /reprocess does not read it twice."""
    stmt = select(InboundMessage.id).where(
        InboundMessage.telegram_id == telegram_id,
        InboundMessage.status != "analyzed",
    )
    if source.file_id:
        stmt = stmt.where(InboundMessage.photo_file_id == source.file_id)
    else:
        stmt = stmt.where(InboundMessage.content == source.text)
    return db.scalar(stmt.order_by(InboundMessage.id.desc()).limit(1))


def unread_meals(db, limit: int) -> list:
    """Ids of the meals still without a reading; a voice has nothing to read it yet."""
    stmt = (
        select(Meal.id)
        .join(MealSource, MealSource.meal_id == Meal.id)
        .where(
            Meal.reading_status.in_(("pending", "failed")),
            Meal.deleted_at.is_(None),
            Meal.is_draft.is_(False),
            MealSource.kind != "voice",
        )
        .distinct()
        .order_by(Meal.id)
        .limit(limit)
    )
    return list(db.scalars(stmt))


# --- the reads nobody asked for, section 5.2 ------------------------------------------


def balance_out(db) -> bool:
    """Whether the model's last word was that the money is gone. From the rows, so a
    restart remembers: only a refusal or a real answer says anything about it. A
    timeout does not, and an exact name asked no model."""
    last = db.scalar(
        select(MealReading.status)
        .where(
            or_(
                MealReading.status == NO_CREDIT,
                and_(
                    MealReading.kind.in_(("vision", "text")),
                    MealReading.status.in_(("done", "no_food")),
                ),
            )
        )
        .order_by(MealReading.id.desc())
        .limit(1)
    )
    return last == NO_CREDIT


def _may_read_by_itself(db, meal: Meal) -> bool:
    """A voice has no reader yet, the cap holds, and dishes in the meal are the
    person's: a read would be paid for and could not apply."""
    if meal.deleted_at is not None or meal.is_draft:
        return False
    if (meal.read_attempts or 0) >= len(RETRY_AFTER):
        return False
    voice = db.scalar(
        select(MealSource.id)
        .where(MealSource.meal_id == meal.id, MealSource.kind == "voice")
        .limit(1)
    )
    if voice is not None:
        return False
    return not crud_meal_item.live(db, meal.id)


def pending_meals(db) -> list:
    stmt = select(Meal.id).where(
        Meal.reading_status == "pending",
        Meal.deleted_at.is_(None),
        Meal.is_draft.is_(False),
    )
    return list(db.scalars(stmt.order_by(Meal.id)))


def stuck_meals(db, live) -> list:
    """Pending, older than the wait, and no task holds them. A slow read is not stuck.
    From the row's birth, not the message's: it may have come in while the bot was down.
    """
    stmt = select(Meal.id, Meal.created_at).where(
        Meal.reading_status == "pending",
        Meal.deleted_at.is_(None),
        Meal.is_draft.is_(False),
    )
    limit = _now() - STUCK_AFTER
    return [
        meal_id
        for meal_id, born_at in db.execute(stmt.order_by(Meal.id))
        if meal_id not in live and _aware(born_at) <= limit
    ]


def due_retries(db, limit: int) -> list:
    """Ids of the failed meals whose next try is due; none while the balance is out."""
    if balance_out(db):
        return []
    now = _now()
    failed = db.scalars(
        select(Meal)
        .where(
            Meal.reading_status == "failed",
            Meal.sent_at > now - RETRY_WINDOW,
            Meal.read_attempts < len(RETRY_AFTER),
        )
        .order_by(Meal.id)
    )
    due = []
    for meal in failed:
        last = db.scalar(
            select(MealReading)
            .where(MealReading.meal_id == meal.id)
            .order_by(MealReading.id.desc())
            .limit(1)
        )
        if last is None or last.status != RETRY:
            continue
        if now - _aware(last.created_at) < RETRY_AFTER[meal.read_attempts or 0]:
            continue
        if _may_read_by_itself(db, meal):
            due.append(meal.id)
            if len(due) == limit:
                break
    return due


def automatic_read(db, meal_id: int) -> Optional[Read]:
    """The read of a pending meal found at startup; None if it may not have one."""
    meal = db.get(Meal, meal_id)
    if meal is None or meal.reading_status != "pending":
        return None
    if not _may_read_by_itself(db, meal):
        return None
    return read_again(db, meal_id, automatic=True)


def automatic_start(db, job: Read) -> Optional[Done]:
    """At the semaphore, right before the paid call. Counted now, so a process that
    dies in the read has still spent the try. A Done: the balance ran out while this
    one waited, and nothing was asked."""
    if balance_out(db):
        return read_failed(db, job, NO_BALANCE, status=RETRY)
    meal = db.get(Meal, job.meal_id)
    meal.read_attempts = (meal.read_attempts or 0) + 1
    db.commit()
    return None


def give_up(db, meal_id: int) -> tuple:
    """(job, done) of a pending meal with nothing reading it, so that no line stays on
    «читаю». What it failed on may pass by itself, so it may be retried. (None, None)
    if there is nothing to edit, or it is no longer pending."""
    meal = db.get(Meal, meal_id)
    if meal is None or meal.reading_status != "pending":
        return None, None
    job = _job_of(db, meal)
    if job is None:
        meal.reading_status = "failed"
        db.commit()
        return None, None
    return job, read_failed(db, job, NO_LIVE_READ, status=RETRY)
