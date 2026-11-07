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
an answer make them rarer, one every third evening, and six stop them; only a return
after a break in the records, or a tap in ⚙️, starts them again (R9, D7). None of this
is ever said in the chat.

After a lapse in the records, one short message at the seventh day and one at the
fourteenth, then silence until food comes (D6): neutral for the ladder, and no recall
window, so whatever comes after one is a capture at its own time.

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
UNANSWERED = 3  # in a row, before they come rarer (D7)
UNANSWERED_OFF = 6  # in a row, before they stop (D7, R9)
SPARSE_EVERY = 3  # rarer: a regular one at most every third local day (D7)
DELETE_WITHIN = timedelta(hours=48)  # Telegram deletes a bot's message for 48 hours
LATE_DAYS = 2  # a chip on an older reminder opens ⭐ Избранное instead
CHIPS = 4
RANK_DAYS = 28
SUNDAY = 6
_INT_MAX = 2**31 - 1

# Re-entry after a lapse (D6): ReminderSend.kind, and its two slots. The spec's
# «reentry7» / «reentry14», short enough for reminder_sends.slot (String(5)): the
# UNIQUE(user_id, local_day, slot) claim holds them with no migration.
REENTRY = "reentry"
REENTRY_FIRST, REENTRY_SECOND = "re7", "re14"
# Local days since the last meal row: [from, to) for each.
REENTRY_DAYS = {REENTRY_FIRST: (7, 14), REENTRY_SECOND: (14, 30)}

# The ladder (§5.6): what a send's ``answer`` and ``status`` mean.
FULL, SPARSE = "full", "sparse"
ANSWERED, NEUTRAL, UNANSWERED_SEND = "answered", "neutral", "unanswered"
FAV, NONE = "fav", "none"
# R16 over a ⭐: the ⭐ still holds the message (R4), and «ничего» closed it.
FAV_NONE = "fav_none"
_ANSWERS = (FAV, "words", NONE, FAV_NONE)
_NEUTRAL_ANSWERS = ("ok", "off")
STRIP = "strip"  # Screen.view: take the buttons off the message, keep its text

TITLE = "🔔 <b>Еда за сегодня</b>"
_FIRST = (
    "Можно не отвечать. Если что-то вспоминается, можно рассказать голосовым или "
    "написать — примерно и не всё. Время можно назвать: «в 13 суп». Без времени "
    "запишется на этот день, без часов."
)
_FIRST_STARS = "Или нажать на блюдо из ⭐:"
_LATER_STARS = (
    "Можно не отвечать. Если что-то вспоминается — голосовым, текстом или блюдом из "
    "⭐, можно примерно и не всё:"
)
_LATER = (
    "Можно не отвечать. Если что-то вспоминается — голосовым или текстом, можно "
    "примерно и не всё."
)
# D6: the owner's own words; R10's «never names an absence» is waived for these two.
REENTRY_TEXT = {
    REENTRY_FIRST: (
        "Можно начать с сегодняшнего. Прошлые дни восстанавливать не нужно."
    ),
    REENTRY_SECOND: (
        "Могу больше не напоминать. Если захочется — можно просто отправить еду в "
        "любой момент."
    ),
}
_FOOTNOTE = (
    "<i>Приходит в {slot} по времени {zone}, {sound}. Если были записи за три часа до "
    "него, не приходит. Выключить — кнопкой ✖. Текст на экране блокировки можно "
    "скрыть в настройках уведомлений Telegram.</i>"
)
_MORE = "Можно нажать ещё или рассказать голосовым."
_SETTINGS_TITLE = "<b>🔔 Напоминания</b>"
PAUSED_LINE = "Сейчас не приходят. Включить — кнопкой ✓ 🕘 Вечером."
SPARSE_LINE = (
    "Сейчас приходят реже, потому что остаются без ответа. Как раньше — кнопкой "
    "✓ 🕘 Вечером."
)
# No record for longer than R3 allows, or the day of a return after a re-entry.
STOPPED_LINE = "Сейчас не приходят: придут на следующий день после любой записи."
_OFF_TITLE = "🔔 <b>Напоминания</b>"
_TURNED_OFF = "✓ Напоминания выключены. Включить: ⚙️ Настройки → 🔔 Напоминания."
LATE_NOTICE = "<i>Это напоминание за прошлые дни. Вот ⭐ Избранное.</i>"
STALE_TOAST = "Этого блюда уже нет в избранном"
OK_TOAST = "Понятно"
UNDO_TOAST = "Отменено"
UNDONE_TOAST = "Эта запись уже отменена"
ZONE_LABEL = "🕐 Часовой пояс"
ACCEPTED = "✓ Принято."  # a «ничего» answered it (R16)

