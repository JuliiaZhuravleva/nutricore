"""☀️ The morning question: «is yesterday's food all in the records?» (stage 6, D10-D14).

    the question → ☀️ <b>Записи за вчера, вт 10 ноября</b>, [👍 Да] [✍️ Частично]
      [✋ Почти нет] / [✖ Не спрашивать утром]
    an answer → the same message: «✓ Ответ сохранён: …», [✅ ОК] / [✏️ Изменить ответ]
    ✏️ → the three answers again, on the same message
    ✖ → off for this person: [✅ ОК] / [☀️ Вернуть вопрос]; ☀️ → on again

It goes out at 10:00 on the person's own clock, silently, about the local day before
(a day ends at local midnight, D13) and only when that day has a record. Да, Частично
or no answer: the day counts as usual. Почти нет: the day is left out of the 7-day
averages and nothing else: the records stay, and the week's summary marks it (D11).
On for everyone who may receive messages; the evening reminder's «✖ Выкл» is another
switch (D12). The switch lives in ``User.settings``, the answers in ``morning_answers``.
Nothing here calls a model.

Every button's data carries the whole step, so a tap works after a restart; a foreign
or malformed id gets today's card and a warning, as everywhere.
"""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.crud.crud_meal import crud_meal
from app.crud.crud_morning_answer import crud_morning_answer
from app.models.morning_answer import MorningAnswer
from app.models.user import User
from app.services import meal_card, settings_screen, stats_screen
from app.services.access_control import may_receive
from app.services.close_row import ok_row
from app.services.day_card import local_time
from app.services.favorites import Screen

logger = logging.getLogger(__name__)

MORNING_ON = "morning"  # False: no morning question; on unless turned off
CALLBACK_PREFIX = "mq:"
SWITCH = "rm:morning"  # the 🔔 screen's own verb (reminders._VERBS), not an mq: one
HOUR = 10  # the local hour the question may go out in, and only that day
DELETE_WITHIN = timedelta(hours=48)  # Telegram deletes a bot's message for 48 hours
_INT_MAX = 2**31 - 1

ALL, PART, LITTLE = "all", "part", "little"
ANSWERS = {ALL: "да", PART: "частично", LITTLE: "почти нет"}
STOP = "stop"  # the row's answer when ✖ was tapped before any answer

_BUTTONS = ((ALL, "👍 Да"), (PART, "✍️ Частично"), (LITTLE, "✋ Почти нет"))
STOP_LABEL = "✖ Не спрашивать утром"
CHANGE_LABEL = "✏️ Изменить ответ"
RETURN_LABEL = "☀️ Вернуть вопрос"
SWITCH_ON = "☀️ Вопрос утром: вкл"
SWITCH_OFF = "☀️ Вопрос утром: выкл"
LINE_ON = "☀️ Утром, в 10:00 — вопрос про вчерашние записи: присылаю."
LINE_OFF = "☀️ Утром, в 10:00 — вопрос про вчерашние записи: не присылаю."

_ASK = (
    "Вчерашняя еда вся в записях?\n"
    "Можно не отвечать — тогда день считается как обычно. Да или частично — тоже.\n"
    "Почти нет — {day} не войдёт в средние за 7 дней, а в итогах будет с пометкой."
)
_SAVED = "✓ Ответ сохранён: {answer}. "
_SAVED_USUAL = "{Day} считается как обычно."
_SAVED_LITTLE = (
    "{Day} не войдёт в средние за 7 дней, а в итогах будет с пометкой. "
    "Записи за него остаются в дневнике."
)
_STOPPED_HEAD = "✓ Утром больше не спрашиваю, сколько записано за вчера. "
_EVENING_STAYS = "Вечерние напоминания остаются. "
_STOPPED_FOOT = "Включить потом — ⚙️ Настройки → 🔔 Напоминания → ☀️ Вопрос утром."
STOPPED = _STOPPED_HEAD + _EVENING_STAYS + _STOPPED_FOOT
# Someone whose evening reminder is off is not promised that it stays (review 2026-11-11).
STOPPED_NO_EVENING = _STOPPED_HEAD + _STOPPED_FOOT
RETURNED = "✓ Вопрос утром включён: завтра в 10:00, если за сегодня будут записи."
# Turned back on before 10:00: the next question goes out today, about yesterday.
RETURNED_TODAY = "✓ Вопрос утром включён: сегодня в 10:00, если за вчера есть записи."
# ✏️ on an answered question: the stored answer holds until another is tapped, so the
# «можно не отвечать» line would say the opposite of what is kept (review 2026-11-11).
_ASK_AGAIN = (
    "Вчерашняя еда вся в записях?\n"
    "Сейчас в ответе: {answer}. Он остаётся, пока не выбран другой.\n"
    "Да или частично — день считается как обычно.\n"
    "Почти нет — {day} не войдёт в средние за 7 дней, а в итогах будет с пометкой."
)

_WEEKDAYS_NOMINATIVE = (
    "понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье",
)  # fmt: skip


# --- the switch -------------------------------------------------------------------


def morning_on(user: User | None) -> bool:
    return (user.settings or {}).get(MORNING_ON) is not False if user else True


def _set_on(db, user: User, on: bool) -> None:
    # A new dict: SQLAlchemy does not see a JSON value changed in place.
    user.settings = {**(user.settings or {}), MORNING_ON: on}
    db.commit()


def settings_line(user: User | None) -> str:
    return LINE_ON if morning_on(user) else LINE_OFF


def settings_button(user: User | None) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        SWITCH_ON if morning_on(user) else SWITCH_OFF,
        callback_data=SWITCH,
    )


# --- the days ---------------------------------------------------------------------


