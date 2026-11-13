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
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from datetime import time as clock_time
from datetime import timedelta
from typing import Optional

import httpx
import openai
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from telegram import InlineKeyboardMarkup
from telegram.error import BadRequest, NetworkError

from app.core.config import settings
from app.crud.crud_inbound_message import crud_inbound_message
from app.crud.crud_meal import crud_meal
from app.crud.crud_meal_item import crud_meal_item
from app.crud.crud_personal_food import crud_personal_food
from app.models.inbound_message import InboundMessage
from app.models.meal import Meal
from app.models.meal_message import MealMessage
from app.models.meal_reading import MealReading
from app.models.meal_source import MealSource
from app.models.record_event import RecordEvent
from app.models.reminder_send import ReminderSend
from app.models.user import User
from app.schemas.meal import MealCreate
from app.services import (
    ai_errors,
    food_groups,
    meal_card,
    meal_items,
    reminders,
    stats_screen,
    voice,
    weight_screen,
)
from app.services.access_control import may_receive
from app.services.close_row import ok_row
from app.services.day_card import DAY_ONLY, local_time
from app.services.meal_card import M1_PENDING  # noqa: F401 (the name tests read it by)
from app.services.meal_card import M4_PENDING  # noqa: F401 (the name tests read it by)
from app.services.meal_card import (  # noqa: F401 (the copy is read from here too)
    AGAIN,
    DEAD_FILE,
    DELETE,
    IMAGE,
    M3A_NO_FOOD,
    M4C_VOICE_HINT,
    M4D_FAILED,
    M4E_NO_SPEECH,
    M4T_READING,
    M7_FAILED,
    M7A_FAILED,
    M7D_NO_FOOD,
    M7E_DEAD_FILE,
    TEXT,
    VOICE,
    WRITE,
    WRITE_OTHER,
    Retouch,
    _button,
    _cut,
    _names,
    _what,
    _when,
)
from app.services.meal_card import kind_of_read as _kind_of_read
from app.services.meal_card import (  # noqa: F401 (the copy is read from here too)
    line_head,
    line_plain,
    line_what,
)
from app.services.meal_draft import num
from app.services.meal_words import STOP_WORDS  # noqa: F401 (the name tests read)
from app.services.meal_words import _TODAY, _YESTERDAY, ADD, REMOVE, REPEAT, SaidTime
from app.services.meal_words import correction as _correction
from app.services.meal_words import matching as _matching
from app.services.meal_words import says_no as _says_no
from app.services.meal_words import time_words
from app.services.settings_screen import draft_on, user_zone

logger = logging.getLogger(__name__)

# --- the copy (section 5.5) ---------------------------------------------------------

M1B_HINT = "На этом всё. Поправить можно кнопками или ответом на это сообщение."
# M11 where 🔁 would not read what was edited, and where no record stands to wait.
M11_KEPT = "Сообщение изменено. В дневнике пока прежняя запись."
M11_NO_RECORD = "Сообщение изменено. В дневнике по нему записи нет."
M3B_QUESTION = "Вопросы здесь пока не разбираются, в дневник не записано."
M4F_TOO_BIG = (
    "Файл больше 20 МБ, Telegram такие боту не передаёт. В дневник не записано."
)
M5A_DELETED = "Тот приём был удалён раньше, поэтому это отдельная запись."
TARGET_GONE = "<i>Того приёма уже нет.</i>"
M5F_NO_MEAL = (
    "Похоже на время, но не ясно, к какому приёму. В дневнике ничего не поменялось. "
    "Время можно указать ответом на нужную запись."
)
M6A_NOT_ASKED = "Можно не отвечать, ничего не изменится."
# A change sent while the message before it is still being worked out: the change
# may belong to what that message turns out to be.
STILL_READING = "Ещё разбираю прошлое сообщение — пришли это через минуту."
M4C_DRAFT_HINT = (
    "Голосовые пока в пробном режиме. Что услышано, видно по кнопке «🎙 Текст». "
    "Если сервис проекта не отвечает, голосовое уходит во внешний сервис."
)
M12_ROLLOUT = (
    "Теперь еда записывается сразу, без подтверждения. "
    "В меню появились «🍲 Блюда». "
    "Как это работает: ⚙️ Настройки → ℹ️ Как это работает."
)
ROLLOUT_SEEN = "rollout_seen"
M7_NOTE = "Фото хранится при этой записи."
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

AGAIN_TOAST = "Читаю"
AGAIN_FAILED_TOAST = "Пока не вышло, запись сохранена"
M7B_ASK = (
    "<b>✏️ Что было?</b>\n"
    "Напиши одним сообщением, что и примерно сколько, "
    "например «суп с курицей, тарелка». Калории и БЖУ посчитаются по этим словам."
)
M8_JOINED = "Объединено с приёмом {when}: {what}."
M8_TOGETHER_TOAST = "Уже вместе"
M8A_APART = "Отделено: {what}. Теперь это отдельный приём, {when}."
M8A_APART_TOAST = "Уже отдельно"
M8B_ASK = "<b>🔗 С каким приёмом объединить?</b>"
M11_EDITED = (
    "Сообщение изменено. В дневнике пока прежняя запись. "
    "Кнопка «🔁 Прочитать заново» прочитает новый текст."
)
M13_TITLE = "<b>📋 Что прислано</b>"
M13_ABOUT = (
    "Здесь твои сообщения, как они пришли. Исправления меняют приём, а сообщения "
    "остаются."
)
M13_MORE = (
    "«🔁 Прочитать заново» читает их ещё раз, твои исправления остаются.\n"
    "«➖ Отделить» делает сообщение отдельным приёмом."
)
M13A_NOTHING = "Сообщение при этой записи не сохранено."
M13_PHOTO_GONE_TOAST = "Это фото больше недоступно в Telegram"
M2B_SPLIT = (
    "<b>➖ Разделить {count} фото на отдельные приёмы?</b>\n"
    "Каждое фото будет распознано заново, отдельно от других. "
    "Вернуть как было можно кнопкой «↩️ Вернуть»."
)

FIX = "✏️ Исправить"
TIME = "🕐 Время"
UNDO = "↩️ Вернуть"
SPLIT = "➖ Отделить"
SPLIT_ALBUM = "➖ Разделить"
IT_IS_FOOD = "🍽 Это еда"
WHOLE_MEAL = "🍽 Весь приём"
SEPARATE = "➕ Отдельный приём"
READ_ANEW = "🔁 Прочитать заново"
SHOW_PHOTO = "📷 Показать фото"

# --- the rules ------------------------------------------------------------------------

READ_SURVIVES = ("time", "join")  # what a person does that takes nothing from a read
OWED_VIEW = (
    meal_items.OWED_VIEW
)  # the read is over; Telegram has not taken its last word
LINE_VIEWS = meal_items.LINE_VIEWS
READ_ANEW_VIEW = "reread"  # a message whose 🔁 may pay to read a standing record anew
PHOTO_SOURCES = ("photo", "document_photo")
READABLE_IMAGES = frozenset(
    ("image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif")
)
SOURCE_WORDS_MAX = 60
OWED_PER_SWEEP = 20  # lines put right in one pass: a long outage leaves many
SPLIT_BUTTONS_MAX = 9

STALE_LABELS = frozenset(("Сейчас", "Завтрак", "Обед", "Ужин", "Назад", "Отмена"))
FILE_MAX = 20 * 1024 * 1024  # what Telegram hands a bot
CORRECTION_WINDOW = timedelta(hours=3)  # ASSUMED, section 5.1
_DAY_END = clock_time(23, 59, 59)  # a reminder's day, as late as it goes (§3.2(a))
RECALL_KEY = "recall"  # Meal.resolution_signals: what a bound voice found (§3.2)
TIME_WINDOW = timedelta(hours=24)  # ASSUMED, M5e
_MERGES_MAX = 16  # a chain of joined meals; only ever a guard against a bad row

_COLON_CLOCK = re.compile(r"\s*(вчера\s+)?(в\s+)?\d{1,2}:\d{2}\s*", re.IGNORECASE)
_CLOCK_IN = re.compile(r"(\s*(?:вчера\s+)?)в\s+", re.IGNORECASE)

# What the handler does with a message.
LINE, WEIGHT, TICK, SILENT, NEW_MEAL = "line", "weight", "tick", "silent", "new_meal"

# The read that a meal's line waits for.
_READING_KIND = {IMAGE: "vision", TEXT: "text", VOICE: "transcript"}
NO_SPEECH = "no_speech"
# Telegram's message limit, with room for the line above the quote and its escapes.
HEARD_SHOWN_MAX = 2800

# What a reading that did not end says about the next try, section 5.2.
FAILED = "failed"  # the same answer again: a parse error, a dead file, a dead model
RETRY = "retry"  # passes by itself: a timeout, a dropped connection, a 5xx, a 429
NO_CREDIT = "no_credit"  # passes only after a top-up
NO_LIVE_READ = "NoLiveRead"  # no task holds this meal, the process went down in it
NO_BALANCE = "NoBalance"  # not asked: the last answer said the balance is out

# The reads nobody asked for. ``read_attempts`` counts them and a person's 🔁 Прочитать заново
# (M7 writes it), and is the index here: who just tried by hand waits the longer step.
RETRY_AFTER = (timedelta(minutes=5), timedelta(hours=1), timedelta(hours=6))
RETRY_WINDOW = timedelta(hours=24)
RETRIES_PER_SWEEP = 5
STUCK_AFTER = timedelta(minutes=5)

# An album, M2: the read starts this long after the last photo, and shows this many.
ALBUM_WAIT_SECONDS = 2  # ASSUMED, section 5.5
ALBUM_MAX = 10


class DeadFile(Exception):
    """Telegram refused this file id: no read of it will pass (M7e)."""


class SaidNothing(Exception):
    """R16 in a voice bound to a reminder: no dish in its words, so no read of them."""


class Routed(Exception):
    """A voice whose words are no meal of their own (a weight, «ещё хлеб», «убери
    сыр»): it went the way typed words go, and ``line`` is what its line says now.
    None: the line is no longer the read's to edit."""

    def __init__(self, line: Optional["Line"]):
        super().__init__("routed")
        self.line = line


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


def _is_readable_image(mime: Optional[str]) -> bool:
    """A file sent as an image, if the model can see it: HEIC, TIFF and SVG it cannot,
    however many times it is tried, so they go the way of any other unreadable file."""
    return (mime or "").casefold() in READABLE_IMAGES


def incoming(message, user) -> Incoming:
    kind = text = file = None
    if message.photo:
        kind, file, text = "photo", message.photo[-1], message.caption
    elif message.document and _is_readable_image(message.document.mime_type):
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
        created_at=datetime.now(UTC),  # to the instant: an album's wait counts from it
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
    if meal_id is not None:
        map_message(db, came.chat_id, came.message_id, meal_id, "source")
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


def left_meal(db, chat_id: int, message_id: Optional[int]) -> None:
    """The message shows no meal now: a reply to it is a new meal, not a wrong merge."""
    if message_id is None:
        return
    row = db.scalar(
        select(MealMessage).where(
            MealMessage.chat_id == chat_id, MealMessage.message_id == message_id
        )
    )
    if row is not None:
        db.delete(row)
        db.commit()


def tapped(db, chat_id: int, message_id: Optional[int], screen=None) -> None:
    """A button put a screen on the message. Whatever meal it shows, a reply to it
    means; and a line that became a screen is no longer the read's to edit."""
    if message_id is None:
        return
    if screen is not None and screen.view == meal_card.NO_MEAL:
        left_meal(db, chat_id, message_id)
        return
    if screen is not None and screen.meal_id is not None:
        map_message(db, chat_id, message_id, screen.meal_id, screen.view or "card")
        return
    row = db.scalar(
        select(MealMessage).where(
            MealMessage.chat_id == chat_id,
            MealMessage.message_id == message_id,
            MealMessage.view.in_(LINE_VIEWS),
        )
    )
    if row is not None:
        row.view = "card"
        db.commit()


def _claim_view(db, job: "Read") -> bool:
    """Whether the line is still the read's to edit; from here on it is a plain line."""
    if job.line_id is None:
        return job.unsent
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
    view: str = "line"  # how it maps: only the meal's own record line is a "line"
    retouch: Optional[Retouch] = None


def _take_hint(db, user: User) -> bool:
    """M1b, once in a person's life with the bot. It says the food is written, so it
    waits while the person's food waits for ✅."""
    if draft_on(user) or (user.settings or {}).get("capture_hint_seen"):
        return False
    user.settings = {**(user.settings or {}), "capture_hint_seen": True}
    db.commit()
    return True


def _take_voice_hint(db, user: User) -> bool:
    """M4c, once in a person's life with the bot, on a voice that was written."""
    if (user.settings or {}).get("voice_hint_seen"):
        return False
    user.settings = {**(user.settings or {}), "voice_hint_seen": True}
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
    voice_hint: bool = False,
    reading: bool = False,
) -> str:
    """``reading``: a read of the words is out, and the line says so until it ends."""
    what = line_what(db, meal, user)
    if meal.is_draft:
        text = _draft_text(meal, user, what, note, cost, voice_hint)
        return f"{text}\n{M4T_READING}" if reading else text
    head = line_head(meal, user_zone(user), line_plain(meal))
    first = f"{head}: {what}." if what else f"{head}."
    if note:
        first += f" {note}"
    lines = (
        first,
        M4T_READING if reading else None,
        M1B_HINT if hint else None,
        M4C_VOICE_HINT if voice_hint else None,
        cost,
    )
    return "\n".join(line for line in lines if line)


def _draft_text(
    meal: Meal,
    user: User,
    what: str,
    note: Optional[str],
    cost: Optional[str],
    voice_hint: bool,
) -> str:
    """M10b. Its voice hint keeps the disclosure and loses «в дневнике»: not yet."""
    title, *rest = meal_card.draft_lines(meal, user_zone(user), what)
    lines = (
        title,
        *rest[:-1],
        note,
        rest[-1],
        M4C_DRAFT_HINT if voice_hint else None,
        cost,
    )
    return "\n".join(line for line in lines if line)


def _first_markup(db, meal: Meal, join: Optional[list] = None) -> InlineKeyboardMarkup:
    """A draft has its own buttons from its first line on."""
    if meal.is_draft:
        return meal_card.line_markup(db, meal)
    return _reading_markup(meal.id, join)