# §3.2: what a voice or a text after a reminder is bound to it by.
RECALL_WINDOW = timedelta(minutes=30)  # after the send, renewed by each bound answer
RECALL_CAP = timedelta(minutes=90)  # never longer than this after the send
_LIVE = ("sent", "maybe")
_PHOTOS = ("photo", "document_photo")
# Food in front of the person now: its time is the sent one, whatever came before.
_NOW_WORDS = re.compile(r"\b(?:сейчас|только\s+что)\b")
# R16: a day with little or nothing, said as an answer; no dish in it to read.
# «нет, ничего», «да ничего», «не ела ничего сегодня» are one too; «нет, не ел» stays a
# «нет» (says_no), as §3.2 has it. A word after «ничего» other than these is food's
# or a remark's: «ничего кроме супа», «ничего особенного». On the text with «ё» as «е».
_SOFTLY = r"(?:(?:сегодня|больше|почти|вообще)\s+)*"
_NIL = r"(?:ничего|ничо|ниче)"
_NOTHING = re.compile(
    rf"(?:(?:нет|да|ну)[,.!…]?\s+(?={_SOFTLY}{_NIL}))?"
    + _SOFTLY
    + rf"(?:{_NIL}|не\s+ел[аио]?(?:\s+{_NIL})?|не\s+было)"
    r"(?:(?:\s+(?:сегодня|больше))*(?:\s+не\s+ел[аио]?|\s+не\s+было))?"
    r"(?:\s+(?:сегодня|больше))*[.!…]*"
    r"(?:,?\s*спасибо)?[.!…]*"
)


@dataclass(frozen=True)
class Slot:
    at: str  # "21:00", local wall clock
    kind: str  # evening, meal, reentry
    centre: str | None = None  # meal slots (R2, R3)
    tag: str | None = None  # a re-entry's own slot name (D6)

    @property
    def key(self) -> str:
        """What ``reminder_sends.slot`` holds: the clock, or a re-entry's name."""
        return self.tag or self.at


@dataclass(frozen=True)
class Config:
    mode: str = EVENING
    evening: str = DEFAULT_EVENING
    sound: bool = False
    since: datetime | None = None
    off_day: date | None = None
    noted: int | None = None  # the id of the first reminder ever sent
    quiet: dict | None = None  # {"level": "off" | "sparse", "on": "2026-11-04"}
    fragile: bool = False
    count_from: datetime | None = None
    prev: dict | None = None
    moved: int | None = None  # the send whose message a tap made another screen (R4)


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
        isinstance(quiet, dict)
        and quiet.get("level") in (OFF, SPARSE)
        and _day(quiet.get("on"))
    ):
        quiet = None
    noted = raw.get("noted")
    prev = raw.get("prev")
    moved = raw.get("moved")
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
        moved=moved if isinstance(moved, int) and not isinstance(moved, bool) else None,
    )


def _write(db, user: User, **values) -> None:
    """One read-modify-write of the reminders' dict; None takes a key out."""
    stored = {**_raw(user), **values}
    stored = {key: value for key, value in stored.items() if value is not None}
    # A new dict: SQLAlchemy does not see a JSON value changed in place.
    user.settings = {**(user.settings or {}), KEY: stored}
    db.commit()


def paused(cfg: Config) -> bool:
    """The pause: no regular reminder until a return or a tap in ⚙️ (R9)."""
    return (cfg.quiet or {}).get("level") == OFF


def rarer(cfg: Config) -> bool:
    """Rarer: a regular reminder only every third evening (D7)."""
    return (cfg.quiet or {}).get("level") == SPARSE


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
    gap = _gap(db, user, local_day, zone)
    if gap is None or gap > STOPPED_AFTER_DAYS:
        return "no record for a week"
    if back_after_reentry(db, user, local_day, zone):
        return "back after a re-entry"  # not watched the same evening
    reason = ladder_reason(db, user, local_day, now)
    if reason is not None:
        return reason
    if crud_reminder_send.exists(db, user.id, local_day, slot.key):
        return "already claimed"
    if crud_reminder_send.count_on(db, user.id, local_day, kind=REENTRY):
        return "a re-entry went out today"  # D6: the day's one message (B1)
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


def _gap(db, user: User, local_day: date, zone) -> int | None:
    """Local days from the last meal row (any: drafts and deleted too) to
    ``local_day``; None when there is none."""
    last = crud_meal.last_sent_at(db, user.id)
    if last is None:
        return None
    return (local_day - last.astimezone(zone).date()).days


