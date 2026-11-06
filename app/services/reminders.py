"""🔔 Напоминания: one quiet message in the evening, answered by voice or a ⭐ (stage 6).

    the evening reminder → 🔔 <b>Еда за сегодня</b>, up to four ⭐ chips,
      [✅ ОК] [✖ Выключить], and on the very first one [🕐 Часовой пояс] while no zone
      was chosen
    a ⭐ chip → logged on the reminder's day, without a clock; ✓ line, [✅ ОК],
      [↩️ Отменить] [🕐 Время], the chips stay
    ✖ → off at once: [✅ ОК], [↩️ Вернуть] [⚙️ Настроить]
    ⚙️ → 🔔 Напоминания: off / evening, the hour, 🗓 the week's summary, the zone, sound

On for everyone (owner's decision D1), silent unless the sound is switched on (D3), on
the person's own clock. Nothing here calls a model: the text and the buttons come from
the database, and a ⭐ copies stored numbers (R1). Three reminders in a row left without
an answer stop them, and only a return after a break in the records, or a tap in ⚙️,
starts them again (R9); none of this is ever said in the chat.

What a reminder says never names an absence or the day's content (R10): no times, no
counts, the same title every day. Every button's data carries the whole step, so a tap
works after a restart; a foreign or malformed id gets today's card and a warning, as
everywhere.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from datetime import time as clock_time
from datetime import timedelta

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app.crud.crud_favorite_meal import crud_favorite_meal
from app.crud.crud_meal import crud_meal
from app.crud.crud_reminder_send import crud_reminder_send
from app.models.favorite_meal import FavoriteMeal
from app.models.meal import Meal
from app.models.meal_item import MealItem
from app.models.reminder_send import ReminderSend
from app.models.user import User
from app.services import (
    access_control,
    favorites,
    meal_card,
    meal_items,
    settings_screen,
    stats_screen,
)
from app.services.close_row import ok_row
from app.services.day_card import DAY_ONLY, clock, local_time
from app.services.favorites import Screen

logger = logging.getLogger(__name__)

KEY = "reminders"  # User.settings["reminders"]: always written as a new dict
CALLBACK_PREFIX = "rm:"
OPEN = CALLBACK_PREFIX + "o"
OFF, EVENING = "off", "evening"
MODES = (OFF, EVENING)  # «✏️ Своё время» and «🔁 По записям» come in later slices
DEFAULT_EVENING = "21:00"
EVENING_TIMES = ("20:00", "21:00", "22:00", "23:00")
SOURCE = "reminder"  # Meal.time_source of what a reminder logged

WINDOW = timedelta(minutes=60)  # a slot goes out within its hour, or not that day
FRESH = timedelta(hours=3)  # == capture.CORRECTION_WINDOW (R8, §6.4)
STOPPED_AFTER_DAYS = 7  # no record for longer: the bot does not chase (R3)
MAX_A_DAY = 3  # R4
UNANSWERED = 3  # in a row, before they stop (R9)
DELETE_WITHIN = timedelta(hours=48)  # Telegram deletes a bot's message for 48 hours
LATE_DAYS = 2  # a chip on an older reminder opens ⭐ Избранное instead
CHIPS = 4
RANK_DAYS = 28
SUNDAY = 6
_INT_MAX = 2**31 - 1

# The ladder (§5.6): what a send's ``answer`` and ``status`` mean.
ANSWERED, NEUTRAL, UNANSWERED_SEND = "answered", "neutral", "unanswered"
_ANSWERS = ("fav", "words", "none")
_NEUTRAL_ANSWERS = ("ok", "off")
STRIP = "strip"  # Screen.view: take the buttons off the message, keep its text

TITLE = "🔔 <b>Еда за сегодня</b>"
_FIRST = (
    "Можно не отвечать. Если что-то вспоминается, можно рассказать голосовым или "
    "написать. Время можно назвать: «в 13 суп». Без времени запишется на этот день, "
    "без часов."
)
_FIRST_STARS = "Или нажать на блюдо из ⭐:"
_LATER_STARS = (
    "Можно не отвечать. Если что-то вспоминается — голосовым, текстом или блюдом из ⭐:"
)
_LATER = "Можно не отвечать. Если что-то вспоминается — голосовым или текстом."
_FOOTNOTE = (
    "<i>Приходит в {slot} по времени {zone}, {sound}. Если были записи за три часа до "
    "него, не приходит. Выключить — кнопкой ✖. Текст на экране блокировки можно "
    "скрыть в настройках уведомлений Telegram.</i>"
)
_MORE = "Можно нажать ещё или рассказать голосовым."
_SETTINGS_TITLE = "<b>🔔 Напоминания</b>"
_OFF_TITLE = "🔔 <b>Напоминания</b>"
_TURNED_OFF = "✓ Напоминания выключены. Включить: ⚙️ Настройки → 🔔 Напоминания."
LATE_NOTICE = "<i>Это напоминание за прошлые дни. Вот ⭐ Избранное.</i>"
STALE_TOAST = "Этого блюда уже нет в избранном"
OK_TOAST = "Понятно"
UNDO_TOAST = "Отменено"
UNDONE_TOAST = "Эта запись уже отменена"
ZONE_LABEL = "🕐 Часовой пояс"


@dataclass(frozen=True)
class Slot:
    at: str  # "21:00", local wall clock
    kind: str  # evening, meal
    centre: str | None = None  # meal slots (R2, R3)


@dataclass(frozen=True)
class Config:
    mode: str = EVENING
    evening: str = DEFAULT_EVENING
    sound: bool = False
    since: datetime | None = None
    off_day: date | None = None
    noted: int | None = None  # the id of the first reminder ever sent
    quiet: dict | None = None  # {"level": "off", "on": "2026-11-04"}
    fragile: bool = False
    count_from: datetime | None = None
    prev: dict | None = None


# --- the config -------------------------------------------------------------------


def _raw(user: User | None) -> dict:
    value = (user.settings or {}).get(KEY) if user else None
    return dict(value) if isinstance(value, dict) else {}


def _moment(value) -> datetime | None:
    try:
        moment = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None
    if moment is not None and moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment


def _day(value) -> date | None:
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def config(user: User | None) -> Config:
    """What is stored, with anything unreadable read as the default: on, 21:00."""
    raw = _raw(user)
    mode = raw.get("mode")
    if mode not in MODES:
        if mode is not None:
            logger.warning("reminders: unknown mode %r, using the evening", mode)
        mode = EVENING
    evening = raw.get("evening")
    quiet = raw.get("quiet")
    if not (
        isinstance(quiet, dict) and quiet.get("level") == OFF and _day(quiet.get("on"))
    ):
        quiet = None
    noted = raw.get("noted")
    prev = raw.get("prev")
    return Config(
        mode=mode,
        evening=evening if evening in EVENING_TIMES else DEFAULT_EVENING,
        sound=raw.get("sound") is True,
        since=_moment(raw.get("since")),
        off_day=_day(raw.get("off_day")),
        noted=noted if isinstance(noted, int) and not isinstance(noted, bool) else None,
        quiet=quiet,
        fragile=raw.get("fragile") is True,
        count_from=_moment(raw.get("count_from")),
        prev=prev if isinstance(prev, dict) else None,
    )


def _write(db, user: User, **values) -> None:
    """One read-modify-write of the reminders' dict; None takes a key out."""
    stored = {**_raw(user), **values}
    stored = {key: value for key, value in stored.items() if value is not None}
    # A new dict: SQLAlchemy does not see a JSON value changed in place.
    user.settings = {**(user.settings or {}), KEY: stored}
    db.commit()