def _reading_markup(meal_id: int, join: Optional[list] = None) -> InlineKeyboardMarkup:
    """While the items are not there yet the fix opens the meal. A line that stays as
    it is when the read ends has its M1c row from the start."""
    row = [_button(FIX, f"ml:o:{meal_id}:r"), _button(TIME, f"ml:t:{meal_id}:r")]
    return InlineKeyboardMarkup([row, join] if join else [row])


def _undo_markup(meal_id: int, event_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            ok_row(),
            [_button(UNDO, f"ml:u:{event_id}:r"), _button(FIX, f"ml:o:{meal_id}:r")],
        ]
    )


def _fix_markup(meal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[_button(FIX, f"ml:o:{meal_id}:r")]])


def _split_markup(source_id: int, meal_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            ok_row(),
            [_button(SPLIT, f"ml:z:{source_id}:r"), _button(FIX, f"ml:o:{meal_id}:r")],
        ]
    )


def _it_is_food(source_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[_button(IT_IS_FOOD, f"ml:nf:{source_id}")]])


def _failed_line(kind: str, meal: Meal) -> Line:
    text, markup = meal_card.failed_view(kind, meal.id, draft=meal.is_draft)
    return Line(text, markup, meal_id=meal.id)


def _no_food_photo_line(meal: Meal) -> Line:
    text, markup = meal_card.no_food_view(meal.id, draft=meal.is_draft)
    return Line(text, markup, meal_id=meal.id)


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
    db,
    came: Incoming,
    user: User,
    meal: Meal,
    moment: datetime,
    precision: str = meal_card.EXACT,
) -> Optional[Line]:
    change = meal_items.set_time(db, user.id, meal.id, moment, precision)
    if change.status == meal_items.STALE:
        return None
    _keep(db, came, user, meal.id)
    db.refresh(meal)
    what = _what(db, meal)
    when = _when(meal, user_zone(user))
    # A meal still being read has nothing to name yet.
    text = (
        f"Время приёма теперь {when}: {what}."
        if what
        else f"Время приёма теперь {when}."
    )
    if change.status == meal_items.SAME:
        return Line(text, _fix_markup(meal.id), meal_id=meal.id, view="other")
    return Line(
        text, _undo_markup(meal.id, change.event.id), meal_id=meal.id, view="other"
    )


def _grams_line(db, user: User, meal: Meal, item, grams: float) -> Line:
    """M5c."""
    name = meal_card._esc(meal_card._capital(item.name))
    change = meal_items.set_grams(db, user.id, item.id, grams)
    text = f"{name} в приёме {_when(meal, user_zone(user))}: {num(grams)} г."
    if change.event is None:
        return Line(text, _fix_markup(meal.id), meal_id=meal.id, view="other")
    return Line(
        text, _undo_markup(meal.id, change.event.id), meal_id=meal.id, view="other"
    )


def _set_grams(db, came: Incoming, user: User, meal: Meal, grams: float) -> Line:
    items = crud_meal_item.live(db, meal.id)
    if len(items) == 1:
        line = _grams_line(db, user, meal, items[0], grams)
        _keep(db, came, user, meal.id)
        return line
    rows = [_button(_cut(i.name, 14), f"ml:ga:{i.id}:{num(grams)}:r") for i in items]
    chips = [rows[i : i + 3] for i in range(0, len(rows), 3)]
    back = [_button("⬅️ К ответу бота", f"ml:r:{meal.id}")]
    _keep(db, came, user)
    return Line(
        f"<b>{num(grams)} г: у какого блюда?</b>\n{M6A_NOT_ASKED}\n"
        f"Приём {_when(meal, user_zone(user))}: {_what(db, meal)}.",
        InlineKeyboardMarkup([*chips, back]),
        meal_id=meal.id,
        view="other",
    )


def _settle(db, source: MealSource, meal: Meal) -> None:
    """What this message changed is done: it belongs to that meal now."""
    source.meal_id = meal.id
    if source.origin_meal_id is None:
        source.origin_meal_id = meal.id
    db.commit()
    map_message(db, source.chat_id, source.message_id, meal.id, "source")


def _born_row(
    db, source: MealSource, user: User, said: Optional[SaidTime] = None
) -> Meal:
    """The message as its own meal, with its own time, whatever becomes of it next."""
    forward = _aware(source.forward_date) if source.forward_date else None
    sent = _aware(source.sent_at)
    meal = crud_meal.build(
        MealCreate(
            sent_at=sent,
            photos=[],
            numbers_basis="none",
            **_time_columns(sent, forward, said),
        ),
        user.id,
    )
    meal.is_draft = draft_on(user)
    db.add(meal)
    db.flush()
    source.meal_id = source.origin_meal_id = meal.id
    db.commit()
    map_message(db, source.chat_id, source.message_id, meal.id, "source")
    return meal


def _with_dishes(db, user: User, meal: Meal, dishes: list) -> list:
    """The changes that put ``dishes`` on a meal that was just born."""
    changes = []
    for dish in dishes:
        change = meal_items.add_item(db, user.id, meal.id, **dish)
        if change.status != meal_items.DONE:
            raise RuntimeError("a dish of a new meal is gone")
        changes.append(change)
    return changes


def _dish_of(item) -> dict:
    if item.personal_food_id:
        return {"food_id": item.personal_food_id}
    return {"name": item.name}


def _added_line(
    user: User,
    target: Meal,
    source_id: int,
    names: list,
    fill: Optional[meal_card.Fill] = None,
) -> Line:
    """M5, M6. The line names the meal, so a wrong target is visible."""
    listed = _names([meal_card._esc(name) for name in names])
    return Line(
        f"Добавлено к приёму {_when(target, user_zone(user))}: {listed}.",
        _split_markup(source_id, target.id),
        fill=fill,
        meal_id=target.id,
        view="other",
    )


def _stays_its_own_draft(meal: Meal, target: Meal) -> bool:
    """A meal that waits for ✅ does not count by way of one that has it: joined, it
    would be in the day with nobody having said so."""
    return bool(meal.is_draft) and not target.is_draft


def _joined_line(
    db,
    source: MealSource,
    user: User,
    target: Meal,
    dishes: list,
    said: Optional[SaidTime] = None,
) -> Line:
    """Born as its own meal first, then joined: that is why ➖ Отделить always works.
    If the target went in between, the born meal stands as it is."""
    new = len(dishes) == 1 and "name" in dishes[0]
    new = new and _is_new_name(db, user, dishes[0]["name"])
    meal = _born_row(db, source, user, said)
    changes = _with_dishes(db, user, meal, dishes)
    if _stays_its_own_draft(meal, target):
        text = _record_text(db, meal, user)
        return Line(
            text,
            meal_card.line_markup(db, meal),
            meal_id=meal.id,
            fill=_fill_of(changes[0], new),
        )
    names = [item.name for item in crud_meal_item.live(db, meal.id)]
    joined = meal_items.join(db, user.id, target.id, meal.id, source_id=source.id)
    if joined.status != meal_items.DONE:
        text = _record_text(db, meal, user)
        return Line(text, meal_card.line_markup(db, meal), meal_id=meal.id)
    return _added_line(user, target, source.id, names, _fill_of(joined, new))


def _add(db, came: Incoming, user: User, meal: Meal, name: str) -> Optional[Line]:
    said = _said(name, "text", came.sent_at, came.forward_date, user)
    name = said.rest if said else name
    if not meal_items._clean_name(name):
        return None
    source = _keep(db, came, user)
    return _joined_line(db, source, user, meal, [{"name": name}], said)


def _helping(db, source: MealSource, user: User, meal: Meal, items: list) -> Line:
    """A new item of the same dish, both captures kept, and no count in any string."""
    return _joined_line(db, source, user, meal, [_dish_of(item) for item in items])


def _take_out(db, source: MealSource, user: User, meal: Meal, item) -> Line:
    """The last dish takes the meal with it, softly, and the way back still stands."""
    when, gone = _when(meal, user_zone(user)), item.name
    change = meal_items.remove_item(db, user.id, item.id)
    if change.status not in (meal_items.DONE, meal_items.DELETED):
        return Line(TARGET_GONE)
    _settle(db, source, meal)
    return Line(
        f"Убрано из приёма {when}: {meal_card._esc(gone)}.",
        _undo_markup(meal.id, change.event.id),
        meal_id=meal.id,
        view="other",
    )


def _swap(db, source: MealSource, user: User, meal: Meal, item, name: str) -> Line:
    when, was = _when(meal, user_zone(user)), item.name
    new = _is_new_name(db, user, name)
    change = meal_items.replace_item(db, user.id, item.id, name=name)
    if change.status == meal_items.SAME:
        _settle(db, source, meal)
        return Line(
            meal_items.SAME_TOAST, _fix_markup(meal.id), meal_id=meal.id, view="other"
        )
    if change.status != meal_items.DONE:
        return Line(TARGET_GONE)
    _settle(db, source, meal)
    return Line(
        f"Заменено в приёме {when}: {meal_card._esc(was)} → {meal_card._esc(name)}.",
        _undo_markup(meal.id, change.event.id),
        fill=_fill_of(change, new),
        meal_id=meal.id,
        view="other",
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
    separate: bool = True,
) -> Line:
    """M6a: nothing applied, the source kept, and ignoring it changes nothing."""
    source = _keep(db, came, user)
    few = len(items) <= 4
    limit, per_row = (14, 2) if few else (meal_card.CHIP_MAX, meal_card.CHIPS_ROW)
    chips = [_button(_cut(i.name, limit), f"ml:ka:{source.id}:{i.id}") for i in items]
    rows = [chips[i : i + per_row] for i in range(0, len(chips), per_row)]
    if whole:
        rows.append([_button(WHOLE_MEAL, f"ml:kw:{source.id}")])
    if separate:
        rows.append([_button(SEPARATE, f"ml:kn:{source.id}")])
    words = meal_card._esc(_cut(came.text or "", 200))
    return Line(
        f"<b>{title}</b>\n{M6A_NOT_ASKED}\n"
        f"«{words}», приём {_when(meal, user_zone(user))}",
        InlineKeyboardMarkup(rows),
        meal_id=meal.id,
        view="other",
    )


def _apply_correction(
    db, came: Incoming, user: User, meal: Meal, correction: tuple
) -> Optional[Line]:
    """None: nothing here to change, and the words are a new meal."""
    what, name, old = correction
    if what == ADD:
        return _add(db, came, user, meal, name)
    items = crud_meal_item.live(db, meal.id)
    if not items:
        return None
    if what == REPEAT:
        if len({(i.personal_food_id or i.name) for i in items}) == 1:
            items = items[:1]  # the same dish twice is still one dish to choose
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
        return _helping(db, _keep(db, came, user), user, meal, items)
    if what == REMOVE:
        found = _matching(items, name)
        if len(found) != 1:
            return _chooser(
                db, came, user, meal, items, "Какое блюдо это меняет?", separate=False
            )
        return _take_out(db, _keep(db, came, user), user, meal, found[0])
    found = _matching(items, old or name)
    if len(found) != 1:
        # One dish takes a name directly, not a sentence: «это был отличный день» has
        # to ask, or it overwrites the only dish.
        named = old is not None or len(name.split()) == 1
        found = items if len(items) == 1 and named else []
    if not found:
        return _chooser(db, came, user, meal, items, "Какое блюдо это меняет?")
    return _swap(db, _keep(db, came, user), user, meal, found[0], name)


# --- the meal's original line ---------------------------------------------------------


def _last_event(db, meal: Meal) -> int:
    latest = db.scalar(
        select(RecordEvent.id)
        .where(RecordEvent.meal_id == meal.id)
        .order_by(RecordEvent.id.desc())
        .limit(1)
    )
    return latest or 0


def _read_still_lands(db, meal: Meal) -> bool:
    """A read is out, and the last thing done to the meal does not stand in its way."""
    if meal.reading_status != "pending":
        return False
    kind = db.scalar(
        select(RecordEvent.kind)
        .where(RecordEvent.meal_id == meal.id)
        .order_by(RecordEvent.id.desc())
        .limit(1)
    )
    return kind in READ_SURVIVES


def _retouch_of(db, user: User, meal: Meal, known: str, rev) -> Optional[Retouch]:
    """The line of a meal that changed, if it still is a line and says something else
    now. A line still on «читаю» stops saying it: its late read will not apply. Unless
    all that changed is a time or what joined it: that read still lands, on this line.
    """
    db.refresh(meal)
    if meal.deleted_at is not None or (meal.rev or 0) == (rev or 0):
        return None
    row = _line_of(db, meal.id)
    if row is None:
        return None
    if row.view == "pending" and _read_still_lands(db, meal):
        return None
    text = _record_text(db, meal, user)
    if text == known and row.view != "pending":
        return None
    if row.view == "pending":
        row.view = "line"
        db.commit()
    return Retouch(row.message_id, text, meal_card.line_markup(db, meal))


def _touching(db, user: User, meal: Meal, apply) -> Optional[Line]:
    known, rev = _record_text(db, meal, user), meal.rev
    line = apply()
    if line is not None:
        line.retouch = _retouch_of(db, user, meal, known, rev)
    return line


def _aware(moment: datetime) -> datetime:
    """SQLite reads a stored moment back without its zone; stored is always UTC."""
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _sent(meal: Meal) -> datetime:
    return _aware(meal.sent_at or meal.created_at)


def _last_meal(
    db, user: User, within: timedelta, before: Optional[int] = None
) -> Optional[Meal]:
    """``before``: a voice heard late means the meal before its own, not one written
    while it was being heard."""
    stmt = (
        select(Meal)
        # A draft is the last meal too: «ещё сыр» means it, not the one before it.
        .where(Meal.user_id == user.id, Meal.deleted_at.is_(None))
        .order_by(Meal.id.desc())
        .limit(1)
    )
    if before is not None:
        stmt = stmt.where(Meal.id < before)
    meal = db.scalar(stmt)
    if meal is None or _now() - _sent(meal) >= within:
        return None
    return meal


def _unheard(db, meal: Meal) -> bool:
    """A voice not heard yet: what it says may be a change of its own, so a change
    typed meanwhile has no meal it surely belongs to."""
    if meal.reading_status != "pending" or meal_card.heard_of(db, meal.id) is not None:
        return False
    voice_source = db.scalar(
        select(MealSource.id)
        .where(MealSource.origin_meal_id == meal.id, MealSource.kind == VOICE)
        .limit(1)
    )
    return voice_source is not None