def back_after_reentry(db, user: User, local_day: date, zone) -> bool:
    """``local_day`` is the day of the first record after the latest re-entry that
    reached the chat: no regular reminder that evening, the usual rules from the
    next one (acceptance 2026-11-07). The re-entry went out after the record before
    that one, or it would not have gone."""
    reentry = crud_reminder_send.latest(db, user.id, kind=REENTRY, statuses=_LIVE)
    if reentry is None:
        return False
    first = crud_meal.first_sent_after(db, user.id, meal_items._utc(reentry.sent_at))
    return first is not None and first.astimezone(zone).date() == local_day


def stopped(db, user: User, now: datetime) -> bool:
    """No regular reminder until a record: none for longer than R3 allows (while a
    record was ever made), or today is the return after a re-entry."""
    zone = settings_screen.user_zone(user)
    local_day = now.astimezone(zone).date()
    gap = _gap(db, user, local_day, zone)
    if gap is not None and gap > STOPPED_AFTER_DAYS:
        return True
    return gap is not None and back_after_reentry(db, user, local_day, zone)


def reentry_wanted(gap: int, held: bool) -> list[str]:
    """The re-entry messages a day ``gap`` local days after the last record may
    carry, the first one first (D6). A first one that a Sunday's week card held
    back goes the evening after it, even on the fourteenth day. Nothing from the
    thirtieth day on."""
    wanted = []
    low, high = REENTRY_DAYS[REENTRY_FIRST]
    if low <= gap < high or (held and gap == high):
        wanted.append(REENTRY_FIRST)
    low, high = REENTRY_DAYS[REENTRY_SECOND]
    if low <= gap < high:
        wanted.append(REENTRY_SECOND)
    return wanted


def held_by_sunday(user: User, cfg: Config, local_day: date, zone) -> bool:
    """Yesterday was a Sunday whose evening slot a re-entry was due at but for the
    week card (R17), so that card is the only thing that held it back (D6): the
    week card on, the evening mode and its hour set before that slot (``since`` is
    written whenever ✓ turns them on and whenever the hour moves), and that day not
    taken off. With no
    ``since`` and no reminder ever sent, nothing says the sweep ran for this person
    then (the launch), so nothing was held back."""
    sunday = local_day - timedelta(days=1)
    if sunday.weekday() != SUNDAY or not settings_screen.week_push_on(user):
        return False
    if cfg.mode != EVENING or cfg.off_day == sunday:
        return False
    if cfg.since is None:
        return cfg.noted is not None
    return cfg.since < slot_start(Slot(cfg.evening, REENTRY), sunday, zone)


def reentry_check(db, user: User, now: datetime) -> tuple[Slot | None, str | None]:
    """(the re-entry slot due now, None) or (None, why not) (D6). §5.3's checks but
    the week without records (R3), the ladder and the fresh record (R8): it is the
    one message for someone who stopped, so a pause does not hold it back. Each
    goes once a lapse: not again after the last meal row."""
    cfg = config(user)
    if cfg.mode != EVENING:
        return None, "off"
    zone = settings_screen.user_zone(user)
    local_now = now.astimezone(zone)
    local_day = local_now.date()
    start = slot_start(Slot(cfg.evening, REENTRY), local_day, zone)
    if not start <= local_now < start + WINDOW:
        return None, "not its hour"
    if cfg.since is not None and start <= cfg.since:
        return None, "turned on after the slot"
    if cfg.off_day == local_day:
        return None, "off today"
    week = settings_screen.week_push_on(user)
    if local_day.weekday() == SUNDAY and week:
        return None, "the week's summary instead"  # R17: the next evening
    if not access_control.may_receive(db, user.telegram_id):
        return None, "may not receive"
    if not crud_meal.any_live(db, user.id):
        return None, "no record ever"
    if not crud_reminder_send.any_received(db, user.id, statuses=_LIVE):
        # Never the first message a person gets (acceptance 2026-11-07, C2).
        return None, "never got a reminder"
    last = crud_meal.last_sent_at(db, user.id)
    gap = (local_day - last.astimezone(zone).date()).days
    held = held_by_sunday(user, cfg, local_day, zone)
    # In UTC: SQLite compares the stored text, written in UTC.
    after = last.astimezone(UTC)
    tag = next(
        (
            name
            for name in reentry_wanted(gap, held)
            if not crud_reminder_send.sent_after(db, user.id, name, after)
        ),
        None,
    )
    if tag is None:
        return None, "no re-entry due"
    if crud_reminder_send.count_on(db, user.id, local_day):
        return None, "one went out today"
    if meal_card.has_slot(db, user.telegram_id):
        return None, "a question is open"
    return Slot(cfg.evening, REENTRY, None, tag), None