def phrase(cfg: Config) -> str:
    """«вечером, в 21:00», or «выключены»."""
    return "выключены" if cfg.mode == OFF else f"вечером, в {cfg.evening}"


def hub_line(user: User | None) -> str:
    """⚙️'s line: the mode only, never the pause."""
    cfg = config(user)
    return "🔔 Напоминания: " + (
        "выкл" if cfg.mode == OFF else f"вечером, {cfg.evening}"
    )


def _sound(cfg: Config) -> str:
    return "со звуком" if cfg.sound else "без звука"


# --- when (§5.2, §5.3) ------------------------------------------------------------


def slots_today(cfg: Config, learned, local_date: date) -> list[Slot]:
    """The day's slots. Evening only for now: one, at the chosen hour."""
    if cfg.mode == OFF:
        return []
    return [Slot(cfg.evening, EVENING)]


def slot_start(slot: Slot, local_day: date, zone) -> datetime:
    hours, minutes = (int(part) for part in slot.at.split(":"))
    return datetime.combine(local_day, clock_time(hours, minutes), tzinfo=zone)


def skip_reason(db, user: User, slot: Slot, now: datetime) -> str | None:
    """Why ``slot`` does not go out at ``now``, or None when it is due. The checks on
    the settings come first and those on the database last (§5.3)."""
    cfg = config(user)
    zone = settings_screen.user_zone(user)
    local_now = now.astimezone(zone)
    local_day = local_now.date()
    start = slot_start(slot, local_day, zone)
    if cfg.mode == OFF:
        return "off"
    if not start <= local_now < start + WINDOW:
        return "not its hour"
    if cfg.since is not None and start <= cfg.since:
        return "turned on after the slot"
    if cfg.off_day == local_day:
        return "off today"
    if (
        slot.kind == EVENING
        and local_day.weekday() == SUNDAY
        and settings_screen.week_push_on(user)
    ):
        return "the week's summary instead"  # R17
    if not access_control.may_receive(db, user.telegram_id):
        return "may not receive"
    if not crud_meal.any_live(db, user.id):
        return "no record ever"
    last = crud_meal.last_sent_at(db, user.id)
    if last is None or (local_day - last.astimezone(zone).date()).days > (
        STOPPED_AFTER_DAYS
    ):
        return "no record for a week"
    if quiet_now(db, user, local_day, now):
        return "quiet"
    if crud_reminder_send.exists(db, user.id, local_day, slot.at):
        return "already claimed"
    if crud_reminder_send.count_on(db, user.id, local_day) >= MAX_A_DAY:
        return "three today"
    if slot.kind == EVENING and crud_reminder_send.count_on(
        db, user.id, local_day, kind=EVENING
    ):
        return "the evening one went out"  # an hour moved after it was sent
    # In UTC: SQLite compares the stored text, written in UTC.
    if crud_meal.any_sent_between(db, user.id, (start - FRESH).astimezone(UTC), now):
        return "fresh record"  # R8
    if meal_card.has_slot(db, user.telegram_id):
        return "a question is open"  # the next sweep inside the hour tries again
    return None