def _still_reading(db, came: Incoming, user: User, meal: Optional[Meal]) -> bool:
    if meal is None or not _unheard(db, meal):
        return False
    _keep(db, came, user)
    return True


def _replied_meal(db, chat_id: int, message_id: int, user: User) -> Optional[Meal]:
    """The meal a message answers for, wherever it went since. Deleted ones too: the
    caller says so. Only this person's: another's row means nothing here."""
    row = db.scalar(
        select(MealMessage).where(
            MealMessage.chat_id == chat_id, MealMessage.message_id == message_id
        )
    )
    meal = db.get(Meal, row.meal_id) if row else None
    for _ in range(_MERGES_MAX):
        if meal is None or meal.merged_into_id is None:
            break
        meal = db.get(Meal, meal.merged_into_id)
    return meal if meal is not None and meal.user_id == user.id else None


# --- the router -----------------------------------------------------------------------


@dataclass
class Route:
    action: str
    line: Optional[Line] = None
    tick: Optional[str] = None
    note: Optional[str] = None
    words: Optional[str] = None  # NEW_MEAL: the food, without what was said to the meal
    retouch: Optional[meal_card.Retouch] = None  # SILENT: a message of ours to redraw


def _clock_in(text: str) -> Optional[tuple]:
    """(yesterday?, clock) of a bare clock, with its preposition or not."""
    return meal_card.parse_time(_CLOCK_IN.sub(r"\1", text, count=1))


def _bare_time(text: str, user: User) -> Optional[SaidTime]:
    """A reply of nothing but time words: «в 13», «утром», «час назад»."""
    said = time_words(text, _now(), user_zone(user), bare_ok=True)
    return said if said is not None and not said.rest else None


def _reply_moment(said: SaidTime, meal: Meal, user: User) -> datetime:
    """A clock with no day named is of the replied meal's own day."""
    if not said.of_day:
        return said.moment
    clock = local_time(said.moment, user_zone(user)).time()
    return datetime.combine(meal_card.meal_day(meal, user), clock, user_zone(user))


def _reply_on_deleted(
    db, came: Incoming, user: User, grams, correction, time_only: bool
) -> Optional[Route]:
    """M5a: food stands on its own. A change has nothing left to change: no new meal
    could be true of «убери сыр», «добавка» or «200»."""
    if time_only:
        return Route(LINE, Line(M5F_NO_MEAL))
    if grams is not None or (correction and correction[0] in (REMOVE, REPEAT)):
        _keep(db, came, user)
        return Route(LINE, Line(TARGET_GONE))
    if correction:
        return Route(NEW_MEAL, note=M5A_DELETED, words=correction[1])
    return Route(NEW_MEAL, note=M5A_DELETED)


def _on_reply(
    db, came: Incoming, user: User, meal: Meal, heard: bool = False
) -> Optional[Route]:
    """Row 4. A bare time before bare grams: «12:10» is no number. None: this reply is
    none of the meal's business, and the rows below decide. ``heard``: the words of a
    voice, whose food is read and joined at the end of its read, as it always was."""
    text = came.text
    colon = _COLON_CLOCK.fullmatch(text)
    said = None if colon else _bare_time(text, user)
    grams = None if colon or said else meal_card.parse_grams(text)
    if text.rstrip().endswith("?") or _is_stop_word(text):
        return None
    correction = _correction(text)
    if meal_card.waits_for_words(db, meal):
        return (
            None  # nothing stands there to change: these words are a meal of their own
        )
    if meal.deleted_at is not None:
        time_only = bool(colon or said)
        return _reply_on_deleted(db, came, user, grams, correction, time_only)
    if colon:
        parsed = _clock_in(text)
        if parsed is None:
            return Route(LINE, Line(M5F_NO_MEAL))
        moment, precision = _moment(meal, user, parsed[0], parsed[1]), meal_card.EXACT
    elif said:
        moment, precision = _reply_moment(said, meal, user), said.precision
    if colon or said:
        if moment > _now():
            return Route(LINE, Line(meal_card.TIME_FUTURE))
        line = _touching(
            db,
            user,
            meal,
            lambda: _set_time(db, came, user, meal, moment, precision),
        )
    elif grams is not None:
        line = _touching(
            db, user, meal, lambda: _set_grams(db, came, user, meal, grams)
        )
    elif correction:
        line = _touching(
            db,
            user,
            meal,
            lambda: _apply_correction(db, came, user, meal, correction),
        )
        _renews(db, came, user, line)
    elif heard:
        return None
    else:
        line = _touching(
            db, user, meal, lambda: _add(db, came, user, meal, text.strip())
        )
        _renews(db, came, user, line)
    return Route(LINE, line) if line else None


def _is_stop_word(text: str) -> bool:
    word = text.strip().strip(".,!…)( ").casefold()
    return word in STOP_WORDS or _says_no(text)


def route_media(db, came: Incoming) -> Route:
    """A photo or a voice note is always born and read; if it answered a live meal it
    joins it at the end of the read, and a deleted one is said aloud in its line."""
    user = _user(db, came)
    if came.reply_to is not None:
        if _replied_send(db, came, user) is not None:
            # §3.2(a): a meal of its own on the reminder's day, never the ⭐ it holds.
            return Route(NEW_MEAL)
        target = _replied_meal(db, came.chat_id, came.reply_to, user)
        if (
            target is not None
            and target.deleted_at is not None
            and not meal_card.waits_for_words(db, target)
        ):
            return Route(NEW_MEAL, note=M5A_DELETED)
    return Route(NEW_MEAL)


def _timed_add(came: Incoming, user: User, correction) -> bool:
    """«а ещё днём был йогурт»: a corrective add that says a time of its own."""
    if not correction or correction[0] != ADD or not correction[1]:
        return False
    return time_words(correction[1], _aware(came.sent_at), user_zone(user)) is not None


def _own_meal_add(came: Incoming, user: User, correction) -> bool:
    """A corrective add that is a meal of its own under a reminder: a time of its own
    («а ещё днём был йогурт»), or food eaten now («а ещё сейчас яблоко»)."""
    if _timed_add(came, user, correction):
        return True
    return (
        bool(correction) and correction[0] == ADD and reminders.says_now(correction[1])
    )


def _recalls_more(db, came: Incoming, user: User, correction) -> bool:
    """A corrective add of a meal of its own, sent while a reminder is answered.
    One of food eaten now is never bound (born leaves it at its own sent time),
    and it is never a dish of the recalled no-clock answer either."""
    if not _own_meal_add(came, user, correction):
        return False
    return reminders.answering(db, user, came) is not None


# «а ещё днём был йогурт»: the verb is no dish, and a known «йогурт» costs no read.
_RECALL_VERB = re.compile(r"(?<!\w)(?:был[аио]?|ещё|еще)(?!\w)\s*", re.IGNORECASE)


def _recalled(rest: str) -> str:
    return _RECALL_VERB.sub("", rest).strip(" ,") or rest


def _replied_send(db, came: Incoming, user: Optional[User]):
    """§3.2(a): the 🔔 reminder this message replies to, if it is one."""
    return reminders.replied_send(db, user, came.chat_id, came.reply_to, came.sent_at)


def _for_the_star(came: Incoming, user: User) -> bool:
    """A reply to a reminder that holds a ⭐ changes that ⭐ only by a bare time,
    grams or a correction; any other words are a meal of their own (§3.2(a))."""
    text = came.text
    if _COLON_CLOCK.fullmatch(text) or _bare_time(text, user):
        return True
    if meal_card.parse_grams(text) is not None:
        return True
    correction = _correction(text)
    return bool(correction) and not _own_meal_add(came, user, correction)


def _renews(db, came: Incoming, user: User, line: Optional[Line]) -> Optional[Line]:
    """§3.2: words that changed a meal inside a reminder's window are bound too, and
    run the window on from them, as a meal of their own would."""
    if line is not None:
        send = reminders.bound_send(db, user, came)
        if send is not None:
            reminders.answered(db, user, send, came)
    return line


def _nothing(db, came: Incoming, user: User, send) -> Route:
    """R16: an answer with no dish in it, so nothing to read and no line."""
    _keep(db, came, user)
    retouch = reminders.accept_nothing(db, user, send, came)
    return Route(SILENT, retouch=retouch)


def route_text(
    db, came: Incoming, as_food: bool = False, *, heard: Optional[int] = None
) -> Route:
    """Rows 3 to 10 for a text. The first two are ahead of this handler. ``as_food``: the
    person said it is food, so its form does not say otherwise. ``heard``: the meal of
    a voice whose words these are; only a meal before it is the «last» one."""
    text = came.text
    if text.strip() in STALE_LABELS:
        return Route(LINE, Line(M9B_STALE, menu=True))
    user = _user(db, came)

    target = send = None
    if came.reply_to is not None:
        target = _replied_meal(db, came.chat_id, came.reply_to, user)
        send = _replied_send(db, came, user)
    if send is not None and not as_food and reminders.is_nothing(text):
        return _nothing(db, came, user, send)  # never the ⭐ the reminder holds
    if target is not None and (send is None or _for_the_star(came, user)):
        route = _on_reply(db, came, user, target, heard=heard is not None)
        if route is not None:
            return route
    if weight_screen.KG_PATTERN.match(text):
        return Route(WEIGHT)
    name = stats_screen.tick_by_name(db, came.telegram_id, text)
    if name is not None:
        return Route(TICK, tick=name)

    if _COLON_CLOCK.fullmatch(text):
        parsed = _clock_in(text)
        meal = _last_meal(db, user, TIME_WINDOW, heard)
        if _still_reading(db, came, user, meal):
            return Route(LINE, Line(STILL_READING))
        if parsed is None or meal is None:
            return Route(LINE, Line(M5F_NO_MEAL))
        moment = _moment(None, user, parsed[0], parsed[1])
        line = _touching(
            db, user, meal, lambda: _set_time(db, came, user, meal, moment)
        )
        return Route(LINE, line or Line(M5F_NO_MEAL))

    if not as_food and reminders.is_nothing(text):
        # R16 ahead of the corrective words: «нет, ничего» is no «это было …».
        send = reminders.bound_send(db, user, came)
        if send is not None:
            return _nothing(db, came, user, send)

    correction = _correction(text)
    if correction and _recalls_more(db, came, user, correction):
        # §3.2: «а ещё днём был йогурт» after an answer is a meal of its own, at
        # that time; «а ещё хлеб» still joins the first answer below.
        return Route(NEW_MEAL, words=_recalled(correction[1]))
    if correction:
        meal = _last_meal(db, user, CORRECTION_WINDOW, heard)
        if _still_reading(db, came, user, meal):
            return Route(LINE, Line(STILL_READING))
        line = None
        if meal is not None:
            line = _touching(
                db,
                user,
                meal,
                lambda: _apply_correction(db, came, user, meal, correction),
            )
        if _renews(db, came, user, line) is not None:
            return Route(LINE, line)
        logger.info("Corrective words with no meal to change: a new meal")

    if as_food:
        return Route(NEW_MEAL)
    if _is_stop_word(text):
        _keep(db, came, user)
        return Route(SILENT)
    if text.rstrip().endswith("?"):
        source = _keep(db, came, user)
        return Route(LINE, Line(M3B_QUESTION, _it_is_food(source.id)))
    return Route(NEW_MEAL)


# --- M6a, M5d: the chips --------------------------------------------------------------


def _screen_of(line: Line) -> meal_card.Screen:
    if line.text == meal_items.SAME_TOAST:
        return meal_card.Screen("", None, toast=meal_items.SAME_TOAST)
    return meal_card.Screen(
        line.text,
        line.markup,
        meal_id=line.meal_id,
        view=line.view,
        fill=line.fill,
        retouch=line.retouch,
    )


def _own_source(t, sid: str, data: str) -> MealSource:
    source = meal_card._row(t.db, MealSource, int(sid))
    if source is None or t.user is None or source.user_id != t.user.id:
        raise meal_card._Answer(meal_card._unknown(t, data))
    return source


def _chosen_words(t, source: MealSource, data: str) -> tuple:
    """What the kept words say. A source with a meal has been answered: a double tap
    changes nothing."""
    if source.meal_id is not None:
        raise meal_card._Answer(meal_card.Screen("", None, toast=meal_items.SAME_TOAST))
    words = _correction(source.text or "")
    if words is None or words[0] == ADD:
        raise meal_card._Answer(meal_card._unknown(t, data))
    return words


def _chooser_meal(t, source: MealSource, data: str) -> Meal:
    """The meal of the chooser this tap is on: the callback carries only the source."""
    meal = None
    if t.message_id is not None:
        meal = _replied_meal(t.db, source.chat_id, t.message_id, t.user)
    if meal is None:
        raise meal_card._Answer(meal_card._unknown(t, data))
    if meal.deleted_at is not None:
        raise meal_card._Answer(meal_card._stale_meal(t))
    return meal


def _v_choose(t, data: str, sid: str, iid: str) -> meal_card.Screen:
    """A dish chip of M6a."""
    source = _own_source(t, sid, data)
    what, name, _old = _chosen_words(t, source, data)
    item, meal = meal_card._own_item(t, int(iid), "r", data)

    def apply() -> Line:
        if what == REPEAT:
            return _helping(t.db, source, t.user, meal, [item])
        if what == REMOVE:
            return _take_out(t.db, source, t.user, meal, item)
        return _swap(t.db, source, t.user, meal, item, name)

    return _screen_of(_touching(t.db, t.user, meal, apply))


def _v_choose_whole(t, data: str, sid: str) -> meal_card.Screen:
    """🍽 Весь приём: the second helping of everything on the plate."""
    source = _own_source(t, sid, data)
    what, _name, _old = _chosen_words(t, source, data)
    if what != REPEAT:
        return meal_card._unknown(t, data)
    meal = _chooser_meal(t, source, data)
    items = crud_meal_item.live(t.db, meal.id)
    if not items:
        return meal_card._stale_meal(t)
    line = _touching(
        t.db, t.user, meal, lambda: _helping(t.db, source, t.user, meal, items)
    )
    return _screen_of(line)