def due(db, user: User, now: datetime) -> Slot | None:
    """The slot to send now, if any: a re-entry first, then the day's own. Each
    skip is logged with its reason, no text."""
    slot, reason = reentry_check(db, user, now)
    if slot is not None:
        return slot
    logger.debug("reminders: %s re-entry skipped: %s", user.id, reason)
    zone = settings_screen.user_zone(user)
    local_day = now.astimezone(zone).date()
    for slot in slots_today(config(user), None, local_day):
        reason = skip_reason(db, user, slot, now)
        if reason is None:
            return slot
        logger.debug("reminders: %s skipped at %s: %s", user.id, slot.at, reason)
    return None


# --- unanswered → silent (§5.6) ---------------------------------------------------


def is_reentry(send: ReminderSend | None) -> bool:
    return send is not None and send.kind == REENTRY


def holds_star(send: ReminderSend) -> bool:
    """A ⭐ was logged from it: its message stays in the chat (R4)."""
    return send.answer in (FAV, FAV_NONE)


def said_nothing(send: ReminderSend) -> bool:
    """A «ничего» closed it (R16), over a ⭐ or not."""
    return send.answer in (NONE, FAV_NONE)


def outcome(send: ReminderSend) -> str:
    if is_reentry(send):
        return NEUTRAL  # D6: it asks nothing, so nothing counts either way
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
    """One slot a day: full; rarer after three in a row; off after six, or after
    one once they came back (D7)."""
    if cfg.fragile:
        return OFF if unanswered >= 1 else FULL
    if unanswered >= UNANSWERED_OFF:
        return OFF
    return SPARSE if unanswered >= UNANSWERED else FULL


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


def ladder_reason(db, user: User, local_day: date, now: datetime) -> str | None:
    """The ladder, kept up to date (D7): "quiet" while paused, "rarer" on a day
    the rarer level skips, None when the regular reminder may go. A paused person
    may have come back; anyone else may have left days without an answer behind,
    or answered one, which is daily again."""
    cfg = config(user)
    if paused(cfg):
        if not resume_due(db, user, cfg, local_day, now):
            return "quiet"
        _write(db, user, quiet=None, fragile=True, count_from=now.isoformat())
        logger.info("reminders: %s back after a break", user.id)
        return None
    # A day still open can be answered, so only the days before it count.
    sends = crud_reminder_send.newest_first(
        db, user.id, after=cfg.count_from, before_day=local_day
    )
    # Neutral ones (re-entries, ✅ ОК) are stepped over: with a streak, this is
    # its newest unanswered send, the one that completed it.
    latest = next((s for s in sends if outcome(s) != NEUTRAL), None)
    if cfg.fragile and latest is not None and outcome(latest) == ANSWERED:
        _write(db, user, fragile=None)
        cfg = config(user)
    now_level = level(cfg, streak(sends))
    if now_level == FULL:
        if cfg.quiet is not None:
            _write(db, user, quiet=None)  # an answer: daily again
            logger.info("reminders: %s daily again after an answer", user.id)
        return None
    if now_level != (cfg.quiet or {}).get("level"):
        # Dated by the send that completed the streak, not by this sweep: the
        # sweep runs a day later, and that day may be the return's gap day (B2).
        _write(db, user, quiet={"level": now_level, "on": latest.local_day.isoformat()})
        logger.info("reminders: %s %s after unanswered ones", user.id, now_level)
    if now_level == OFF:
        return "quiet"
    last = crud_reminder_send.last_regular_day(db, user.id, besides=REENTRY)
    if last is not None and local_day < last + timedelta(days=SPARSE_EVERY):
        return "rarer"
    return None


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


def reentry_view(send: ReminderSend) -> Screen:
    """D6: the title, the owner's words, [✅ ОК] [✖ Выключить]; no ⭐, no footnote."""
    text = REENTRY_TEXT.get(send.slot, REENTRY_TEXT[REENTRY_FIRST])
    rows = [[_btn("✅ ОК", f"k:{send.id}"), _btn("✖ Выключить", f"x:{send.id}")]]
    return Screen(
        f"{TITLE}\n{text}", InlineKeyboardMarkup(rows), view=meal_card.NO_MEAL
    )


