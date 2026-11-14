"""The free words of sections 5.3 and 5.4: time, and corrections. No model, no rows.

Word boundaries all the way: «вчерашний суп» stays food, and «плюсик» stays a word. All
the clocks are ASSUMED, as in the spec.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Optional

# --- 5.3 time words -------------------------------------------------------------------

EXACT, APPROX, DAY_ONLY = "exact", "approx", "day_only"

_PARTS = {"утром": time(9), "днем": time(14), "вечером": time(20)}
_NOON = time(12)
_NIGHT = time(3)
_FLAGS = re.IGNORECASE

_CLOCK = re.compile(
    r"(?<![\w:.,])(?:(вчера)\s+)?(?:в\s+)?(\d{1,2}):(\d{2})(?![\d:])", _FLAGS
)
_YESTERDAY_PART = re.compile(r"\bвчера\s+(утром|дн[её]м|вечером)\b", _FLAGS)
# «в 13» only at either end of the text: in the middle a number is more likely food's,
# and a unit after it always is: «в 3 раза больше», «в 2 тарелки».
_NOT_HOUR = r"раз|г\b|гр|кг|мл|л\b|шт|тарел|порц|кус|лож|стак|чаш|упак|пач|бан|бут|ломт"
_HOUR_FIRST = re.compile(
    rf"^\s*(?:(вчера)\s+)?в\s+(\d{{1,2}})(?:\s+час(?:а|ов)?)?\b(?!\s+(?:{_NOT_HOUR}))",
    _FLAGS,
)
_HOUR_LAST = re.compile(
    r"\b(?:(вчера)\s+)?в\s+(\d{1,2})(?:\s+час(?:а|ов)?)?\s*$", _FLAGS
)
_AGO = re.compile(r"\b(полчаса|час)\s+назад\b", _FLAGS)
_YESTERDAY = re.compile(r"\bвчера\b", _FLAGS)
_NIGHT_WORD = re.compile(r"\bночью\b", _FLAGS)
_PART = re.compile(r"\b(утром|дн[её]м|вечером)\b", _FLAGS)
_TODAY = re.compile(r"\bсегодня\b", _FLAGS)
# A meal's name said as when: «в обед суп». It gives no clock: on its own the meal
# keeps its moment, as before R0. Only R0's rule counts it as a time a clause says, at
# these clocks (ASSUMED, «на ужин» as «вечером»): «утром овсянка, в обед суп» is two
# meals of one day, and the record keeps the day alone.
_MEAL_NAME_CLOCKS = {
    "завтрак": time(9),
    "обед": time(13),
    "полдник": time(16),
    "ужин": time(20),
}
_MEAL_NAME = re.compile(
    r"\b(?:(?:в|на)\s+(обед)|за\s+(обед)ом|на\s+(завтрак)|за\s+(завтрак)ом"
    r"|на\s+(ужин)|за\s+(ужин)ом|на\s+(полдник))\b",
    _FLAGS,
)
# R0: what parts one clause from the next, «утром каша, в 19:30 паста»: a comma, a
# semicolon, a dash, and a standalone «и» or «а» between words.
_CLAUSE_EDGE = re.compile(r"[,;—]|(?<!\S)[иа](?!\S)", _FLAGS)


@dataclass(frozen=True)
class SaidTime:
    moment: datetime  # in UTC
    precision: str
    phrase: Optional[str]  # None when clauses said different times (R0)
    rest: str  # the text without the phrase
    of_day: bool = False  # a clock of a day that the words did not name


@dataclass(frozen=True)
class _Found:
    match: re.Match
    moment: datetime
    precision: str
    of_day: bool = False


def _plain(word: str) -> str:
    return word.casefold().replace("ё", "е")


def _at(day, clock: time, zone) -> datetime:
    return datetime.combine(day, clock, tzinfo=zone)


def _latest(local: datetime, clock: time, zone) -> datetime:
    """That clock today, or yesterday if it is still ahead."""
    moment = _at(local.date(), clock, zone)
    return (
        moment
        if moment <= local
        else _at(local.date() - timedelta(days=1), clock, zone)
    )


def _clock_of(match, hour: int, minute: int, local: datetime, zone) -> Optional[_Found]:
    if hour > 23 or minute > 59:
        return None
    clock = time(hour, minute)
    if match.group(1):
        day = local.date() - timedelta(days=1)
        return _Found(match, _at(day, clock, zone), EXACT)
    return _Found(match, _latest(local, clock, zone), EXACT, of_day=True)


def _find_clock(text: str, local: datetime, zone) -> Optional[_Found]:
    match = _CLOCK.search(text)
    if match is None:
        return None
    return _clock_of(match, int(match.group(2)), int(match.group(3)), local, zone)


def _find_yesterday_part(text: str, local: datetime, zone) -> Optional[_Found]:
    match = _YESTERDAY_PART.search(text)
    if match is None:
        return None
    day = local.date() - timedelta(days=1)
    return _Found(match, _at(day, _PARTS[_plain(match.group(1))], zone), APPROX)


def _find_hour(text: str, local: datetime, zone) -> Optional[_Found]:
    match = _HOUR_FIRST.search(text) or _HOUR_LAST.search(text)
    if match is None:
        return None
    return _clock_of(match, int(match.group(2)), 0, local, zone)


def _find_ago(text: str, local: datetime, zone) -> Optional[_Found]:
    match = _AGO.search(text)
    if match is None:
        return None
    half = _plain(match.group(1)) == "полчаса"
    return _Found(match, local - timedelta(minutes=30 if half else 60), APPROX)


def _find_yesterday(text: str, local: datetime, zone) -> Optional[_Found]:
    match = _YESTERDAY.search(text)
    if match is None:
        return None
    return _Found(match, _at(local.date() - timedelta(days=1), _NOON, zone), DAY_ONLY)


def _find_night(text: str, local: datetime, zone) -> Optional[_Found]:
    match = _NIGHT_WORD.search(text)
    if match is None:
        return None
    return _Found(match, _latest(local, _NIGHT, zone), APPROX)


def _find_part(text: str, local: datetime, zone) -> Optional[_Found]:
    match = _PART.search(text)
    if match is None:
        return None
    clock = _PARTS[_plain(match.group(1))]
    return _Found(match, _latest(local, clock, zone), APPROX, of_day=True)


_FINDERS = (
    _find_clock,
    _find_yesterday_part,
    _find_hour,
    _find_ago,
    _find_yesterday,
    _find_night,
    _find_part,
)


def _without(text: str, match: re.Match) -> str:
    left = f"{text[: match.start()]} {text[match.end() :]}"
    return " ".join(left.split()).strip(" ,.;:—-")


def _first_phrase(
    text: str, local: datetime, zone, bare_ok: bool
) -> Optional[SaidTime]:
    """The first phrase of the table. None if there is none, or, unless ``bare_ok``,
    if nothing else is there: a time word alone is nobody's dish, and nobody's time."""
    for find in _FINDERS:
        found = find(text, local, zone)
        if found is None:
            continue
        rest = _without(text, found.match)
        if not rest and not bare_ok:
            return None
        return SaidTime(
            found.moment.astimezone(UTC),
            found.precision,
            found.match.group(0).strip(),
            rest,
            found.of_day,
        )
    return None


def clauses(text: str) -> list:
    """The text cut at every clause edge. Some may be empty."""
    return _CLAUSE_EDGE.split(text)


def _meal_name(text: str, local: datetime, zone) -> Optional[datetime]:
    """The moment R0's rule counts for a meal's name in a clause, else None."""
    match = _MEAL_NAME.search(text)
    if match is None:
        return None
    name = _plain(next(word for word in match.groups() if word))
    return _latest(local, _MEAL_NAME_CLOCKS[name], zone).astimezone(UTC)


def _common_day(moments: list, text: str, local: datetime):
    """The day several said moments share; else yesterday, if «вчера» is said and
    «сегодня» is not; else the day it was sent."""
    days = {moment.astimezone(local.tzinfo).date() for moment in moments}
    if len(days) == 1:
        return days.pop()
    if _YESTERDAY.search(text) and not _TODAY.search(text):
        return local.date() - timedelta(days=1)
    return local.date()


def said_moments(text: Optional[str], sent: datetime, zone) -> list:
    """The moment each clause says, in order, as R0 counts them: a time phrase, else a
    meal's name. Clauses that say none are left out."""
    if not text:
        return []
    local = sent.astimezone(zone)
    moments = []
    for one in clauses(text):
        said = _first_phrase(one, local, zone, True)
        moment = said.moment if said else _meal_name(one, local, zone)
        if moment is not None:
            moments.append(moment)
    return moments