def due(db, user: User, now: datetime) -> Slot | None:
    """The slot to send now, if any. Each skip is logged with its reason, no text."""
    zone = settings_screen.user_zone(user)
    local_day = now.astimezone(zone).date()
    for slot in slots_today(config(user), None, local_day):
        reason = skip_reason(db, user, slot, now)
        if reason is None:
            return slot
        logger.debug("reminders: %s skipped at %s: %s", user.id, slot.at, reason)
    return None


# --- unanswered → silent (§5.6) ---------------------------------------------------


def outcome(send: ReminderSend) -> str:
    if send.answer in _ANSWERS:
        return ANSWERED
    if send.answer in _NEUTRAL_ANSWERS or send.status == "maybe":
        return NEUTRAL
    if send.status in ("sent", "blocked"):
        return UNANSWERED_SEND
    return NEUTRAL  # claimed and never sent: the person saw nothing


def streak(sends: list[ReminderSend]) -> int:
    """Unanswered ones in a row, newest first; neutral ones are stepped over."""
    count = 0
    for send in sends:
        result = outcome(send)
        if result == ANSWERED:
            break
        if result == UNANSWERED_SEND:
            count += 1
    return count


def level(cfg: Config, unanswered: int) -> str:
    """One slot a day: full, or off after ``k`` in a row (one after a return)."""
    k = 1 if cfg.fragile else UNANSWERED
    return OFF if unanswered >= k else "full"