def reminder_view(db, user: User, send: ReminderSend, now: datetime) -> Screen:
    if is_reentry(send):
        return reentry_view(send)
    cfg = config(user)
    zone = settings_screen.user_zone(user)
    picks = pick_favorites(db, user, send.local_day, zone, before=send.sent_at)
    first = is_first(cfg, send)
    lines = [_title(send, now.astimezone(zone).date())]
    if first:
        lines.append(_FIRST)
        if picks:
            lines.append(_FIRST_STARS)
        label = settings_screen.zone_label_of(settings_screen.zone_name(user), now)
        lines.append(_FOOTNOTE.format(slot=send.slot, zone=label, sound=_sound(cfg)))
    else:
        lines.append(_LATER_STARS if picks else _LATER)
    rows = _chip_rows(send, picks)
    rows.append([_btn("✅ ОК", f"k:{send.id}"), _btn("✖ Выключить", f"x:{send.id}")])
    if first and not (user.settings or {}).get(settings_screen.TIMEZONE):
        rows.append([_btn(ZONE_LABEL, f"z:{send.id}")])  # its own: back here
    return Screen("\n".join(lines), InlineKeyboardMarkup(rows), view=meal_card.NO_MEAL)


def dated_title(send: ReminderSend) -> str:
    """What a reminder too old to delete keeps (R4): «🔔 Еда за пн 9 ноября»."""
    return f"🔔 <b>Еда за {stats_screen._card_title(send.local_day)}</b>"


def _title(send: ReminderSend, today: date) -> str:
    """«сегодня» only on its own day: a ⭐ under it logs onto ``local_day``, and a
    zone picked across midnight, or a tap the next morning, is another day by then."""
    return TITLE if send.local_day == today else dated_title(send)


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


def accepted_view() -> Screen:
    """R16: «✓ Принято.» and no buttons."""
    return Screen(f"{TITLE}\n{ACCEPTED}", None, view=meal_card.NO_MEAL)


def tap_view(db, user: User, send: ReminderSend, now: datetime) -> Screen:
    """The reminder after a ⭐: a line for each, ↩️ and 🕐 for the latest still
    logged, so undoing the newest leaves the one before it in reach (A3). One a
    «больше ничего» closed (R16) keeps those lines, ✅ ОК and ↩️ / 🕐, with
    «✓ Принято.» under them and no chips; with every ⭐ undone, it is R16's own."""
    zone = settings_screen.user_zone(user)
    logs = _logs(db, user, send)
    latest = next((meal for meal in reversed(logs) if meal.deleted_at is None), None)
    live = latest is not None
    accepted = said_nothing(send)
    if accepted and not live:
        return accepted_view()
    today = now.astimezone(zone).date()
    lines = [_title(send, today)] + [_logged_line(meal, zone, today) for meal in logs]
    lines.append(ACCEPTED if accepted else _MORE)
    rows = []
    if live:
        rows.append(ok_row())
        back = f"{meal_card.REMINDER_BACK}{send.id}"  # ⬅️ К напоминанию
        rows.append(
            [
                _btn("↩️ Отменить", f"u:{send.id}:{latest.id}"),
                InlineKeyboardButton(
                    "🕐 Время", callback_data=f"ml:t:{latest.id}:{back}"
                ),
            ]
        )
    if not accepted:
        picks = pick_favorites(db, user, send.local_day, zone, before=send.sent_at)
        rows += _chip_rows(send, picks)
    return Screen(
        "\n".join(lines),
        InlineKeyboardMarkup(rows),
        meal_id=latest.id if live else None,
        view=None if live else meal_card.NO_MEAL,
    )


def _redraw(t, send: ReminderSend, toast: str) -> Screen:
    if _logs(t.db, t.user, send) or said_nothing(send):
        return replace(tap_view(t.db, t.user, send, t.now), toast=toast)
    return replace(reminder_view(t.db, t.user, send, t.now), toast=toast)


# --- an answer in words (§3.2, R16) -------------------------------------------------


def says_now(text: str | None) -> bool:
    """«сейчас», «только что», «прямо сейчас»: food in front of the person now."""
    return bool(text) and _NOW_WORDS.search(text.casefold()) is not None


def is_nothing(text: str | None) -> bool:
    """«ничего», «почти ничего не ела», «сегодня не было»: no dish, only an answer."""
    if not text:
        return False
    return _NOTHING.fullmatch(text.strip().casefold().replace("ё", "е")) is not None


def window_end(send: ReminderSend) -> datetime:
    """30 minutes after the send, renewed by each bound answer, at most 90 in all."""
    sent = meal_items._utc(send.sent_at)
    answered = meal_items._utc(send.answered_at) if send.answered_at else sent
    return min(sent + RECALL_CAP, max(sent, answered) + RECALL_WINDOW)