def _v_choose_separate(t, data: str, sid: str) -> meal_card.Screen:
    """➕ Отдельный приём: the words were not about that meal at all."""
    source = _own_source(t, sid, data)
    what, name, _old = _chosen_words(t, source, data)
    if what == REMOVE:
        return meal_card._unknown(t, data)
    if what == REPEAT:
        taken = _chooser_meal(t, source, data)
        dishes = [_dish_of(item) for item in crud_meal_item.live(t.db, taken.id)]
        new = False
    else:
        dishes = [{"name": name}]
        new = _is_new_name(t.db, t.user, name)
    meal = _born_row(t.db, source, t.user)
    changes = _with_dishes(t.db, t.user, meal, dishes)
    return meal_card.Screen(
        _record_text(t.db, meal, t.user),
        # A relation fired and the person said apart: M1c has nothing to add.
        meal_card.line_markup(t.db, meal, joinable=False),
        meal_id=meal.id,
        view="line",
        fill=_fill_of(changes[0], new),
    )


def _v_grams_chip(t, data: str, iid: str, typed: str, back: str) -> meal_card.Screen:
    """M5d: the typed grams at once, so the number is never asked for twice."""
    grams = meal_card.parse_grams(typed)
    if grams is None:
        return meal_card._unknown(t, data)
    item, meal = meal_card._own_item(t, int(iid), back, data)
    return _screen_of(_grams_line(t.db, t.user, meal, item, grams))


_SID = r"(\d{1,18})"
meal_card.add_verbs(
    (re.compile(rf"ml:ka:{_SID}:{_SID}"), _v_choose),
    (re.compile(rf"ml:kw:{_SID}"), _v_choose_whole),
    (re.compile(rf"ml:kn:{_SID}"), _v_choose_separate),
    (
        re.compile(rf"ml:ga:{_SID}:(\d{{1,4}}(?:[.,]\d{{1,2}})?):{meal_card._BACK}"),
        _v_grams_chip,
    ),
)


# --- M7: the read did not end well ----------------------------------------------------


def _v_again(t, data: str, mid: str, back: str) -> meal_card.Screen:
    """🔁 Прочитать заново. A read already out is paid for, so a second tap asks nothing; the
    message tapped is the one the answer lands on, whichever screen it was."""
    meal = meal_card._own_meal(t, int(mid), data)
    if meal.reading_status == "pending":
        return meal_card.Screen("", None, toast=AGAIN_TOAST)
    has_words = meal.reading_status == "read" and not meal_card._unread(meal, [])
    stands = crud_meal_item.live(t.db, meal.id)
    if meal.reading_status == "failed" and not _own_items(meal, stands):
        stands = []  # what was joined to it is all there is: its own read is still owed
    if stands or has_words:
        # A record stands: only the button of M13 or M11 pays to read it anew. The
        # 🔁 Прочитать заново of an old line, tapped late, costs nothing.
        if _shows_read_anew(t, meal):
            return _read_anew(t, meal)
        return meal_card._v_line(t, data, mid)
    job = read_again(t.db, meal.id)
    if job is None:
        return meal_card.Screen("", None, toast=AGAIN_FAILED_TOAST)
    meal.read_attempts = (meal.read_attempts or 0) + 1
    other = _line_of(t.db, meal.id)
    if other is not None and other.message_id != t.message_id:
        other.view = "line"
    t.db.commit()
    if job.kind == TEXT:
        first = _record_text(t.db, meal, t.user, reading=True)
        markup = _first_markup(t.db, meal)
    else:
        first, markup = meal_card.pending_text(job.kind, meal), None
    return meal_card.Screen(
        first,
        markup,
        toast=AGAIN_TOAST,
        meal_id=meal.id,
        view="pending",
        read=replace(job, line_id=t.message_id, shown=first, again=True),
    )


def _own_items(meal: Meal, items: list) -> list:
    return [item for item in items if item.origin_meal_id == meal.id]


def _shows_read_anew(t, meal: Meal) -> bool:
    return (
        t.db.scalar(
            select(MealMessage.id).where(
                MealMessage.meal_id == meal.id,
                MealMessage.message_id == t.message_id,
                MealMessage.view == READ_ANEW_VIEW,
            )
        )
        is not None
    )


def _read_anew(t, meal: Meal) -> meal_card.Screen:
    """🔁 Прочитать заново: the meal's own message as it stands now, paid for on this
    tap. The record stays until the answer is there."""
    job = _anew_job(t.db, meal, t.message_id)
    if job is None:
        return meal_card.Screen("", None, toast=AGAIN_FAILED_TOAST)
    meal.reading_status = "pending"
    meal.read_attempts = (meal.read_attempts or 0) + 1
    t.db.commit()
    if job.kind == TEXT:
        first = _record_text(t.db, meal, t.user, reading=True)
        markup = _first_markup(t.db, meal)
    else:
        first, markup = meal_card.pending_text(job.kind, meal), None
    return meal_card.Screen(
        first,
        markup,
        toast=AGAIN_TOAST,
        meal_id=meal.id,
        view="pending",
        read=replace(job, shown=first),
    )


def _v_write(t, data: str, mid: str, back: str) -> meal_card.Screen:
    """M7b: ✏️ Написать, on the same message."""
    meal = meal_card.own_or_waiting(t, int(mid), data)
    meal_card._open_slot(t, meal_card.MEAL_TEXT, back, meal_id=meal.id)
    row = meal_card._back_row(t, meal, back)
    return meal_card.Screen(
        M7B_ASK, InlineKeyboardMarkup([row]), meal_id=meal.id, view="card"
    )


def _typed_meal_text(t, ask: dict, text: str) -> Optional[meal_card.Screen]:
    """M7c: the words are this meal's reading, and what it came with stays. Words that
    failed are no dish beside these, and /reprocess pays for nothing here any more.
    A dish already known is free; new words are read like any text sent as food."""
    meal = meal_card.own_or_waiting(t, ask["meal_id"], "slot")
    if t.said and _is_new_name(t.db, t.user, text):
        return _words_to_read(t, meal, text)
    gone_at = meal.deleted_at
    meal.deleted_at = None  # a voice that waited for words: they came
    t.db.flush()
    first = t.db.scalar(
        select(MealSource)
        .where(MealSource.meal_id == meal.id)
        .order_by(MealSource.id)
        .limit(1)
    )
    if not crud_meal_item.live(t.db, meal.id):
        meal.description = None
    new = _is_new_name(t.db, t.user, text)
    change = meal_items.add_item(t.db, t.user.id, meal.id, name=text)
    if change.status != meal_items.DONE:
        meal.deleted_at = gone_at
        t.db.commit()
        return None if change.meal is not None else meal_card._stale_meal(t)
    meal.reading_status = "read"
    source = None
    if t.said:
        chat_id, message_id, sent_at = t.said
        source = MealSource(
            user_id=t.user.id,
            meal_id=meal.id,
            origin_meal_id=meal.id,
            kind="text",
            chat_id=chat_id,
            message_id=message_id,
            sent_at=sent_at,
            text=text,
        )
        t.db.add(source)
        t.db.flush()
    t.db.add(
        MealReading(
            meal_id=meal.id,
            source_ids=[source.id] if source else [],
            kind=_READING_KIND[TEXT],
            output={"foods": [item.name for item in change.items]},
            status="done",
            applied=True,
            created_at=_now(),
        )
    )
    t.db.commit()
    if source is not None:
        map_message(t.db, source.chat_id, source.message_id, meal.id, "source")
    if first is not None:
        row = _inbound_of(t.db, t.user.telegram_id, first)
        if row is not None:
            crud_inbound_message.mark_analyzed(t.db, row, {"meal": meal.id})
    t.fill = _fill_of(change, new)
    return meal_card.Screen(
        _record_text(t.db, meal, t.user, note=M7_NOTE if meal.photos else None),
        meal_card.line_markup(t.db, meal),
        meal_id=meal.id,
        view="line",
    )


def _words_to_read(t, meal: Meal, text: str) -> meal_card.Screen:
    """M7c for new words: no dish named by a whole sentence. The model splits them into
    foods with their numbers, and the answer edits the line sent here."""
    chat_id, message_id, sent_at = t.said
    first_source = _originals(t.db, meal.id)[0]
    meal.deleted_at = None  # a voice that waited for words: they came
    if not crud_meal_item.live(t.db, meal.id):
        meal.description = text
    meal.reading_status = "pending"
    source = MealSource(
        user_id=t.user.id,
        meal_id=meal.id,
        origin_meal_id=meal.id,
        kind="text",
        chat_id=chat_id,
        message_id=message_id,
        sent_at=sent_at,
        text=text,
    )
    t.db.add(source)
    t.db.commit()
    map_message(t.db, chat_id, message_id, meal.id, "source")
    row = _inbound_of(t.db, t.user.telegram_id, first_source)
    if row is not None:
        crud_inbound_message.mark_analyzed(t.db, row, {"meal": meal.id})
    note = M7_NOTE if meal.photos else None
    shown = _record_text(t.db, meal, t.user, note=note, reading=True)
    return meal_card.Screen(
        shown,
        _first_markup(t.db, meal),
        meal_id=meal.id,
        view="pending",
        read=Read(
            meal_id=meal.id,
            rev=meal.rev or 0,
            source_id=source.id,
            telegram_id=t.user.telegram_id,
            chat_id=chat_id,
            kind=TEXT,
            file_id=None,
            text=text,
            shown=shown,
            note=note,
            typed=True,
            last_event=_last_event(t.db, meal),
        ),
    )


def _typed_words_stand(db, job: "Read", meal: Meal, nutrition: dict, model) -> Line:
    """The model saw no food in words typed to a meal: the person knows better. They
    stay as written, with no numbers, and the photo stays beside them."""
    _store_reading(db, job, status="no_food", output=nutrition, model=model)
    user = db.get(User, meal.user_id)
    if not crud_meal_item.live(db, meal.id):
        meal.description = None  # or the same words open as a line of their own
    meal_items.add_item(db, user.id, meal.id, name=job.text)
    meal.reading_status = "read"
    db.commit()
    text = _record_text(db, meal, user, note=job.note)
    return Line(text, meal_card.line_markup(db, meal), meal_id=meal.id)


def _v_it_is_food(t, data: str, sid: str) -> meal_card.Screen:
    """🍽 Это еда under M3a: the words at the time they came. The model has said what
    it thinks of them, so nobody asks it again, only for the numbers of a new name."""
    source = _own_source(t, sid, data)
    if source.meal_id is not None:
        return meal_card._v_line(t, data, str(source.meal_id))
    # The words as they stand now, if the message was edited since.
    words = latest_version(t.db, source).text
    said = _said(words, source.kind, source.sent_at, source.forward_date, t.user)
    name = (said.rest if said else words) or ""
    if not name.strip():
        return meal_card._unknown(t, data)
    new = _is_new_name(t.db, t.user, name)
    meal = _born_row(t.db, source, t.user, said)
    changes = _with_dishes(t.db, t.user, meal, [{"name": name}])
    return meal_card.Screen(
        _record_text(t.db, meal, t.user),
        meal_card.line_markup(t.db, meal),
        meal_id=meal.id,
        view="line",
        fill=_fill_of(changes[0], new),
    )


def _v_heard(t, data: str, mid: str, want: str) -> meal_card.Screen:
    """🎙 Текст, M4a and M4b: what was heard, in place. Not a state kept anywhere: the
    button says which way, so a second tap on an old one lands on the same screen."""
    meal = meal_card._own_meal(t, int(mid), data)
    heard = meal_card.heard_of(t.db, meal.id)
    if heard is None or not crud_meal_item.live(t.db, meal.id):
        return meal_card._v_line(t, data, mid)
    opened = want == "1"
    text = _record_text(t.db, meal, t.user)
    if opened:
        quoted = meal_card._esc(meal_card._cut(heard, HEARD_SHOWN_MAX))
        text += f"\n<blockquote expandable>{quoted}</blockquote>"
    markup = meal_card.line_markup(t.db, meal, heard_open=opened)
    return meal_card.Screen(text, markup, meal_id=meal.id, view="line")


def _v_record(t, data: str, mid: str) -> meal_card.Screen:
    """✅ Записать: from here on it counts. A read still out keeps its way in, so ``rev``
    does not move; and a second tap on an old button lands on the same line."""
    meal = meal_card._own_meal(t, int(mid), data)
    if meal.is_draft:
        meal.is_draft = False  # ``updated_at`` moves with it: a pull sees the row now
        t.db.commit()
    if not crud_meal_item.live(t.db, meal.id):
        return meal_card._v_line(t, data, mid)
    return _screen_of(_standing_line(t.db, meal))


meal_card.add_verbs(
    (re.compile(rf"ml:ok:{_SID}"), _v_record),
    (re.compile(rf"ml:rr:{_SID}:{meal_card._BACK}"), _v_again),
    (re.compile(rf"ml:w:{_SID}:{meal_card._BACK}"), _v_write),
    (re.compile(rf"ml:nf:{_SID}"), _v_it_is_food),
    (re.compile(rf"ml:vt:{_SID}:([01])"), _v_heard),
)
meal_card._TYPED[meal_card.MEAL_TEXT] = _typed_meal_text


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
    again: bool = False  # 🔁 Прочитать заново: a file gone by now is said to be gone
    tap: Optional[str] = None  # the callback query it answers, if it fails
    file_ids: tuple = ()  # an album: what the model is shown, in order of arrival
    source_ids: tuple = ()
    replaces: bool = False  # a straggler's re-read, over a reading that stands
    alone: bool = False  # a photo split off its album: never read with it again
    anew: bool = False  # 🔁 Прочитать заново: what the person typed stays
    last_event: int = 0  # the newest event of the meal when the read went out
    unsent: bool = False  # Telegram took no line for it: the read owes one
    typed: bool = (
        False  # words typed to a meal (M7c): they stand even if no food is seen
    )


@dataclass(frozen=True)
class Split:
    """M2b done: the way back, and each photo's message with its read."""

    event_id: int
    jobs: tuple


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