def resume_due(db, user: User, cfg: Config, local_day: date, now: datetime) -> bool:
    """A return after a break: a day with no meal row after the pause began, then
    rows on two days after it, ``local_day`` being at most the second of them."""
    paused_on = _day((cfg.quiet or {}).get("on"))
    if paused_on is None:
        return False
    zone = settings_screen.user_zone(user)
    first = paused_on + timedelta(days=1)
    start = datetime.combine(first, clock_time(0), tzinfo=zone)
    end = now + timedelta(seconds=1)
    days = crud_meal.record_days(db, user.id, start.astimezone(UTC), end, zone)
    gap = first
    while gap < local_day and gap in days:
        gap += timedelta(days=1)
    if gap >= local_day:
        return False
    return len({day for day in days if gap < day <= local_day}) >= 2


def quiet_now(db, user: User, local_day: date, now: datetime) -> bool:
    """The pause, kept up to date: a quiet person may have come back, and a person
    who is not quiet may have left three days without an answer behind."""
    cfg = config(user)
    if cfg.quiet is not None:
        if not resume_due(db, user, cfg, local_day, now):
            return True
        _write(db, user, quiet=None, fragile=True, count_from=now.isoformat())
        logger.info("reminders: %s back after a break", user.id)
        return False
    # A day still open can be answered, so only the days before it count.
    sends = crud_reminder_send.newest_first(
        db, user.id, after=cfg.count_from, before_day=local_day
    )
    latest = next((s for s in sends if outcome(s) != NEUTRAL), None)
    if cfg.fragile and latest is not None and outcome(latest) == ANSWERED:
        _write(db, user, fragile=None)
        cfg = config(user)
    if level(cfg, streak(sends)) == OFF:
        # Dated by the send that completed the streak, not by this sweep: the
        # sweep runs a day later, and that day may be the return's gap day (B2).
        _write(db, user, quiet={"level": OFF, "on": latest.local_day.isoformat()})
        logger.info("reminders: %s quiet after unanswered ones", user.id)
        return True
    return False


# --- which ⭐ -----------------------------------------------------------------------


def pick_favorites(
    db, user: User, local_day: date, zone, before: datetime | None = None
) -> list[tuple[FavoriteMeal, str]]:
    """Up to four (⭐, its name): one eaten that day goes last, then the most days
    in four weeks, then the newest ⭐ first. ``before``: count only what came
    before the reminder, so a tap on it does not reorder its own chips. The last
    key is when the ⭐ was saved, not ``list_active``'s last use: a tap moves that,
    and every chip under the finger would move with it."""
    favs = crud_favorite_meal.list_active(db, user.id)
    if not favs:
        return []
    start = datetime.combine(
        local_day - timedelta(days=RANK_DAYS - 1), clock_time(0), tzinfo=zone
    )
    end = datetime.combine(local_day + timedelta(days=1), clock_time(0), tzinfo=zone)
    stmt = (
        select(MealItem.personal_food_id, Meal.meal_time)
        .join(Meal, Meal.id == MealItem.meal_id)
        .where(
            Meal.user_id == user.id,
            Meal.deleted_at.is_(None),
            Meal.is_draft.is_(False),
            MealItem.removed_at.is_(None),
            MealItem.personal_food_id.is_not(None),
            # In UTC: SQLite compares the stored text, written in UTC.
            Meal.meal_time >= start.astimezone(UTC),
            Meal.meal_time < end.astimezone(UTC),
        )
    )
    if before is not None:
        stmt = stmt.where(Meal.sent_at < meal_items._utc(before))
    days = defaultdict(set)
    for dish_id, moment in db.execute(stmt):
        days[dish_id].add(local_time(moment, zone).date())
    ranked = []
    for fav in favs:
        dish = crud_favorite_meal.dish_if_any(db, fav)
        eaten = days.get(dish.id, set()) if dish is not None else set()
        name = dish.canonical_name if dish is not None else fav.name
        saved = -meal_card._stamp(fav.created_at)
        ranked.append(((local_day in eaten, -len(eaten), saved, -fav.id), fav, name))
    ranked.sort(key=lambda entry: entry[0])
    return [(fav, name) for _key, fav, name in ranked[:CHIPS]]