def replied_send(
    db, user: User | None, chat_id, message_id, sent_at
) -> ReminderSend | None:
    """(a) The reminder a message replies to, within two local days of it."""
    if user is None or message_id is None:
        return None
    stmt = (
        select(ReminderSend)
        .where(
            ReminderSend.user_id == user.id,
            ReminderSend.chat_id == chat_id,
            ReminderSend.message_id == message_id,
        )
        .order_by(ReminderSend.id.desc())
        .limit(1)
    )
    send = db.scalars(stmt).first()
    if send is None or is_reentry(send):
        return None  # D6: a re-entry binds nothing, a reply to it is a capture
    zone = settings_screen.user_zone(user)
    day = meal_items._utc(sent_at).astimezone(zone).date()
    return send if (day - send.local_day).days <= LATE_DAYS else None


def _replied(db, user: User, came) -> ReminderSend | None:
    return replied_send(db, user, came.chat_id, came.reply_to, came.sent_at)


def _in_window(db, user: User, came) -> ReminderSend | None:
    """(b) The latest reminder, while it is live and its window is open. A
    re-entry opens none (D6), nor hides the one before it."""
    stmt = (
        select(ReminderSend)
        .where(
            ReminderSend.user_id == user.id,
            ReminderSend.status.in_(_LIVE),
            ReminderSend.kind != REENTRY,
        )
        .order_by(ReminderSend.sent_at.desc(), ReminderSend.id.desc())
        .limit(1)
    )
    send = db.scalars(stmt).first()
    if send is None or send.closed_at is not None:
        return None
    moment = meal_items._utc(came.sent_at)
    if not meal_items._utc(send.sent_at) <= moment < window_end(send):
        return None
    return send


def bound_send(db, user: User | None, came) -> ReminderSend | None:
    """The reminder a message from this person answers, if any (§3.2): an explicit
    reply to it, any kind, within two local days; or, but for a photo, one that came
    while its window was open. Never a forward, and never words of food eaten now."""
    if says_now(came.text):
        return None
    return answering(db, user, came)


def answering(db, user: User | None, came) -> ReminderSend | None:
    """The reminder a message came under, its words aside: «а ещё сейчас яблоко»
    is not bound, yet it is still said while the reminder is being answered."""
    if user is None or came.forward_date is not None:
        return None
    send = _replied(db, user, came)
    if send is not None:
        return send
    if came.kind in _PHOTOS:
        return None  # a photo is food in front of the person now
    return _in_window(db, user, came)


def recall_time(send: ReminderSend, came, zone) -> tuple[datetime, str]:
    """(meal_time, precision) of a bound meal with no time of its own: the
    reminder's day, no clock. A meal slot's centre, ≈, only for a reply to it: a
    guessed centre is never stamped on a message that merely came in its window."""
    replied = came.reply_to is not None and came.reply_to == send.message_id
    if send.kind != EVENING and send.centre and replied:
        hours, minutes = (int(part) for part in send.centre.split(":"))
        centre = datetime.combine(send.local_day, clock_time(hours, minutes), zone)
        return centre.astimezone(UTC), "approx"
    return meal_card.noon_of(send.local_day, zone).astimezone(UTC), DAY_ONLY


def _answered_by(db, user: User, send: ReminderSend, came) -> None:
    send.answered_at = meal_items._utc(came.sent_at)
    db.commit()
    if config(user).fragile:
        _write(db, user, fragile=None)  # an answer: back to the full count


def answered(db, user: User, send: ReminderSend, came) -> dict:
    """A message bound to ``send``: answered in words, and the window runs on from
    it. A ⭐ logged from it stays the answer: that message keeps its lines (R4).
    Returns what stood before, for ``take_back``."""
    prior = {
        "send_id": send.id,
        "answer": send.answer,
        "answered_at": (
            meal_items._utc(send.answered_at).isoformat() if send.answered_at else None
        ),
        "fragile": config(user).fragile,
    }
    # A ⭐ stays the answer; one ✖ turned off stays off, as accept_nothing has it.
    if not holds_star(send) and send.answer != "off":
        send.answer = "words"
    _answered_by(db, user, send, came)
    return prior


def take_back(db, user: User, prior: dict | None, sent_at) -> None:
    """A voice bound at birth whose words turned out to be of food eaten now: no
    answer after all (§3.2), so the ladder and the window are as they were. Only
    while it is still the latest answer: one that came after it stands."""
    send_id = (prior or {}).get("send_id")
    send = db.get(ReminderSend, send_id) if isinstance(send_id, int) else None
    if send is None or send.user_id != user.id or send.answered_at is None:
        return
    if meal_items._utc(send.answered_at) != meal_items._utc(sent_at):
        return
    answer = prior.get("answer")
    send.answer = answer if answer in (*_ANSWERS, *_NEUTRAL_ANSWERS) else None
    send.answered_at = _moment(prior.get("answered_at"))
    db.commit()
    if prior.get("fragile") is True and not config(user).fragile:
        _write(db, user, fragile=True)