def weekday_name(day: date) -> str:
    return _WEEKDAYS_NOMINATIVE[day.weekday()]


def little_days(db, user: User | None, first: date, last: date) -> set[date]:
    """The days in ``first``..``last`` the person answered «почти нет»: out of the
    7-day averages, nowhere else. Nothing answered, an empty set."""
    if user is None:
        return set()
    return crud_morning_answer.little_days(db, user.id, first, last)


def due(db, user: User, now: datetime) -> date | None:
    """The day to ask about now, or None. Inside the 10:00 hour of the person's own
    clock, about the local day before, when it has a record and was not asked yet."""
    if not morning_on(user) or not may_receive(db, user.telegram_id):
        return None
    zone = settings_screen.user_zone(user)
    local = local_time(now, zone)
    if local.hour != HOUR:
        return None
    day = local.date() - timedelta(days=1)
    if crud_morning_answer.for_day(db, user.id, day) is not None:
        return None
    start, end = stats_screen._day_bounds(day, zone)
    if not crud_meal.get_user_meals_between(db, user.id, start, end):
        return None
    return day


# --- the screens ------------------------------------------------------------------


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=CALLBACK_PREFIX + data)


def _title(day: date) -> str:
    return f"☀️ <b>Записи за вчера, {stats_screen._card_title(day)}</b>"


def question_view(row: MorningAnswer) -> Screen:
    ask = _ASK_AGAIN if row.answer in ANSWERS else _ASK
    answer = ANSWERS.get(row.answer, "")
    text = _title(row.day) + "\n" + ask.format(day=weekday_name(row.day), answer=answer)
    rows = [
        [_btn(label, f"a:{row.id}:{code}") for code, label in _BUTTONS],
        [_btn(STOP_LABEL, f"x:{row.id}")],
    ]
    return Screen(text, InlineKeyboardMarkup(rows), view=meal_card.NO_MEAL)


def answered_view(row: MorningAnswer) -> Screen:
    day = weekday_name(row.day)
    text = _SAVED.format(answer=ANSWERS[row.answer]) + (
        _SAVED_LITTLE if row.answer == LITTLE else _SAVED_USUAL
    ).format(Day=day.capitalize())
    rows = [ok_row(), [_btn(CHANGE_LABEL, f"c:{row.id}")]]
    return Screen(text, InlineKeyboardMarkup(rows), view=meal_card.NO_MEAL)


def _stopped_view(row: MorningAnswer, evening_on: bool = True) -> Screen:
    rows = [ok_row(), [_btn(RETURN_LABEL, f"r:{row.id}")]]
    text = STOPPED if evening_on else STOPPED_NO_EVENING
    return Screen(text, InlineKeyboardMarkup(rows), view=meal_card.NO_MEAL)


def _returned_view(before_hour: bool = False) -> Screen:
    text = RETURNED_TODAY if before_hour else RETURNED
    return Screen(text, InlineKeyboardMarkup([ok_row()]), view=meal_card.NO_MEAL)


# --- the verbs --------------------------------------------------------------------


def _unknown(t, data: str) -> Screen:
    logger.warning("morning: unknown or foreign data %r", data)
    return meal_card._today(t)


def _own_row(t, data: str, rid: str) -> MorningAnswer:
    row = t.db.get(MorningAnswer, int(rid)) if int(rid) <= _INT_MAX else None
    if row is None or t.user is None or row.user_id != t.user.id:
        raise meal_card._Answer(_unknown(t, data))
    return row


def _v_answer(t, data: str, rid: str, code: str) -> Screen:
    row = _own_row(t, data, rid)
    row.answer = code
    row.answered_at = t.now
    t.db.commit()
    return replace(answered_view(row), toast="")


def _v_change(t, data: str, rid: str) -> Screen:
    return question_view(_own_row(t, data, rid))


def _v_stop(t, data: str, rid: str) -> Screen:
    row = _own_row(t, data, rid)
    if row.answer is None:
        row.answer = STOP  # the message is the stop screen now: its buttons go, not it
        row.answered_at = t.now
    _set_on(t.db, t.user, False)
    from app.services import reminders  # reminders imports this module

    return _stopped_view(row, reminders.config(t.user).mode != reminders.OFF)


def _v_return(t, data: str, rid: str) -> Screen:
    _own_row(t, data, rid)
    _set_on(t.db, t.user, True)
    local = local_time(t.now, settings_screen.user_zone(t.user))
    return _returned_view(before_hour=local.hour < HOUR)


def toggle(db, user: User) -> bool:
    """The 🔔 screen's switch; the new state."""
    on = not morning_on(user)
    _set_on(db, user, on)
    return on


_N = r"(\d{1,12})"
_VERBS = [
    (re.compile(rf"mq:a:{_N}:({ALL}|{PART}|{LITTLE})"), _v_answer),
    (re.compile(rf"mq:c:{_N}"), _v_change),
    (re.compile(rf"mq:x:{_N}"), _v_stop),
    (re.compile(rf"mq:r:{_N}"), _v_return),
]


def handle_callback(
    db,
    telegram_id: int,
    data: str,
    now: datetime | None = None,
    message_id: int | None = None,
) -> Screen:
    """The screen a tap on a ☀️ button leads to. Every id in ``data`` is checked
    against the tapping user: callback data comes from the client."""
    user = db.query(User).filter(User.telegram_id == telegram_id).first()
    t = meal_card._Tap(db, user, telegram_id, now or datetime.now(UTC), message_id)
    for pattern, verb in _VERBS:
        match = pattern.fullmatch(data)
        if match is None:
            continue
        if user is None:
            break
        try:
            return verb(t, data, *match.groups())
        except meal_card._Answer as answer:
            return answer.screen
    return _unknown(t, data)