def _join_album(
    db, came: Incoming, user: User, said: Optional[SaidTime]
) -> Optional[Born]:
    """Found by the database, not memory. An album split in meals, or one that is gone,
    has no meal to join: the photo is its own."""
    if not came.media_group_id or came.kind != "photo":
        return None
    meals = list(
        db.scalars(
            select(Meal).where(
                Meal.deleted_at.is_(None),
                Meal.id.in_(
                    select(MealSource.meal_id).where(
                        MealSource.user_id == user.id,
                        MealSource.media_group_id == came.media_group_id,
                    )
                ),
            )
        )
    )
    if len(meals) != 1:
        return None
    meal = meals[0]
    meal.photos = [*(meal.photos or []), came.file_id]
    if said and meal.time_source in ("sent", reminders.SOURCE):  # a hand's time stays
        for key, value in _time_columns(came.sent_at, None, said).items():
            setattr(meal, key, value)
    source = _source(came, user, meal.id)
    db.add(source)
    db.add(
        MealMessage(
            chat_id=came.chat_id,
            message_id=came.message_id,
            meal_id=meal.id,
            view="source",
        )
    )
    db.commit()
    return Born(meal.id, source.id, meal.rev or 0, joined=len(meal.photos))


def _time_columns(
    sent, forward, said: Optional[SaidTime], on_day=None, recall=None
) -> dict:
    """When it was eaten, section 5.3. A forward's own time outranks any words, and a
    day the person chose (O1b) outranks both: it is a hand's, and nothing moves it.
    ``recall``: (moment, precision) of a reminder's answer with no time of its own."""
    if on_day:
        return {
            "meal_time": on_day,
            "time_precision": DAY_ONLY,
            "time_source": "edited",
            "time_phrase": None,
        }
    if forward:
        return {
            "meal_time": forward,
            "time_precision": "approx",
            "time_source": "forward",
            "time_phrase": None,
        }
    if said:
        return {
            "meal_time": said.moment,
            "time_precision": said.precision,
            "time_source": "said",
            "time_phrase": said.phrase,
        }
    if recall:
        return {
            "meal_time": recall[0],
            "time_precision": recall[1],
            "time_source": reminders.SOURCE,
            "time_phrase": None,
        }
    return {
        "meal_time": sent,
        "time_precision": "exact",
        "time_source": "sent",
        "time_phrase": None,
    }


def _said(
    text: Optional[str], kind: str, sent, forward, user: User, send=None, reply_to=None
):
    """The time words of what came. A caption's words may be all there is. A reply
    to a reminder (§3.2(a)) sent on a later day: a time with no day of its own is of
    the reminder's day, as a reply with no time is."""
    if forward:
        return None
    zone = user_zone(user)
    said = time_words(text, _aware(sent), zone, bare_ok=kind != "text")
    if said is None or not said.of_day or send is None:
        return said
    if reply_to is None or reply_to != send.message_id:
        return said
    if local_time(_aware(sent), zone).date() <= send.local_day:
        return said
    if _YESTERDAY.search(text) or _TODAY.search(text):
        return said  # «вчера …», «сегодня …»: the words name their own day
    end = datetime.combine(send.local_day, _DAY_END, zone)
    return time_words(text, end, zone, bare_ok=kind != "text") or said


def _born_exact(
    db,
    came: Incoming,
    user: User,
    dishes: list,
    note: Optional[str],
    said=None,
    on_day=None,
    recall=None,
) -> Born:
    columns = _time_columns(came.sent_at, came.forward_date, said, on_day, recall)
    meal = meal_items.save_exact(
        db, user.id, dishes, meal_time=columns["meal_time"], draft=draft_on(user)
    )
    meal.sent_at = came.sent_at
    meal.time_source = columns["time_source"]
    meal.time_precision = columns["time_precision"]
    meal.time_phrase = columns["time_phrase"]
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


def born(
    db,
    came: Incoming,
    *,
    note: Optional[str] = None,
    words: Optional[str] = None,
    day: Optional[date] = None,
) -> Optional[Born]:
    """Source and meal in one commit, before any download or model call. None: this
    message is already here, the unique key said so. ``words``: the food of a message
    whose first words were for a meal that is gone. The source keeps what came.
    ``day``: the day it was asked for (O1b); time words in it are still no dish."""
    user = _user(db, came)
    told = words if words is not None else came.text
    send = None if day else reminders.bound_send(db, user, came)
    said = _said(
        told, came.kind, came.sent_at, came.forward_date, user, send, came.reply_to
    )
    recall = reminders.recall_time(send, came, user_zone(user)) if send else None
    told = said.rest if said and (said.rest or not day) else told
    on_day = meal_card.noon_of(day, user_zone(user)) if day else None
    try:
        joined = _join_album(db, came, user, said)
        if joined is not None:
            return joined
        if came.kind == "text":
            dishes = meal_items.exact_match(db, user.id, told)
            if dishes:
                exact = _born_exact(db, came, user, dishes, note, said, on_day, recall)
                if send is not None:
                    reminders.answered(db, user, send, came)
                return exact
        reading_text = came.kind == "text"
        meal = crud_meal.build(
            MealCreate(
                description=told if reading_text else None,
                sent_at=came.sent_at,
                photos=[] if reading_text or came.kind == "voice" else [came.file_id],
                numbers_basis="none",
                **_time_columns(came.sent_at, came.forward_date, said, on_day, recall),
            ),
            user.id,
        )
        meal.reading_status = "pending"
        meal.is_draft = draft_on(user)
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
    if send is not None:
        prior = reminders.answered(db, user, send, came)
        if came.kind == "voice" and meal.time_source == reminders.SOURCE:
            # Its words come later and may be of food eaten now (spoken_words).
            meal.resolution_signals = {RECALL_KEY: prior}
            db.commit()

    kind = _kind_of_read(came.kind)
    hint = False
    if kind == TEXT:
        hint = _take_hint(db, user)
        words = _record_text(db, meal, user, hint=hint, note=note, reading=True)
        markup = _first_markup(db, meal, meal_card.join_row(db, meal))
        first = Line(words, markup, meal_id=meal.id)
    else:
        first = Line(meal_card.pending_text(kind, meal), meal_id=meal.id)
    job = Read(
        meal_id=meal.id,
        rev=meal.rev or 0,
        source_id=source.id,
        telegram_id=came.telegram_id,
        chat_id=came.chat_id,
        kind=kind,
        file_id=came.file_id,
        text=told or None,
        shown=first.text,
        hint=hint,
        note=note,
    )
    return Born(meal.id, source.id, meal.rev or 0, first=first, job=job)


# --- an album, M2 and M2a --------------------------------------------------------------


def _standing_line(db, meal: Meal) -> Line:
    user = db.get(User, meal.user_id)
    text = _record_text(db, meal, user)
    return Line(text, meal_card.line_markup(db, meal), meal_id=meal.id)


def album_line(db, meal_id: int) -> Optional[Retouch]:
    """What the album's line says now: the count while it is read, else its record."""
    line, meal = _line_of(db, meal_id), db.get(Meal, meal_id)
    if line is None or meal is None:
        return None
    if line.view == "pending":
        text = meal_card.photos_pending(len(meal.photos or []), meal.is_draft)
        return Retouch(line.message_id, text, None)
    standing = _standing_line(db, meal)
    return Retouch(line.message_id, standing.text, standing.markup)


def split_pending(db, meal_id: int) -> str:
    """The first line of one photo out of an album."""
    return meal_card.pending_text(IMAGE, db.get(Meal, meal_id))


def album_wait(db, meal_id: int) -> float:
    """Seconds until the read may start: from the last photo of an album, by the rows.
    The wall clock on purpose: the same one that stamped ``created_at``."""
    last = db.scalar(
        select(MealSource.created_at)
        .where(
            MealSource.meal_id == meal_id,
            MealSource.media_group_id.is_not(None),
            MealSource.supersedes_id.is_(None),
        )
        .order_by(MealSource.id.desc())
        .limit(1)
    )
    if last is None:
        return 0
    passed = (datetime.now(UTC) - _aware(last)).total_seconds()
    return max(ALBUM_WAIT_SECONDS - passed, 0)


def read_as_of_now(db, job: Read) -> Read:
    """Right before the call: the album as it stands. Up to ten photos, every caption."""
    if job.kind != IMAGE:
        return job
    sources = meal_items.album_sources(db, job.meal_id)
    if len(sources) < 2:
        return job
    user = db.get(User, db.get(Meal, job.meal_id).user_id)
    told = []
    for source in map(lambda kept: latest_version(db, kept), sources):
        said = _said(
            source.text, source.kind, source.sent_at, source.forward_date, user
        )
        words = said.rest if said else source.text
        if words:
            told.append(words)
    shown = sources[:ALBUM_MAX]
    return replace(
        job,
        file_ids=tuple(latest_version(db, source).file_id for source in shown),
        source_ids=tuple(source.id for source in shown),
        text="\n".join(told) or None,
    )


def _touched(db, meal_id: int) -> bool:
    """Whether the person changed what is in this meal. A time is no matter of a read,
    and a split that was taken back left nothing."""
    return (
        db.scalar(
            select(RecordEvent.id)
            .where(
                RecordEvent.meal_id == meal_id,
                RecordEvent.kind.not_in(("time", "reread", "split")),
            )
            .limit(1)
        )
        is not None
    )


def album_again(db, meal_id: int) -> Optional[Read]:
    """A straggler's read of the whole album. None: past the limit, or the person has
    been here, and their record stands. The photo is kept either way."""
    meal = db.get(Meal, meal_id)
    if meal is None or meal.deleted_at is not None:
        return None
    if len(meal_items.album_sources(db, meal_id)) > ALBUM_MAX:
        return None
    if _touched(db, meal_id):
        return None
    had_items = bool(crud_meal_item.live(db, meal_id))
    job = read_again(db, meal_id)
    return replace(job, replaces=had_items) if job else None


def album_after(db, job: Read) -> Optional[Read]:
    """The read that has just ended missed a photo that came during it."""
    if job.kind != IMAGE or job.alone:
        return None
    shown = meal_items.album_sources(db, job.meal_id)[:ALBUM_MAX]
    read = job.source_ids or (job.source_id,)
    if all(source.id in read for source in shown):
        return None
    return album_again(db, job.meal_id)


def split_settled(db, event_id: int, message_id: int) -> Optional[Retouch]:
    """The reads of a split ended: the tapped message stops saying «читаю», and keeps
    the way back while there is one. Nothing if the way back was taken: that screen
    stands."""
    event = db.get(RecordEvent, event_id)
    meal = db.get(Meal, event.meal_id) if event else None
    undone = db.scalar(
        select(RecordEvent.id).where(RecordEvent.undoes_event_id == event_id).limit(1)
    )
    if meal is None or meal.deleted_at is not None or undone is not None:
        return None
    markup = None
    if meal_items.split_holds(db, event):
        markup = _undo_markup(meal.id, event.id)
    return Retouch(message_id, _standing_line(db, meal).text, markup)


def _v_split_ask(t, data: str, mid: str, back: str) -> meal_card.Screen:
    """M2b. A question, so it reads nothing."""
    meal = meal_card._own_meal(t, int(mid), data)
    if meal_items.parts(t.db, meal):
        return _sources_screen(t, meal, back)
    count = len(meal_items.album_sources(t.db, meal.id))
    if not count:
        return meal_card._unknown(t, data)
    if count == 1:
        return meal_card._card(t, meal, back)
    markup = InlineKeyboardMarkup(
        [
            [_button(SPLIT_ALBUM, f"ml:za:{meal.id}")],
            meal_card._to_card(meal.id, back),
        ]
    )
    return meal_card.Screen(
        M2B_SPLIT.format(count=count), markup, meal_id=meal.id, view="card"
    )


def _v_split_do(t, data: str, mid: str) -> meal_card.Screen:
    """➖ Разделить. A second tap pays for nothing, and a read already out is not split
    from under itself. With a meal joined to it, the sources screen says what can come
    apart, and splits nothing."""
    meal = meal_card._own_meal(t, int(mid), data)
    if meal_items.parts(t.db, meal):
        return _sources_screen(t, meal, "r")
    sources = meal_items.album_sources(t.db, meal.id)
    if not sources:
        return meal_card._unknown(t, data)
    if len(sources) == 1:
        return meal_card.Screen("", None, toast=meal_items.SAME_TOAST)
    if meal.reading_status == "pending":
        return meal_card.Screen("", None, toast=AGAIN_TOAST)
    change = meal_items.split_album(t.db, t.user.id, meal.id)
    if change.status != meal_items.DONE:
        return meal_card._stale_meal(t)
    jobs = []
    for source in sources:
        job = _job_of(t.db, t.db.get(Meal, source.meal_id))
        jobs.append((source.message_id, replace(job, alone=True)))
    return meal_card.Screen(
        meal_card.photos_pending(len(sources), meal.is_draft),
        InlineKeyboardMarkup(
            [ok_row(), [meal_card._undo_button(change.event.id, "r")]]
        ),
        meal_id=meal.id,
        view="other",  # not this meal's line: that one is sent now
        split=Split(change.event.id, tuple(jobs)),
    )


meal_card.add_verbs(
    (re.compile(rf"ml:zq:{_SID}:{meal_card._BACK}"), _v_split_ask),
    (re.compile(rf"ml:za:{_SID}"), _v_split_do),
)


# --- M8, M8a, M8b: join, separate -----------------------------------------------------


def _not_yet_toast() -> meal_card.Screen:
    return meal_card.Screen("", None, toast=AGAIN_TOAST)


def _elsewhere(line: Optional[Retouch], message_id: Optional[int]):
    """A line other than the message the screen lands on."""
    return None if line is None or line.message_id == message_id else line