def _chip_rows(send: ReminderSend, picks: list) -> list:
    chips = [
        _btn(
            meal_card._cut(meal_card._capital(name), favorites.PAIR_LABEL_MAX),
            f"f:{send.id}:{fav.id}",
        )
        for fav, name in picks
    ]
    return [chips[i : i + 2] for i in range(0, len(chips), 2)]


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=CALLBACK_PREFIX + data)


# --- the message (§3.1) -----------------------------------------------------------


def is_first(cfg: Config, send: ReminderSend) -> bool:
    """The first reminder a person ever gets, and that one again when redrawn."""
    return cfg.noted is None or cfg.noted == send.id


def reminder_view(db, user: User, send: ReminderSend, now: datetime) -> Screen:
    cfg = config(user)
    zone = settings_screen.user_zone(user)
    picks = pick_favorites(db, user, send.local_day, zone, before=send.sent_at)
    first = is_first(cfg, send)
    lines = [TITLE]
    if first:
        lines.append(_FIRST)
        if picks:
            lines.append(_FIRST_STARS)
        label = settings_screen.zone_label(settings_screen.zone_name(user), now)
        lines.append(_FOOTNOTE.format(slot=send.slot, zone=label, sound=_sound(cfg)))
    else:
        lines.append(_LATER_STARS if picks else _LATER)
    rows = _chip_rows(send, picks)
    rows.append([_btn("✅ ОК", f"k:{send.id}"), _btn("✖ Выключить", f"x:{send.id}")])
    if first and not (user.settings or {}).get(settings_screen.TIMEZONE):
        rows.append(
            [
                InlineKeyboardButton(
                    ZONE_LABEL, callback_data=settings_screen.ZONES_CALLBACK
                )
            ]
        )
    return Screen("\n".join(lines), InlineKeyboardMarkup(rows), view=meal_card.NO_MEAL)


def _logs(db, user: User, send: ReminderSend) -> list[Meal]:
    """What was logged from this reminder, deleted ones too, in order."""
    stmt = (
        select(Meal)
        .where(
            Meal.user_id == user.id,
            Meal.resolution_source == favorites.FAVORITE_SOURCE,
            Meal.sent_at >= send.sent_at,
        )
        .order_by(Meal.id)
    )
    return [
        meal
        for meal in db.scalars(stmt)
        if (meal.resolution_signals or {}).get("send_id") == send.id
    ]


def _logged_line(meal: Meal, zone, today: date) -> str:
    name = meal_card._esc(meal_card._capital(meal.description or "Блюдо"))
    if meal.deleted_at is not None:
        return f"↩️ Отменено: {name}."
    day = local_time(meal.meal_time, zone).date()
    when = "сегодня" if day == today else stats_screen._card_title(day)
    return f"✓ Записано на {when}, {clock(meal, zone)}: {name}."


def tap_view(db, user: User, send: ReminderSend, now: datetime) -> Screen:
    """The reminder after a ⭐: a line for each, ↩️ and 🕐 for the latest still
    logged, so undoing the newest leaves the one before it in reach (A3)."""
    zone = settings_screen.user_zone(user)
    logs = _logs(db, user, send)
    today = now.astimezone(zone).date()
    lines = [TITLE] + [_logged_line(meal, zone, today) for meal in logs] + [_MORE]
    latest = next((meal for meal in reversed(logs) if meal.deleted_at is None), None)
    live = latest is not None
    rows = []
    if live:
        rows.append(ok_row())
        rows.append(
            [
                _btn("↩️ Отменить", f"u:{send.id}:{latest.id}"),
                InlineKeyboardButton("🕐 Время", callback_data=f"ml:t:{latest.id}:r"),
            ]
        )
    picks = pick_favorites(db, user, send.local_day, zone, before=send.sent_at)
    rows += _chip_rows(send, picks)
    return Screen(
        "\n".join(lines),
        InlineKeyboardMarkup(rows),
        meal_id=latest.id if live else None,
        view=None if live else meal_card.NO_MEAL,
    )