def says_several(text: Optional[str], sent: datetime, zone) -> bool:
    """R0's own condition: two or more clauses say a time, and the times differ."""
    moments = said_moments(text, sent, zone)
    return len(moments) >= 2 and len(set(moments)) > 1


def _part_clock(span: str, local: datetime, zone):
    """(clock, precision, phrase, rest, moment) of one part's own words. ``moment`` is
    set when the words fix it whole («час назад», «вчера в 9:00»); ``clock`` is None
    when they say no time of day."""
    said = _first_phrase(span, local, zone, True)
    rest = said.rest if said else span
    if said is not None and said.precision != DAY_ONLY:
        clock = said.moment.astimezone(zone).time()
        moment = None if said.of_day else said.moment
        return clock, said.precision, said.phrase, rest, moment
    match = _MEAL_NAME.search(rest)
    if match is None:
        return None, None, said.phrase if said else None, rest, None
    name = _plain(next(word for word in match.groups() if word))
    phrase = match.group(0).strip()
    return _MEAL_NAME_CLOCKS[name], APPROX, phrase, _without(rest, match), None


def _bare_days(text: str) -> str:
    left = _TODAY.sub(" ", _YESTERDAY.sub(" ", text))
    return " ".join(left.split()).strip(" ,.;:—-")