def accept_nothing(
    db, user: User, send: ReminderSend, came
) -> meal_card.Retouch | None:
    """R16: answered and closed, with no read. The reminder's message becomes
    «✓ Принято.» with no buttons. A ⭐ logged from it keeps its lines, ✅ ОК and its
    ↩️ / 🕐 (the hanging-undo rule), with «✓ Принято.» under them and no chips
    (FAV_NONE). One turned off by ✖ stays as it is: its ↩️ Вернуть is the only way
    back, and nothing here says the reminders are on."""
    if send.answer == "off":
        return None
    send.answer = FAV_NONE if holds_star(send) else NONE
    if send.closed_at is None:
        send.closed_at = meal_items._utc(came.sent_at)
    _answered_by(db, user, send, came)
    if send.message_id is None or send.chat_id != came.chat_id:
        return None
    view = tap_view(db, user, send, meal_items._utc(came.sent_at))
    return meal_card.Retouch(send.message_id, view.text, view.markup)


# --- the sub-screen (§4.2) --------------------------------------------------------


def settings_view(
    db, user: User | None, now: datetime, notice: str | None = None
) -> Screen:
    """§4.2. Rarer or stopped, the time and the zone are still said, with why
    under them; the pause says only how to end it."""
    cfg = config(user)
    week = settings_screen.week_push_on(user)
    lines = [f"<i>{notice}</i>", ""] if notice else []
    lines += [_SETTINGS_TITLE, ""]
    if cfg.mode == OFF:
        lines.append("Сейчас: выключены.")
    elif paused(cfg):
        lines.append(PAUSED_LINE)
    else:
        label = settings_screen.zone_label_of(settings_screen.zone_name(user), now)
        lines.append(f"Сейчас: {phrase(cfg)} по времени {label}.")
        if user is not None and stopped(db, user, now):
            lines.append(STOPPED_LINE)
        elif rarer(cfg):
            lines.append(SPARSE_LINE)
    stars = user is not None and crud_favorite_meal.list_active(db, user.id)
    about = (
        f"Не больше одного сообщения в день {_sound(cfg)}: можно рассказать "
        "голосовым, что было"
        + (", или нажать на блюдо из ⭐" if stars else "")
        + ". Если были записи за три часа до него, оно не приходит."
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
    if is_reentry(send):
        return _unknown(t, data)  # it never shows a ⭐ (D6)
    zone = t.zone
    if (t.now.astimezone(zone).date() - send.local_day).days > LATE_DAYS:
        _moved(t, send)  # the message is ⭐ Избранное now: the next send leaves it
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
    # A stale chip after «ничего»: logged, and the «✓ Принято.» stays.
    send.answer = FAV_NONE if said_nothing(send) else FAV
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
    if holds_star(send) and all(
        log.deleted_at is not None for log in _logs(t.db, t.user, send)
    ):
        # Nothing logged from it any more, and the next send may delete it (R4).
        # Neutral, as ✅ ОК: the person did look at it. A «ничего» that closed it
        # stays its answer, and the message is R16's own.
        send.answer = NONE if send.answer == FAV_NONE else "ok"
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
    # The message is ✖'s screen now, and may be ⚙️'s: the next send leaves it (R4).
    if cfg.mode != OFF:
        # Tied to this message: its ↩️ only puts back what this ✖ took away.
        prev = {"mode": cfg.mode, "evening": cfg.evening, "send": send.id}
        _write(t.db, t.user, mode=OFF, prev=prev, moved=send.id)
    else:
        _write(t.db, t.user, moved=send.id)  # and the send's answer
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


def _moved(t, send: ReminderSend) -> None:
    """The message is another screen now: the next send leaves it as it is (R4)."""
    if config(t.user).moved != send.id:
        _write(t.db, t.user, moved=send.id)


def _back_home(t, send: ReminderSend) -> None:
    """The message is this reminder again: the next send deletes or strips it."""
    if config(t.user).moved == send.id:
        _write(t.db, t.user, moved=None)


def _superseded(t, send: ReminderSend) -> bool:
    """A newer reminder reached the chat: this one is no live reminder any more, and
    drawing its chips again would make two (R4)."""
    return crud_reminder_send.has_later(t.db, t.user.id, send.id)


def _v_view(t, data: str, sid: str) -> Screen:
    """⬅️ К напоминанию: the reminder as it was, its lines, chips and buttons. One a
    newer reminder came after is only its dated title: that one is the live one."""
    send = _own_send(t, data, sid)
    if _superseded(t, send):
        return Screen(dated_title(send), None, view=meal_card.NO_MEAL)
    _back_home(t, send)
    return _redraw(t, send, None)


def _zone_back(send: ReminderSend) -> list:
    return [_btn(meal_card.REMINDER_BACK_LABEL, f"v:{send.id}")]


def _v_zone(t, data: str, sid: str) -> Screen:
    """The first reminder's 🕐 Часовой пояс: answered as neutral and closed, as
    ✅ ОК, and the picker in its place; a pick comes back to the reminder."""
    send = _own_send(t, data, sid)
    if send.answer is None:
        send.answer = "ok"
    if send.closed_at is None:
        send.closed_at = t.now
    t.db.commit()
    _moved(t, send)
    screen = settings_screen.zones_screen(
        settings_screen.zone_name(t.user),
        t.now,
        pick=f"{CALLBACK_PREFIX}zp:{send.id}:",
        offsets=f"{CALLBACK_PREFIX}zo:{send.id}:",
        back=_zone_back(send),
    )
    return replace(screen, view=meal_card.NO_MEAL)


def _v_zone_page(t, data: str, sid: str, page: str) -> Screen:
    send = _own_send(t, data, sid)
    if int(page) >= settings_screen.pages():
        return _unknown(t, data)
    screen = settings_screen.offsets_screen(
        settings_screen.zone_name(t.user),
        t.now,
        int(page),
        pick=f"{CALLBACK_PREFIX}zp:{send.id}:",
        offsets=f"{CALLBACK_PREFIX}zo:{send.id}:",
        back=[_btn("⬅️ Часовой пояс", f"z:{send.id}")],
    )
    return replace(screen, view=meal_card.NO_MEAL)


def _v_zone_pick(t, data: str, sid: str, name: str) -> Screen:
    send = _own_send(t, data, sid)
    if not settings_screen.known_zone(name):
        return _unknown(t, data)
    settings_screen._save(t.db, t.telegram_id, **{settings_screen.TIMEZONE: name})
    t.db.refresh(t.user)
    notice = f"✓ Часовой пояс: {settings_screen.zone_label(name, t.now)}"
    if _superseded(t, send):
        text = f"<i>{notice}</i>\n\n{dated_title(send)}"
        return Screen(text, None, view=meal_card.NO_MEAL)
    _back_home(t, send)
    # The view reads the zone from the refreshed user (t.zone too): its «сегодня»
    # is the new zone's.
    screen = _redraw(t, send, None)
    return replace(screen, text=f"<i>{notice}</i>\n\n{screen.text}")


def _v_open(t, data: str) -> Screen:
    return settings_view(t.db, t.user, t.now)


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
        return settings_view(t.db, t.user, t.now, "✓ Напоминания выключены")
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
    return settings_view(t.db, t.user, t.now, notice)


def _v_evening(t, data: str, digits: str) -> Screen:
    at = f"{digits[:2]}:{digits[2:]}"
    if at not in EVENING_TIMES:
        return _unknown(t, data)
    if config(t.user).mode == OFF:
        # A stale hour button while off: the hour is kept, they stay off.
        _write(t.db, t.user, evening=at)
    else:
        _write(t.db, t.user, evening=at, since=t.now.isoformat(), prev=None)
    return settings_view(t.db, t.user, t.now, f"✓ Время: {at}")


def _v_week(t, data: str) -> Screen:
    on = not settings_screen.week_push_on(t.user)
    settings_screen._save(t.db, t.telegram_id, **{settings_screen.WEEK_PUSH: on})
    t.db.refresh(t.user)
    notice = "✓ Итог недели включён" if on else "✓ Итог недели выключен"
    return settings_view(t.db, t.user, t.now, notice)


def _v_sound(t, data: str) -> Screen:
    on = not config(t.user).sound
    _write(t.db, t.user, sound=on)
    return settings_view(t.db, t.user, t.now, "✓ Со звуком" if on else "✓ Без звука")


_N = r"(\d{1,12})"
_VERBS = [
    (re.compile(rf"rm:f:{_N}:{_N}"), _v_fav),
    (re.compile(rf"rm:u:{_N}:{_N}"), _v_undo),
    (re.compile(rf"rm:k:{_N}"), _v_ok),
    (re.compile(rf"rm:x:{_N}"), _v_off),
    (re.compile(rf"rm:b:{_N}"), _v_back),
    (re.compile(rf"rm:v:{_N}"), _v_view),
    (re.compile(rf"rm:z:{_N}"), _v_zone),
    (re.compile(rf"rm:zo:{_N}:(\d{{1,2}})"), _v_zone_page),
    (re.compile(rf"rm:zp:{_N}:([A-Za-z0-9_+/-]{{1,40}})"), _v_zone_pick),
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