def _redraw(t, send: ReminderSend, toast: str) -> Screen:
    if _logs(t.db, t.user, send):
        return replace(tap_view(t.db, t.user, send, t.now), toast=toast)
    return replace(reminder_view(t.db, t.user, send, t.now), toast=toast)


# --- the sub-screen (§4.2) --------------------------------------------------------


def settings_view(
    user: User | None, now: datetime, notice: str | None = None
) -> Screen:
    cfg = config(user)
    week = settings_screen.week_push_on(user)
    lines = [f"<i>{notice}</i>", ""] if notice else []
    lines += [_SETTINGS_TITLE, ""]
    if cfg.mode == OFF:
        lines.append("Сейчас: выключены.")
    elif cfg.quiet is not None:
        lines.append("Сейчас не приходят. Включить — кнопкой ✓ 🕘 Вечером.")
    else:
        label = settings_screen.zone_label(settings_screen.zone_name(user), now)
        lines.append(f"Сейчас: {phrase(cfg)} по времени {label}.")
    about = (
        f"Одно сообщение в день {_sound(cfg)}: можно рассказать голосовым, что было, "
        "или нажать на блюдо из ⭐. Если были записи за три часа до него, оно не "
        "приходит."
    )
    if week:
        about += " В воскресенье вместо него приходит итог недели."
    lines.append(about)
    lines.append("🗓 Итог недели: " + ("вс, 22:00." if week else "не присылаю."))
    off = cfg.mode == OFF
    rows = [
        [
            _btn("✓ Выкл" if off else "✖ Выкл", f"m:{OFF}"),
            _btn(("" if off else "✓ ") + "🕘 Вечером", f"m:{EVENING}"),
        ]
    ]
    if not off:
        rows.append(
            [
                _btn(
                    ("✓ " if at == cfg.evening else "") + at, "e:" + at.replace(":", "")
                )
                for at in EVENING_TIMES
            ]
        )
    rows.append(
        [
            _btn(f"🗓 Итог недели: {'вкл' if week else 'выкл'}", "week"),
            InlineKeyboardButton(
                ZONE_LABEL, callback_data=settings_screen.ZONES_CALLBACK
            ),
        ]
    )
    rows.append([_btn(f"🔔 Звук: {'вкл' if cfg.sound else 'выкл'}", "snd")])
    rows.append(
        [
            InlineKeyboardButton(
                "⬅️ Настройки", callback_data=settings_screen.CALLBACK_PREFIX + "open"
            )
        ]
    )
    return Screen("\n".join(lines), InlineKeyboardMarkup(rows), view=meal_card.NO_MEAL)


# --- the verbs --------------------------------------------------------------------


def _unknown(t, data: str) -> Screen:
    logger.warning("reminders: unknown or foreign data %r", data)
    return meal_card._today(t)


def _own_send(t, data: str, sid: str) -> ReminderSend:
    send = t.db.get(ReminderSend, int(sid)) if int(sid) <= _INT_MAX else None
    if send is None or t.user is None or send.user_id != t.user.id:
        raise meal_card._Answer(_unknown(t, data))
    return send