def _v_join(t, data: str, one: str, other: str, back: str) -> meal_card.Screen:
    """🔗 Объединить. Nothing is read; the later meal keeps its row and its time."""
    a = meal_card._own_row(t, int(one), data)
    b = meal_card._own_row(t, int(other), data)
    home_a, home_b = meal_card.survivor_of(t.db, a), meal_card.survivor_of(t.db, b)
    if home_a is None and home_b is None:
        return meal_card._stale_meal(t)
    if home_a is not None and home_b is not None and home_a.id == home_b.id:
        return meal_card.Screen("", None, toast=M8_TOGETHER_TOAST)
    if home_a is not a or home_b is not b:
        live = a if home_a is a else b if home_b is b else (home_a or home_b)
        return meal_card._card(t, live, back, notice=TARGET_GONE)
    if a.reading_status == "pending" or b.reading_status == "pending":
        return _not_yet_toast()
    earlier, later = meal_card.in_order(a, b, t.zone)
    if not meal_card.has_source(t.db, later):
        return meal_card._card(t, a, back)
    known, rev = _record_text(t.db, earlier, t.user), earlier.rev
    own = _line_of(t.db, earlier.id)
    flipped = own is not None and own.message_id == t.message_id
    absorbed = _line_of(t.db, later.id) if flipped else None
    change = meal_items.join(t.db, t.user.id, earlier.id, later.id)
    if change.status != meal_items.DONE:
        return meal_card._stale_meal(t)
    part = next(p for p in meal_items.parts(t.db, earlier) if p.meal.id == later.id)
    text = M8_JOINED.format(
        when=meal_card._when(earlier, t.zone), what=meal_card._what(t.db, earlier)
    )
    split = _split_markup(part.sources[0].id, earlier.id)
    if flipped:
        return _joined_onto_own_line(t, earlier, own, absorbed, text, split)
    return meal_card.Screen(
        text,
        split,
        meal_id=earlier.id,
        view="other",
        retouch=_elsewhere(
            _retouch_of(t.db, t.user, earlier, known, rev), t.message_id
        ),
    )


def _joined_onto_own_line(t, survivor, own, absorbed, text, split) -> meal_card.Screen:
    """The tap was under the survivor's own line (a day_only meal sent first joins the
    timed one sent after it). That line stays the survivor's, with both lists; the
    absorbed meal's line says it was joined and holds ➖ Отделить, as it would had the
    chip been under it. With no such line left, ➖ is on the card."""
    view = own.view
    retouch = None
    if absorbed is not None:
        map_message(t.db, absorbed.chat_id, absorbed.message_id, survivor.id, "other")
        retouch = Retouch(absorbed.message_id, text, split)
    return meal_card.Screen(
        _record_text(t.db, survivor, t.user),
        meal_card.line_markup(t.db, survivor),
        meal_id=survivor.id,
        view=view,
        retouch=retouch,
    )


def _v_join_list(t, data: str, mid: str, back: str) -> meal_card.Screen:
    """M8b, in place of a ➖ that has nothing to take apart yet."""
    meal = meal_card._own_meal(t, int(mid), data)
    around = meal_card.join_candidates(t.db, meal)
    if not around:
        return meal_card._card(t, meal, back)
    lines, buttons = [M8B_ASK], []
    for other in around:
        what = meal_card._what(t.db, other)
        lines.append(f"{meal_card._when(other, t.zone)} · {what}")
        clock = meal_card._clock(other, t.zone)
        label = meal_card._cut(
            f"{clock} · {meal_card._names(meal_card._meal_names(t.db, other))}", 20
        )
        buttons.append(_button(label, f"ml:j:{meal.id}:{other.id}:{back}"))
    rows = [buttons[start : start + 2] for start in range(0, len(buttons), 2)]
    rows.append(meal_card._to_card(meal.id, back))
    return meal_card.Screen(
        "\n".join(lines), InlineKeyboardMarkup(rows), meal_id=meal.id, view="card"
    )


def _v_separate(t, data: str, sid: str, back: str) -> meal_card.Screen:
    """➖ Отделить: the meal this message was comes back, with its time and its sources,
    and the fixes made while it was inside the other."""
    source = _own_source(t, sid, data)
    survivor = t.db.get(Meal, source.meal_id) if source.meal_id else None
    if survivor is None:
        return meal_card.Screen("", None, toast=M8A_APART_TOAST)
    if survivor.deleted_at is not None:
        return meal_card._stale_meal(t)
    part = meal_items.part_of(t.db, survivor, source.id)
    if part is None:
        return meal_card.Screen("", None, toast=M8A_APART_TOAST)
    if survivor.reading_status == "pending":
        return _not_yet_toast()
    known, rev = _record_text(t.db, survivor, t.user), survivor.rev
    other_rev = part.meal.rev
    change = meal_items.separate(t.db, t.user.id, survivor, part)
    if change.status != meal_items.DONE:
        return meal_card._stale_meal(t)
    other = change.meals[0]
    items = crud_meal_item.live(t.db, other.id)
    fix = f"ml:i:{items[0].id}:{back}" if len(items) == 1 else f"ml:o:{other.id}:{back}"
    rows = [
        [meal_card.join_button(t.db, survivor, other, back)],
        [_button(FIX, fix), _button(DELETE, f"ml:d:{other.id}:{back}")],
    ]
    text = M8A_APART.format(
        what=meal_card._what(t.db, other), when=meal_card._when(other, t.zone)
    )
    # Its own line, if it still is one, stops saying what it said while joined.
    own = _retouch_of(t.db, t.user, other, "", other_rev)
    return meal_card.Screen(
        text,
        InlineKeyboardMarkup(rows),
        meal_id=other.id,
        view="other",
        retouch=_elsewhere(
            _retouch_of(t.db, t.user, survivor, known, rev), t.message_id
        ),
        retouch_too=_elsewhere(own, t.message_id),
    )


meal_card.add_verbs(
    (re.compile(rf"ml:j:{_SID}:{_SID}:{meal_card._BACK}"), _v_join),
    (re.compile(rf"ml:jl:{_SID}:{meal_card._BACK}"), _v_join_list),
    (re.compile(rf"ml:z:{_SID}:{meal_card._BACK}"), _v_separate),
)


# --- M13: what was sent ---------------------------------------------------------------


def latest_version(db, source: MealSource) -> MealSource:
    """The message as it stands now: an edit is a row of its own beside the original."""
    return (
        db.scalar(
            select(MealSource)
            .where(
                MealSource.chat_id == source.chat_id,
                MealSource.message_id == source.message_id,
                MealSource.supersedes_id.is_not(None),
            )
            .order_by(MealSource.id.desc())
            .limit(1)
        )
        or source
    )


def _originals(db, meal_id: int) -> list:
    rows = db.scalars(
        select(MealSource).where(
            MealSource.meal_id == meal_id, MealSource.supersedes_id.is_(None)
        )
    )
    return sorted(rows, key=lambda row: (_aware(row.sent_at), row.id))


def _was_read(db, source: MealSource) -> bool:
    return (
        db.scalar(
            select(MealReading.id)
            .where(
                MealReading.meal_id == source.origin_meal_id,
                MealReading.applied.is_(True),
            )
            .limit(1)
        )
        is not None
    )


def _source_line(t, number: int, source: MealSource) -> str:
    current = latest_version(t.db, source)
    at = meal_card.plain_time(local_time(_aware(source.sent_at), t.zone))
    if source.kind in PHOTO_SOURCES:
        said = " · оценка по фото" if not t.simple and _was_read(t.db, source) else ""
        return f"{number}. 📷 Фото, {at}{said}"
    if _kind_of_read(source.kind) != VOICE:
        words = meal_card._esc(meal_card._cut(current.text or "", SOURCE_WORDS_MAX))
        return f"{number}. ✏️ «{words}», {at}"
    line = f"{number}. 🎙 Голосовое, {at}"
    heard = meal_card.heard_of(t.db, source.origin_meal_id or source.meal_id)
    if heard is None or t.simple:
        return line
    quoted = meal_card._esc(meal_card._cut(heard, HEARD_SHOWN_MAX))
    return f"{line}\n<blockquote expandable>{quoted}</blockquote>"


def _split_rows(sources: list, joined: list, back: str) -> list:
    """One ➖ for each meal joined here, by the number of its first message; the first
    message of the meal itself has none."""
    numbers = {source.id: number for number, source in enumerate(sources, 1)}
    wanted = sorted(
        (numbers[part.sources[0].id], part.sources[0].id)
        for part in joined
        if part.sources[0].id in numbers
    )[:SPLIT_BUTTONS_MAX]
    buttons = [
        _button(f"{SPLIT} №{number}", f"ml:z:{sid}:{back}") for number, sid in wanted
    ]
    wide = 3 if all(number < 10 for number, _sid in wanted) else 2
    return [buttons[start : start + wide] for start in range(0, len(buttons), wide)]


def _sources_screen(t, meal: Meal, back: str) -> meal_card.Screen:
    """M13, M13h, M13a. Opening it writes nothing but the message's map."""
    to_card = meal_card._to_card(meal.id, back)
    sources = _originals(t.db, meal.id)
    if not sources:
        return meal_card.Screen(
            f"{M13_TITLE}\n{M13A_NOTHING}",
            InlineKeyboardMarkup([to_card]),
            meal_id=meal.id,
            view="card",
        )
    lines = [M13_TITLE]
    lines += [_source_line(t, n, source) for n, source in enumerate(sources, 1)]
    lines.append(M13_ABOUT)
    if t.simple:
        return meal_card.Screen(
            "\n".join(lines),
            InlineKeyboardMarkup([to_card]),
            meal_id=meal.id,
            view="card",
        )
    lines.append(M13_MORE)
    joined = meal_items.parts(t.db, meal)
    rows = _split_rows(sources, joined, back)
    if not joined and len(meal_items.album_sources(t.db, meal.id)) > 1:
        rows.append([_button(SPLIT_ALBUM, f"ml:zq:{meal.id}:{back}")])
    last = [_button(READ_ANEW, f"ml:rr:{meal.id}:{back}")]
    if any(source.kind in PHOTO_SOURCES for source in sources):
        last.insert(0, _button(SHOW_PHOTO, f"ml:sf:{meal.id}"))
    rows += [last, to_card]
    return meal_card.Screen(
        "\n".join(lines),
        InlineKeyboardMarkup(rows),
        meal_id=meal.id,
        view=READ_ANEW_VIEW,
    )


def _v_sources(t, data: str, mid: str, back: str) -> meal_card.Screen:
    return _sources_screen(t, meal_card._own_meal(t, int(mid), data), back)


def _v_show_photos(t, data: str, mid: str) -> meal_card.Screen:
    """📷 Показать фото: as they came, silently. The toast follows what Telegram says."""
    meal = meal_card._own_meal(t, int(mid), data)
    files = tuple(
        (source.kind, latest_version(t.db, source).file_id)
        for source in _originals(t.db, meal.id)
        if source.kind in PHOTO_SOURCES
    )
    if not files:
        return meal_card.Screen("", None, toast=M13_PHOTO_GONE_TOAST)
    return meal_card.Screen("", None, files=files)


meal_card.add_verbs(
    (re.compile(rf"ml:src:{_SID}:{meal_card._BACK}"), _v_sources),
    (re.compile(rf"ml:sf:{_SID}"), _v_show_photos),
)


# --- M11: an edited message -----------------------------------------------------------


def _read_anew_reads(db, meal: Meal, original: MealSource) -> bool:
    """🔁 Прочитать заново reads the meal's own first message, or its whole album. Any
    other message of the meal (one joined to it, words added) it would not read."""
    own = _own_first(db, meal)
    if own is None:
        return False
    if own.id == original.id:
        return True
    return bool(own.media_group_id) and own.media_group_id == original.media_group_id


def edited(db, came: Incoming) -> Optional[Line]:
    """A message that changed after it was sent. What came first stays as it came, the
    new words are kept beside it, and the record waits for the person's word. None: not
    a message of a meal, or nothing new; a deleted meal keeps the words and says nothing.
    Words that made no record can become one, and 🔁 is offered only where it reads
    what was edited.
    """
    original = db.scalar(
        select(MealSource)
        .where(
            MealSource.chat_id == came.chat_id,
            MealSource.message_id == came.message_id,
            MealSource.supersedes_id.is_(None),
        )
        .order_by(MealSource.id)
        .limit(1)
    )
    if original is None:
        return None
    user = _user(db, came)
    meal = _replied_meal(db, came.chat_id, came.message_id, user)
    if meal is None and original.meal_id is not None:
        return None
    latest = latest_version(db, original)
    if latest.text == came.text and latest.file_id == came.file_id:
        return None
    version = _source(came, user, original.meal_id)
    version.origin_meal_id = original.origin_meal_id
    version.supersedes_id = latest.id
    version.sent_at = original.sent_at
    if version.file_id == original.file_id:
        version.audio_path = original.audio_path
    db.add(version)
    db.commit()
    if original.meal_id is None:
        if _is_stop_word(came.text or ""):
            return None
        return Line(M11_NO_RECORD, _it_is_food(original.id))
    if meal_card.waits_for_words(db, meal):
        markup = meal_card.silent_voice_view(M11_NO_RECORD, meal.id)[1]
        return Line(M11_NO_RECORD, markup, meal_id=meal.id, view="other")
    if meal.deleted_at is not None:
        return None
    fix = _button(FIX, f"ml:o:{meal.id}:r")
    if not _read_anew_reads(db, meal, original):
        return Line(
            M11_KEPT, InlineKeyboardMarkup([[fix]]), meal_id=meal.id, view="other"
        )
    markup = InlineKeyboardMarkup([[_button(READ_ANEW, f"ml:rr:{meal.id}:r"), fix]])
    return Line(M11_EDITED, markup, meal_id=meal.id, view=READ_ANEW_VIEW)


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
            source_ids=list(job.source_ids) or [job.source_id],
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
    retouch: Optional[Retouch] = None  # the original line of the meal it joined


def _no_food(db, job: Read, meal: Meal) -> Line:
    meal.reading_status = "no_food"
    if job.kind == VOICE:
        # Words with no food: no meal in the day, and the voice stays its source.
        meal.deleted_at = _now()
        db.commit()
        return Line(*meal_card.silent_voice_view(M3A_NO_FOOD, meal.id), meal_id=meal.id)
    if job.kind != TEXT:
        db.commit()
        return _no_food_photo_line(meal)
    # M3a: nothing goes in the diary, and the words stay.
    meal.deleted_at = _now()
    source = db.get(MealSource, job.source_id)
    source.meal_id = None
    db.commit()
    return Line(M3A_NO_FOOD, _it_is_food(source.id))