# A meal's name or a part of the day is a guess at the clock: lunch said at 12:30, or
# dinner at 19:30, is today's, with its clock not yet passed. Only a guess this far
# ahead says the log is yesterday's («…, вечером сырники» at 14:00).
_APPROX_SLACK = timedelta(hours=3)


def _ahead(clock, precision: str, local: datetime) -> bool:
    at = datetime.combine(local.date(), clock)
    now = local.replace(tzinfo=None)
    return at > (now + _APPROX_SLACK if precision == APPROX else now)


def part_times(spans: list, sent: datetime, zone, reminder_day=None) -> list:
    """A ``SaidTime`` for each part of one message (several meals), in order.

    The day: the reminder's day if the message answers one; else «вчера» or «сегодня»
    as said; else the latest day on which every part's clock has passed. The clock: the
    part's own time phrase, else its meal's name, approximate. A part with neither, or
    a clock still ahead on a day named outright, keeps the day alone. ``rest`` is the
    part's words without its time words."""
    local = sent.astimezone(zone)
    whole = " ".join(spans)
    found = [_part_clock(span, local, zone) for span in spans]
    named = None
    if reminder_day is not None:
        named = reminder_day
    elif _YESTERDAY.search(whole) and not _TODAY.search(whole):
        named = local.date() - timedelta(days=1)
    elif _TODAY.search(whole):
        named = local.date()
    if named is not None:
        day = named
    else:
        ahead = any(
            _ahead(clock, precision, local)
            for clock, precision, _, _, moment in found
            if clock is not None and moment is None
        )
        day = local.date() - timedelta(days=1) if ahead else local.date()
    out = []
    for clock, precision, phrase, rest, moment in found:
        rest = _bare_days(rest)
        if moment is not None:
            out.append(SaidTime(moment.astimezone(UTC), precision, phrase, rest))
            continue
        at = _at(day, clock, zone) if clock is not None else None
        if at is None or at > local:
            noon = _at(day, _NOON, zone).astimezone(UTC)
            out.append(SaidTime(noon, DAY_ONLY, phrase, rest, of_day=True))
            continue
        out.append(SaidTime(at.astimezone(UTC), precision, phrase, rest, of_day=True))
    return out