def _v_fav(t, data: str, sid: str, fid: str) -> Screen:
    """§3.3: on the reminder's day, without a clock; a late tap opens the list."""
    send = _own_send(t, data, sid)
    zone = t.zone
    if (t.now.astimezone(zone).date() - send.local_day).days > LATE_DAYS:
        return favorites.open_list(t.db, t.telegram_id, 0, LATE_NOTICE)
    if int(fid) > _INT_MAX:
        return _unknown(t, data)  # no row ever had it: malformed, not stale
    fav = t.db.get(FavoriteMeal, int(fid))
    if fav is not None and fav.user_id != t.user.id:
        return _unknown(t, data)
    if fav is None or fav.removed_at is not None:
        return _redraw(t, send, STALE_TOAST)
    dish = crud_favorite_meal.dish_if_any(t.db, fav)
    if dish and favorites.just_logged(t.db, t.user.id, t.message_id, dish.id, t.now):
        return Screen("", None, toast=favorites.LOG_AGAIN_TOAST)
    name = dish.canonical_name if dish is not None else fav.name
    meal = favorites.log_at(
        t,
        fav,
        dish,
        name,
        meal_time=meal_card.noon_of(send.local_day, zone).astimezone(UTC),
        precision=DAY_ONLY,
        source=SOURCE,
        extra_signals={"via": "reminder", "send_id": send.id},
    )
    send.answer = "fav"
    send.answered_at = t.now
    t.db.commit()
    if config(t.user).fragile:
        _write(t.db, t.user, fragile=None)  # an answer: back to the full count
    screen = tap_view(t.db, t.user, send, t.now)
    return replace(screen, toast=favorites.LOG_TOAST, meal_id=meal.id, view=None)


def _v_undo(t, data: str, sid: str, mid: str) -> Screen:
    send = _own_send(t, data, sid)
    meal = t.db.get(Meal, int(mid)) if int(mid) <= _INT_MAX else None
    if (
        meal is None
        or meal.user_id != t.user.id
        or (meal.resolution_signals or {}).get("send_id") != send.id
    ):
        return _unknown(t, data)
    if meal.deleted_at is not None or meal.resolution_source != (
        favorites.FAVORITE_SOURCE
    ):
        return replace(tap_view(t.db, t.user, send, t.now), toast=UNDONE_TOAST)
    # Soft, as every delete: a pull holding this row learns of it.
    meal_items.delete_meal(t.db, t.user.id, meal.id)
    if send.answer == "fav" and all(
        log.deleted_at is not None for log in _logs(t.db, t.user, send)
    ):
        # Nothing logged from it any more: not an answer, and the next send may
        # delete it (R4). Neutral, as ✅ ОК: the person did look at it.
        send.answer = "ok"
        t.db.commit()
    return replace(tap_view(t.db, t.user, send, t.now), toast=UNDO_TOAST)


def _v_ok(t, data: str, sid: str) -> Screen:
    """✅ ОК: the buttons go, the text stays; neutral for the ladder."""
    send = _own_send(t, data, sid)
    if send.answer is None:
        send.answer = "ok"
    if send.closed_at is None:
        send.closed_at = t.now  # dismissed; the next send still deletes it
    t.db.commit()
    return Screen("", None, toast=OK_TOAST, view=STRIP)


def _v_off(t, data: str, sid: str) -> Screen:
    """✖: off at once, no question; ↩️ puts back what was there."""
    send = _own_send(t, data, sid)
    if send.answer is None:
        send.answer = "off"
    if send.closed_at is None:
        send.closed_at = t.now
    cfg = config(t.user)
    if cfg.mode != OFF:
        # Tied to this message: its ↩️ only puts back what this ✖ took away.
        prev = {"mode": cfg.mode, "evening": cfg.evening, "send": send.id}
        _write(t.db, t.user, mode=OFF, prev=prev)
    else:
        t.db.commit()  # the send's answer; the settings stay as they are
    rows = [
        ok_row(),
        [_btn(meal_card.UNDO_LABEL, f"b:{send.id}"), _btn("⚙️ Настроить", "o")],
    ]
    text = f"{_OFF_TITLE}\n{_TURNED_OFF}"
    return Screen(text, InlineKeyboardMarkup(rows), view=meal_card.NO_MEAL)