def _read_stands(db, meal: Meal, job: Read) -> bool:
    """A read whose ``rev`` moved may still land: a time takes nothing from it, and what
    was joined to it stays beside it. A re-read of an album replaces what is there, so
    only a time leaves it room. Anything else the person did outranks it. From the
    events, as a fill of a joined dish moves ``rev`` too and is nobody's word."""
    if meal.deleted_at is not None or meal.reading_status != "pending":
        return False
    allowed = ("time",) if job.replaces and not job.anew else READ_SURVIVES
    kinds = db.scalars(
        select(RecordEvent.kind).where(
            RecordEvent.meal_id == meal.id, RecordEvent.id > job.last_event
        )
    ).all()
    return bool(kinds) and all(kind in allowed for kind in kinds)


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
    if (meal.rev or 0) != job.rev and _read_stands(db, meal, job):
        job = replace(job, rev=meal.rev or 0)
    if (meal.rev or 0) != job.rev or meal.deleted_at is not None:
        # What the person did outranks a late answer. Kept, not applied.
        _store_reading(db, job, status="done", output=nutrition, model=model)
        if meal.reading_status == "pending":
            meal.reading_status = "read"
            db.commit()
        # A record that stands says so; a row with nothing on it has nothing to say.
        stands = job.replaces or crud_meal_item.live(db, meal.id)
        if stands and meal.deleted_at is None and _claim_view(db, job):
            return Done(_standing_line(db, meal))
        return Done()

    names = meal_items.names_read(meal_in)
    if names and job.anew:
        return _read_anew_done(db, job, meal, meal_in, nutrition, resolution, model)
    live = crud_meal_item.live(db, meal.id)
    if not names and (job.replaces or live):
        # The last photo has no food, or nothing was seen under the words said to it:
        # what stands, stands.
        _store_reading(db, job, status="no_food", output=nutrition, model=model)
        meal.reading_status = "read"
        db.commit()
        return Done(_standing_line(db, meal) if _claim_view(db, job) else None)
    if not names and job.typed:
        line = _typed_words_stand(db, job, meal, nutrition, model)
        return Done(line if _claim_view(db, job) else None)
    if not names:
        _store_reading(
            db, job, status="no_food", output=nutrition, applied=True, model=model
        )
        line = _no_food(db, job, meal)
        return Done(line if _claim_view(db, job) else None)

    if live and not job.replaces:
        # The plate under what was joined to it: those lines stay and the read comes in.
        meal_items.reread(
            db, meal, job.rev, meal_in, source_id=job.source_id, quiet=True
        )
    else:
        meal_items.apply_read(
            db, meal, job.rev, meal_in, source_id=job.source_id, replacing=job.replaces
        )
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
    target = _reply_target(db, job, meal, user)
    if target is not None and not _stays_its_own_draft(meal, target):
        return _join_read(db, job, meal, user, target, learn)
    if not _claim_view(db, job):
        return Done(learn=learn)
    voice_hint = job.kind == VOICE and _take_voice_hint(db, user)
    hint = job.hint or (job.kind != VOICE and _take_hint(db, user))
    text = _record_text(
        db, meal, user, hint=hint, note=job.note, cost=cost, voice_hint=voice_hint
    )
    # The same words still owe the one dish its fix: the items came only with this read.
    one_dish = not meal.is_draft and len(crud_meal_item.live(db, meal.id)) == 1
    if text == job.shown and not one_dish:
        return Done(learn=learn)
    line = Line(text, meal_card.line_markup(db, meal), meal_id=meal.id)
    return Done(line, learn)


def _read_anew_done(
    db, job: Read, meal: Meal, meal_in: MealCreate, nutrition, resolution, model
) -> Done:
    """🔁 Прочитать заново came back: the person's own lines stay, the change has a way
    back, and a reply it once was does not join anything again."""
    user = db.get(User, meal.user_id)
    known, rev = _record_text(db, meal, user), meal.rev
    change = meal_items.reread(db, meal, job.rev, meal_in, source_id=job.source_id)
    _store_reading(
        db,
        job,
        status="done",
        output=nutrition,
        applied=change is not None,
        model=model,
    )
    if change is None:
        return Done(_standing_line(db, meal) if _claim_view(db, job) else None)
    learn = {
        "user_id": meal.user_id,
        "meal_id": meal.id,
        "nutrition": nutrition,
        "resolution_signals": resolution.signals if resolution else None,
        "resolution_source": resolution.source if resolution else None,
        "canonical_name": meal_items.only_dish_name(db, meal),
    }
    claimed = _claim_view(db, job)
    text = _record_text(db, meal, user)
    line = Line(text, _undo_markup(meal.id, change.event.id), meal_id=meal.id)
    retouch = _retouch_of(db, user, meal, known, rev)
    if retouch is not None and retouch.message_id == job.line_id:
        retouch = None
    return Done(line if claimed else None, learn, retouch)


def _reply_target(db, job: Read, meal: Meal, user: User) -> Optional[Meal]:
    """The meal a photo or a voice note answered. From the kept source, so a restart
    or a retry does not lose it. A deleted one had its M5a already."""
    source = db.get(MealSource, job.source_id)
    if source.kind == "text" or source.reply_to_message_id is None:
        return None
    if reminders.replied_send(
        db, user, source.chat_id, source.reply_to_message_id, source.sent_at
    ):
        return None  # §3.2(a): an answer to a reminder, never the ⭐ it holds
    target = _replied_meal(db, source.chat_id, source.reply_to_message_id, user)
    if target is None or target.deleted_at is not None or target.id == meal.id:
        return None
    return target


def _join_read(
    db, job: Read, meal: Meal, user: User, target: Meal, learn: dict
) -> Done:
    """M5 by photo or voice: born, read, and only then joined, so a read that fails or
    sees no food stays its own meal."""
    claimed = _claim_view(db, job)
    names = [item.name for item in crud_meal_item.live(db, meal.id)]
    known, rev = _record_text(db, target, user), target.rev
    joined = meal_items.join(db, user.id, target.id, meal.id, source_id=job.source_id)
    if joined.status != meal_items.DONE:
        text = _record_text(db, meal, user)
        line = Line(text, meal_card.line_markup(db, meal), meal_id=meal.id)
        return Done(line if claimed else None, learn)
    line = _added_line(user, target, job.source_id, names)
    retouch = _retouch_of(db, user, target, known, rev)
    return Done(line if claimed else None, learn, retouch)


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
                voice.NotHeard,
            ),
        ):
            return RETRY
    return FAILED


def read_no_speech(db, job: Read) -> Done:
    """M4e: silence. Out of the day until words come, the voice stays its source; what
    the person did meanwhile outranks it, as it does a late answer."""
    meal = db.get(Meal, job.meal_id)
    _store_reading(db, job, status=NO_SPEECH)
    if meal.deleted_at is not None:
        return Done()
    if meal.reading_status != "pending" or crud_meal_item.live(db, meal.id):
        if meal.reading_status == "pending":
            meal.reading_status = "read"
            db.commit()
        if _claim_view(db, job):
            return Done(_standing_line(db, meal))
        return Done()
    meal.reading_status = NO_SPEECH
    meal.deleted_at = _now()
    db.commit()
    line = Line(*meal_card.silent_voice_view(M4E_NO_SPEECH, meal.id), meal_id=meal.id)
    return Done(line if _claim_view(db, job) else None)


def audio_of(db, source_id: int) -> Optional[str]:
    return db.scalar(select(MealSource.audio_path).where(MealSource.id == source_id))


def audio_kept(db, source_id: int, name: str) -> None:
    db.get(MealSource, source_id).audio_path = name
    db.commit()


def store_heard(db, job: Read, heard: voice.Heard, took_s: float) -> Optional[int]:
    """The words, so that no later read listens or pays for them again. Returns how long
    the note runs, for the log line of R35's measure."""
    db.add(
        MealReading(
            meal_id=job.meal_id,
            source_ids=list(job.source_ids) or [job.source_id],
            kind=_READING_KIND[VOICE],
            backend=heard.backend,
            model=heard.model,
            output={"transcript": heard.text, "took_s": took_s},
            status=meal_card.HEARD,
            created_at=_now(),
        )
    )
    db.commit()
    return db.scalar(
        select(MealSource.duration_s).where(MealSource.id == job.source_id)
    )


def _bound_send(db, user: User, prior) -> Optional[ReminderSend]:
    """The reminder a voice was bound to at birth (``born``), from what it kept."""
    send_id = prior.get("send_id") if isinstance(prior, dict) else None
    send = db.get(ReminderSend, send_id) if isinstance(send_id, int) else None
    return send if send is not None and send.user_id == user.id else None


def _said_nothing(db, job: Read, meal: Meal, prior, transcript: str) -> bool:
    """R16: a voice bound to a reminder that says only «ничего», with no caption and
    nothing written to its meal meanwhile."""
    if prior is None or job.text or not reminders.is_nothing(transcript):
        return False
    if meal.deleted_at is not None or meal.reading_status != "pending":
        return False
    return not crud_meal_item.live(db, meal.id)


def read_said_nothing(db, job: Read) -> Done:
    """R16 for a voice: no read and no meal in the day, the voice stays its source, as
    words with no food do. Its reminder is answered «ничего» and closed, and its
    message says «✓ Принято.» (``reminders.accept_nothing``), as the voice's own line
    does, with no buttons."""
    meal = db.get(Meal, job.meal_id)
    source = db.get(MealSource, job.source_id)
    user = db.get(User, meal.user_id)
    signals = (
        meal.resolution_signals if isinstance(meal.resolution_signals, dict) else {}
    )
    send = _bound_send(db, user, signals.get(RECALL_KEY))
    line = _no_food(db, job, meal)
    retouch = None
    if send is not None:
        came = Incoming(
            telegram_id=job.telegram_id,
            username=None,
            chat_id=source.chat_id,
            message_id=source.message_id,
            kind=source.kind,
            text=None,
            file_id=source.file_id,
            file_unique_id=source.file_unique_id,
            file_size=None,
            duration_s=source.duration_s,
            media_group_id=None,
            reply_to=source.reply_to_message_id,
            sent_at=_aware(source.sent_at),
            forward_date=None,
        )
        retouch = reminders.accept_nothing(db, user, send, came)
        # The person said «ничего» to the reminder: no «no food seen» nor ✏️ offer.
        line = Line(reminders.ACCEPTED, meal_id=meal.id)
    return Done(line if _claim_view(db, job) else None, retouch=retouch)


def _heard_came(job: Read, source: MealSource, words: str) -> Incoming:
    """The voice as if its words were typed in its place: same message, same reply, same
    moment. A text of its own beside the voice, which stays the voice's source."""
    return Incoming(
        telegram_id=job.telegram_id,
        username=None,
        chat_id=source.chat_id,
        message_id=source.message_id,
        kind=TEXT,
        text=words,
        file_id=None,
        file_unique_id=None,
        file_size=None,
        duration_s=None,
        media_group_id=None,
        reply_to=source.reply_to_message_id,
        sent_at=_aware(source.sent_at),
        forward_date=_aware(source.forward_date) if source.forward_date else None,
    )


def _routed_line(db, job: Read, meal: Meal, route: Route, words: str) -> Line:
    if route.action == WEIGHT:
        screen = weight_screen.typed(db, job.telegram_id, words)
        return Line(screen.text, screen.markup)
    if route.action == TICK:
        return Line(f"✓ {route.tick}", stats_screen.day_card_markup())
    if route.action == SILENT and route.retouch is not None:
        # R16: «ничего» to a reminder, as a typed one and a voice bound to it say.
        return Line(reminders.ACCEPTED, retouch=route.retouch)
    if route.action == SILENT:
        return _no_food(db, job, meal)  # «спасибо»: what a read would have found
    return route.line


def route_heard(db, job: Read, transcript: str) -> Optional[Routed]:
    """A voice heard for the first time goes where its words would go if typed: a
    weight, a tick, «ещё хлеб» to the meal before it, «убери сыр» in reply to a meal.
    None: it is food, and its read goes on as ever. Otherwise the voice's own meal
    leaves the day, as a voice with no food does, and the line says what was done."""
    meal = db.get(Meal, job.meal_id)
    source = db.get(MealSource, job.source_id)
    if source is None or source.kind != VOICE or meal is None:
        return None
    if meal.deleted_at is not None or meal.reading_status != "pending":
        return None
    if crud_meal_item.live(db, meal.id):
        return None  # the person wrote to it meanwhile: it is a meal
    signals = (
        meal.resolution_signals if isinstance(meal.resolution_signals, dict) else {}
    )
    prior = signals.get(RECALL_KEY) if meal.time_source == reminders.SOURCE else None
    if _said_nothing(db, job, meal, prior, transcript):
        return None  # R16 has its own way, in ``spoken_words``
    words = " ".join(part for part in (job.text, transcript) if part).strip()
    if not words:
        return None
    route = route_text(db, _heard_came(job, source, words), heard=meal.id)
    if route.action == NEW_MEAL:
        return None
    line = _routed_line(db, job, meal, route, words)
    if meal.deleted_at is None:
        meal.reading_status = "no_food"
        meal.deleted_at = _now()
        db.commit()
    voice_row = db.scalar(
        select(MealMessage).where(
            MealMessage.chat_id == source.chat_id,
            MealMessage.message_id == source.message_id,
        )
    )
    if voice_row is not None and voice_row.meal_id == meal.id:
        left_meal(db, source.chat_id, source.message_id)  # a reply to it: a new meal
    if not _claim_view(db, job):
        return Routed(None)
    if job.line_id is not None and line is not None and line.meal_id is not None:
        map_message(db, job.chat_id, job.line_id, line.meal_id, line.view)
    else:
        left_meal(db, job.chat_id, job.line_id)
    return Routed(line)


def spoken_words(db, job: Read, transcript: str) -> str:
    """What the model reads of a note. A time said in it is the meal's, as in typed
    words, unless the person has set one meanwhile. A caption says its part too. The
    noon of ``day_only`` claims no clock, so only a real time can be ahead of now."""
    meal = db.get(Meal, job.meal_id)
    source = db.get(MealSource, job.source_id)
    user = db.get(User, meal.user_id)
    bound = meal.time_source == reminders.SOURCE
    signals = (
        meal.resolution_signals if isinstance(meal.resolution_signals, dict) else {}
    )
    prior = signals.get(RECALL_KEY) if bound else None
    if _said_nothing(db, job, meal, prior, transcript):
        raise SaidNothing()
    send = None
    if bound and reminders.says_now(transcript):
        # §3.2: food in front of the person now is never a reminder's: the sent time,
        # and the reminder is as unanswered as it was before this note.
        for column, value in _time_columns(source.sent_at, None, None).items():
            setattr(meal, column, value)
        rest = {key: value for key, value in signals.items() if key != RECALL_KEY}
        meal.resolution_signals = rest or None
        db.commit()
        reminders.take_back(db, user, prior, source.sent_at)
    elif isinstance(prior, dict) and isinstance(prior.get("send_id"), int):
        send = db.get(ReminderSend, prior["send_id"])
    reply_to = source.reply_to_message_id
    said = _said(
        transcript, TEXT, source.sent_at, source.forward_date, user, send, reply_to
    )
    ahead = said and said.precision != DAY_ONLY and said.moment > _now()
    if said and meal.time_source in ("sent", reminders.SOURCE) and not ahead:
        for column, value in _time_columns(source.sent_at, None, said).items():
            setattr(meal, column, value)
        db.commit()
    words = (said.rest if said else transcript) or ""
    return " ".join(part for part in (job.text, words) if part)