def time_words(
    text: Optional[str], sent: datetime, zone, *, bare_ok: bool = False
) -> Optional[SaidTime]:
    """The time the words say, as ``_first_phrase`` reads the whole text, with one
    rule over it (R0): when two or more clauses each say a time and those times
    differ, no phrase is the meal's. Then it is the day alone, and no clock: «утром
    каша, в 19:30 паста» is two meals, and neither time is the record's. In this rule
    alone a clause with no time phrase but a meal's name says that meal's time: «утром
    овсянка, в обед суп»."""
    if not text:
        return None
    local = sent.astimezone(zone)
    moments = said_moments(text, sent, zone)
    if len(moments) >= 2 and len(set(moments)) > 1:
        day = _common_day(moments, text, local)
        noon = _at(day, _NOON, zone).astimezone(UTC)
        return SaidTime(noon, DAY_ONLY, None, text, of_day=True)
    return _first_phrase(text, local, zone, bare_ok)


# --- 5.4 corrective words -------------------------------------------------------------

ADD, REPEAT, CHANGE, REMOVE = "add", "repeat", "replace", "remove"

_WORDS = re.IGNORECASE | re.DOTALL
_ADD_WORDS = re.compile(
    r"(?:(?:[иа]\s+)?ещ[её]|плюс|добавь)\s+(.+)"
    r"|(?:я\s+)?забыл[аио]?\b"
    r"(?:\s+(?:добавить|написать|сказать|указать|про))*[\s,:]+(.+)",
    _WORDS,
)
# On the plain text, and in their inflections too: «ещё порцию», «добавку».
_SECOND_HELPING = re.compile(
    r"еще\s+порци[яю]|добавк[ау]|втор(?:ая|ую)\s+порци[яю]"
    r"|еще\s+(?:один|одна|одну|одно)"
)
# Words that hold no dish and only answer: «нет, спасибо» is no «нет, индейка».
STOP_WORDS = frozenset(("привет", "спасибо", "ок", "окей", "да", "нет", "пока"))
_IT_WAS = re.compile(r"(?:это\s+был[аои]?\s+|нет,\s*)(.+)", _WORDS)
_SAYS_NO = re.compile(r"нет,\s*(.+)", _WORDS)
_NOT_BUT = re.compile(r"не\s+(.+?),\s*а\s+(.+)", _WORDS)
_REMOVE_WORDS = re.compile(r"(?:убери|убрать)\s+(.+)", _WORDS)
_NAME_EDGE = " .,!;:\n"


def _no_dish(rest: str) -> bool:
    """What follows «нет,»: a thanks, or a denial, names nothing to write."""
    rest = rest.strip(_NAME_EDGE + "…)(").casefold()
    return rest in STOP_WORDS or rest.startswith("не ")


def says_no(text: str) -> bool:
    """«нет, спасибо», «нет, не ел»: an answer, and no food in it."""
    match = _SAYS_NO.fullmatch(text.strip())
    return match is not None and _no_dish(match.group(1))


def correction(text: str) -> Optional[tuple]:
    """(what, name, old name) from the start of the text. A question corrects nothing,
    and neither do words with no food left in them."""
    body = text.strip()
    if body.endswith("?"):
        return None
    if _SECOND_HELPING.fullmatch(_plain(body).rstrip(".! ")):
        return REPEAT, None, None
    match = _NOT_BUT.fullmatch(body)
    if match:
        found = CHANGE, match.group(2), match.group(1).strip()
    elif match := _IT_WAS.fullmatch(body):
        if says_no(body):
            return None
        found = CHANGE, match.group(1), None
    elif match := _REMOVE_WORDS.fullmatch(body):
        found = REMOVE, match.group(1), None
    elif match := _ADD_WORDS.fullmatch(body):
        found = ADD, match.group(1) or match.group(2), None
    else:
        return None
    name = found[1].strip(_NAME_EDGE)
    return (found[0], name, found[2]) if name else None


def _stem(word: str) -> str:
    word = _plain(word)
    return word[: max(3, len(word) - 2)]


def matching(items: list, phrase: str) -> list:
    """The dishes all of whose named words are there, up to a Russian ending."""
    stems = [_stem(word) for word in phrase.split()]
    found = []
    for item in items:
        words = [_plain(word) for word in item.name.split()]
        if stems and all(any(w.startswith(s) for w in words) for s in stems):
            found.append(item)
    return found