def _v_back(t, data: str, sid: str) -> Screen:
    """↩️ Вернуть under ✖: what that ✖ took, once. A second tap, or one after a
    change in ⚙️, leaves the settings as they are and says what they are."""
    send = _own_send(t, data, sid)
    cfg = config(t.user)
    prev = cfg.prev or {}
    if prev.get("send") == send.id:
        mode = prev.get("mode") if prev.get("mode") in MODES else EVENING
        evening = prev.get("evening")
        _write(
            t.db,
            t.user,
            mode=mode,
            evening=evening if evening in EVENING_TIMES else cfg.evening,
            prev=None,
        )
    now_on = config(t.user)
    said = (
        "✓ Напоминания выключены."
        if now_on.mode == OFF
        else f"✓ Напоминания включены: {phrase(now_on)}."
    )
    return Screen(f"{_OFF_TITLE}\n{said}", None, view=meal_card.NO_MEAL)


def _v_open(t, data: str) -> Screen:
    return settings_view(t.user, t.now)


def _v_mode(t, data: str, mode: str) -> Screen:
    cfg = config(t.user)
    if mode == OFF:
        if cfg.mode != OFF:
            _write(
                t.db,
                t.user,
                mode=OFF,
                prev={"mode": cfg.mode, "evening": cfg.evening},
            )
        return settings_view(t.user, t.now, "✓ Напоминания выключены")
    stamp = t.now.isoformat()
    values = {"mode": EVENING, "off_day": None, "prev": None}
    if cfg.mode == OFF or cfg.quiet is not None:
        # The hour just passed does not go out at once: not while the person is
        # still on this screen.
        values.update(since=stamp, quiet=None, fragile=None, count_from=stamp)
    _write(t.db, t.user, **values)
    if cfg.mode != OFF and cfg.quiet is not None:
        notice = "✓ Напоминания приходят"
    else:
        notice = f"✓ Напоминания: {phrase(config(t.user))}"
    return settings_view(t.user, t.now, notice)


def _v_evening(t, data: str, digits: str) -> Screen:
    at = f"{digits[:2]}:{digits[2:]}"
    if at not in EVENING_TIMES:
        return _unknown(t, data)
    if config(t.user).mode == OFF:
        # A stale hour button while off: the hour is kept, they stay off.
        _write(t.db, t.user, evening=at)
    else:
        _write(t.db, t.user, evening=at, since=t.now.isoformat(), prev=None)
    return settings_view(t.user, t.now, f"✓ Время: {at}")


def _v_week(t, data: str) -> Screen:
    on = not settings_screen.week_push_on(t.user)
    settings_screen._save(t.db, t.telegram_id, **{settings_screen.WEEK_PUSH: on})
    t.db.refresh(t.user)
    notice = "✓ Итог недели включён" if on else "✓ Итог недели выключен"
    return settings_view(t.user, t.now, notice)


def _v_sound(t, data: str) -> Screen:
    on = not config(t.user).sound
    _write(t.db, t.user, sound=on)
    return settings_view(t.user, t.now, "✓ Со звуком" if on else "✓ Без звука")


_N = r"(\d{1,12})"
_VERBS = [
    (re.compile(rf"rm:f:{_N}:{_N}"), _v_fav),
    (re.compile(rf"rm:u:{_N}:{_N}"), _v_undo),
    (re.compile(rf"rm:k:{_N}"), _v_ok),
    (re.compile(rf"rm:x:{_N}"), _v_off),
    (re.compile(rf"rm:b:{_N}"), _v_back),
    (re.compile(r"rm:o"), _v_open),
    (re.compile(rf"rm:m:({OFF}|{EVENING})"), _v_mode),
    (re.compile(r"rm:e:(\d{4})"), _v_evening),
    (re.compile(r"rm:week"), _v_week),
    (re.compile(r"rm:snd"), _v_sound),
]


def handle_callback(
    db,
    telegram_id: int,
    data: str,
    now: datetime | None = None,
    message_id: int | None = None,
) -> Screen:
    """The screen a tap on a 🔔 button leads to. Every id in ``data`` is checked
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