def read_failed(
    db,
    job: Read,
    error: str,
    *,
    status: str = FAILED,
    model: Optional[str] = None,
    output: Optional[dict] = None,
) -> Done:
    """``error`` is the exception's class: a body or a transcript never goes there.
    A meal no longer waiting was answered meanwhile, by the words typed to it: a late
    failure is kept and says nothing. ``output``: an answer paid for that could not be
    written, kept so that it need not be paid for again."""
    meal = db.get(Meal, job.meal_id)
    waited = meal.reading_status == "pending"
    live = crud_meal_item.live(db, meal.id) if waited else []
    if live and (job.replaces or _own_items(meal, live)):
        # A read over a record that stands, a straggler's or the person's own: the
        # record stays. From the rows, so a sweep after a restart finds it too. Words
        # joined to a photo nobody read are no such record: the photo says it failed.
        meal.reading_status = "read"
        db.commit()
        _store_reading(db, job, status=status, error=error, model=model, output=output)
        return Done(_standing_line(db, meal) if _claim_view(db, job) else None)
    if waited:
        meal.reading_status = "failed"
        db.commit()
    _store_reading(db, job, status=status, error=error, model=model, output=output)
    if not waited:
        return Done()
    line = _failed_line(job.kind, meal)
    if job.again and error == DEAD_FILE:
        zone = user_zone(db.get(User, meal.user_id))
        text, markup = meal_card.dead_file_view(meal, zone)
        line = Line(text, markup, meal_id=meal.id)
    if not _claim_view(db, job) or line.text == job.shown:
        # A retry that failed again: the line says it already.
        return Done()
    return Done(line)


# --- a line that did not reach the person ---------------------------------------------


def line_not_shown(db, job: Read, line: Line, *, owed: bool) -> None:
    """Telegram did not take this line. What it said once in a life is said later, and a
    line that could still take it goes on the sweep's list. ``owed`` False: it never
    will (the message is gone).
    """
    user = db.scalar(select(User).where(User.telegram_id == job.telegram_id))
    if user is not None:
        settings = dict(user.settings or {})
        if M1B_HINT in line.text:
            settings.pop("capture_hint_seen", None)
        if M4C_VOICE_HINT in line.text or M4C_DRAFT_HINT in line.text:
            settings.pop("voice_hint_seen", None)
        user.settings = settings
    if owed and job.line_id is not None:
        row = db.scalar(
            select(MealMessage).where(
                MealMessage.chat_id == job.chat_id,
                MealMessage.message_id == job.line_id,
            )
        )
        if row is not None and row.view == "line":
            row.view = OWED_VIEW
    db.commit()


@dataclass
class Owed:
    chat_id: int
    message_id: int
    line: Line


def _owed_line(db, meal: Meal) -> Optional[Line]:
    """What the line of a meal whose read is over says, from the rows alone."""
    kind = meal_card._read_kind(db, meal)
    if meal.reading_status == "no_speech":
        return Line(
            *meal_card.silent_voice_view(M4E_NO_SPEECH, meal.id), meal_id=meal.id
        )
    if meal.reading_status == "no_food":
        if kind == VOICE:
            return Line(
                *meal_card.silent_voice_view(M3A_NO_FOOD, meal.id), meal_id=meal.id
            )
        if kind == TEXT:
            source = db.scalar(
                select(MealSource)
                .where(MealSource.origin_meal_id == meal.id)
                .order_by(MealSource.id)
                .limit(1)
            )
            return Line(M3A_NO_FOOD, _it_is_food(source.id)) if source else None
    if meal.deleted_at is not None:
        return None  # the person deleted it: nothing to say on that line
    if meal.reading_status == "no_food":
        return _no_food_photo_line(meal)
    if meal.reading_status == "failed":
        return _failed_line(kind, meal)
    return _standing_line(db, meal)


def owed_lines(db, live) -> list:
    """The lines still on «читаю» though their read is over: Telegram was down for the
    last word. Nothing is read or paid for here. No task holds them."""
    rows = db.execute(
        select(MealMessage, Meal)
        .join(Meal, Meal.id == MealMessage.meal_id)
        .where(MealMessage.view == OWED_VIEW, Meal.reading_status != "pending")
        .order_by(MealMessage.id)
        .limit(OWED_PER_SWEEP)
    ).all()
    owed = []
    for row, meal in rows:
        if meal.id in live:
            continue
        line = _owed_line(db, meal)
        if line is None:
            row.view = "line"
            db.commit()
        else:
            owed.append(Owed(row.chat_id, row.message_id, line))
    return owed


def owed_settled(db, chat_id: int, message_id: int) -> None:
    row = db.scalar(
        select(MealMessage).where(
            MealMessage.chat_id == chat_id, MealMessage.message_id == message_id
        )
    )
    if row is not None and row.view == OWED_VIEW:
        row.view = "line"
        db.commit()


def message_of(db, source_id: int) -> Optional[int]:
    return db.scalar(select(MealSource.message_id).where(MealSource.id == source_id))


# --- reading again --------------------------------------------------------------------


def _line_of(db, meal_id: int) -> Optional[MealMessage]:
    """The line the capture sent, if it still is a line. A later reply's line belongs
    to the meal too, and a screen opened on it is the person's."""
    line = db.scalar(
        select(MealMessage)
        .where(
            MealMessage.meal_id == meal_id,
            MealMessage.view.not_in(("source", "other")),
        )
        .order_by(MealMessage.id)
        .limit(1)
    )
    return line if line is not None and line.view in LINE_VIEWS else None


def _has_line(db, meal_id: int) -> bool:
    """Whether a line of the meal ever went out, whatever it has become since."""
    stmt = select(MealMessage.id).where(
        MealMessage.meal_id == meal_id,
        MealMessage.view.not_in(("source", "other")),
    )
    return db.scalar(stmt.limit(1)) is not None


def _failed_words(db, meal: Meal) -> Optional[MealSource]:
    """M7c: the words whose read failed last. The retry reads them, not the photo."""
    reading = db.scalar(
        select(MealReading)
        .where(MealReading.meal_id == meal.id)
        .order_by(MealReading.id.desc())
        .limit(1)
    )
    if reading is None or reading.status == "done" or not reading.source_ids:
        return None
    if reading.kind != _READING_KIND[TEXT]:
        return None
    return db.get(MealSource, reading.source_ids[0])


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
    words = _failed_words(db, meal) if source.kind != "text" else None
    typed = words is not None
    source = words or source
    kind = _kind_of_read(source.kind)
    # A line still owed its failure has not said it: only what was shown may be spared.
    said_failed = meal.reading_status == "failed" and not (
        line is not None and line.view in ("pending", OWED_VIEW)
    )
    current = latest_version(db, source)
    said = _said(current.text, source.kind, source.sent_at, source.forward_date, user)
    told = said.rest if said else current.text
    if source.kind == "text" and meal.description and current.id == source.id:
        told = meal.description  # without what was said to a meal that is gone
    return Read(
        meal_id=meal.id,
        rev=meal.rev or 0,
        source_id=source.id,
        telegram_id=user.telegram_id,
        chat_id=source.chat_id,
        kind=kind,
        file_id=current.file_id,
        text=told or None,
        line_id=line.message_id if line else None,
        shown=_failed_line(kind, meal).text if said_failed else None,
        inbound_id=_inbound_of(db, user.telegram_id, source),
        automatic=automatic,
        last_event=_last_event(db, meal),
        unsent=not _has_line(db, meal.id),
        typed=typed,
        note=M7_NOTE if typed and meal.photos else None,
    )


def _own_first(db, meal: Meal) -> Optional[MealSource]:
    sources = _originals(db, meal.id)
    own = next((s for s in sources if s.origin_meal_id == meal.id), None)
    return own or (sources[0] if sources else None)


def _anew_job(db, meal: Meal, line_id: Optional[int]) -> Optional[Read]:
    """The read of 🔁 Прочитать заново: this meal's own first message, as it stands now.
    Not ``meal.description``: that is what the record says, not what was sent."""
    own = _own_first(db, meal)
    if own is None:
        return None
    user = db.get(User, meal.user_id)
    current = latest_version(db, own)
    said = _said(current.text, own.kind, own.sent_at, own.forward_date, user)
    told = said.rest if said else current.text
    return Read(
        meal_id=meal.id,
        rev=meal.rev or 0,
        source_id=own.id,
        telegram_id=user.telegram_id,
        chat_id=own.chat_id,
        kind=TEXT if own.kind == "exact_name" else _kind_of_read(own.kind),
        file_id=current.file_id,
        text=told or None,
        line_id=line_id,
        again=True,
        replaces=True,
        anew=True,
        last_event=_last_event(db, meal),
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


REPLAY, WAITS, SETTLED = "replay", "waits", "settled"


def row_fate(db, row: InboundMessage) -> tuple:
    """What /reprocess does with a diagnostic row: (fate, what to write on it).

    A row has a model's time only if nothing else answers for it. Nothing to read is
    no read, a meal that stands was paid for, and a meal still unread is read through
    the live path, in its own turn. Only an old row with no meal is replayed.
    """
    if row.kind != "image" and (row.kind == "voice" or not (row.content or "").strip()):
        return SETTLED, {"skipped": "nothing to read"}
    user = db.scalar(select(User).where(User.telegram_id == row.telegram_id))
    if user is None:
        return REPLAY, None
    stmt = select(MealSource).where(MealSource.user_id == user.id)
    if row.photo_file_id:
        stmt = stmt.where(MealSource.file_id == row.photo_file_id)
    else:
        stmt = stmt.where(MealSource.text == row.content)
    source = db.scalar(stmt.order_by(MealSource.id.desc()).limit(1))
    if source is None:
        return REPLAY, None
    meal = db.get(Meal, source.meal_id) if source.meal_id else None
    unread = meal is not None and meal.reading_status in ("pending", "failed")
    if unread and meal.deleted_at is None:
        return WAITS, None
    return SETTLED, {"meal": source.meal_id}


def take_rollout(db, telegram_id: int) -> Optional[str]:
    """M12, or None. Asked once in a person's life with the bot, on the first capture,
    and marked before it is sent so two fast messages cannot both get it. Only for who
    has a meal from before the capture: such a meal has no source of its own, a
    deleted one counts, and a newcomer has nothing to be told. With drafts on the
    sentence would be false, so it waits for the first capture with them off."""
    user = db.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None or (user.settings or {}).get(ROLLOUT_SEEN):
        return None
    earlier = db.scalar(
        select(Meal.id)
        .outerjoin(MealSource, MealSource.origin_meal_id == Meal.id)
        .where(Meal.user_id == user.id, MealSource.id.is_(None))
        .limit(1)
    )
    if earlier is not None and draft_on(user):
        return None
    user.settings = {**(user.settings or {}), ROLLOUT_SEEN: True}
    db.commit()
    return M12_ROLLOUT if earlier is not None else None


def rollout_again(db, telegram_id: int) -> None:
    """Telegram refused it: the next capture says it instead."""
    user = db.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is not None:
        rest = dict(user.settings or {})
        rest.pop(ROLLOUT_SEEN, None)
        user.settings = rest
        db.commit()


def unread_meals(db, limit: int) -> list:
    """Ids of the meals still without a reading."""
    stmt = (
        select(Meal.id)
        .join(MealSource, MealSource.meal_id == Meal.id)
        .where(
            Meal.reading_status.in_(("pending", "failed")),
            Meal.deleted_at.is_(None),
            Meal.is_draft.is_(False),
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
                    MealReading.kind.in_(("vision", "text", "transcript")),
                    MealReading.status.in_(("done", "no_food")),
                ),
            )
        )
        .order_by(MealReading.id.desc())
        .limit(1)
    )
    return last == NO_CREDIT


def owner_may_receive(db, meal_id: int) -> bool:
    """Someone whose door was closed is not paid for, or written to, behind their back."""
    telegram_id = db.scalar(
        select(User.telegram_id)
        .join(Meal, Meal.user_id == User.id)
        .where(Meal.id == meal_id)
    )
    return telegram_id is not None and may_receive(db, telegram_id)


def _may_read_by_itself(db, meal: Meal) -> bool:
    """The cap holds, and dishes in the meal are the person's: a read would be paid for
    and could not apply. A voice is listened to by itself only locally (5.2), so with
    no local service only one already heard goes on."""
    if meal.deleted_at is not None:
        return False
    if (meal.read_attempts or 0) >= len(RETRY_AFTER):
        return False
    if not owner_may_receive(db, meal.id):
        return False
    has_voice = db.scalar(
        select(MealSource.id)
        .where(MealSource.meal_id == meal.id, MealSource.kind == "voice")
        .limit(1)
    )
    if (
        has_voice is not None
        and not settings.WHISPER_URL
        and meal_card.heard_of(db, meal.id) is None
    ):
        return False
    return not crud_meal_item.live(db, meal.id)


def pending_meals(db) -> list:
    """A draft too: a restart keeps it, and no line of its stays on «читаю»."""
    stmt = select(Meal.id).where(
        Meal.reading_status == "pending",
        Meal.deleted_at.is_(None),
    )
    return list(db.scalars(stmt.order_by(Meal.id)))


def stuck_meals(db, live) -> list:
    """Pending, older than the wait, and no task holds them. A slow read is not stuck.
    From the row's birth, not the message's: it may have come in while the bot was down.
    """
    stmt = select(Meal.id, Meal.created_at).where(
        Meal.reading_status == "pending",
        Meal.deleted_at.is_(None),
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
