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


@dataclass(frozen=True)
class SaidTime:
    moment: datetime  # in UTC
    precision: str
    phrase: str
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


def time_words(
    text: Optional[str], sent: datetime, zone, *, bare_ok: bool = False
) -> Optional[SaidTime]:
    """The first phrase of the table. None if there is none, or, unless ``bare_ok``,
    if nothing else is there: a time word alone is nobody's dish, and nobody's time."""
    if not text:
        return None
    local = sent.astimezone(zone)
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
_IT_WAS = re.compile(r"(?:это\s+был[аои]?\s+|нет,\s*)(.+)", _WORDS)
_NOT_BUT = re.compile(r"не\s+(.+?),\s*а\s+(.+)", _WORDS)
_REMOVE_WORDS = re.compile(r"(?:убери|убрать)\s+(.+)", _WORDS)
_NAME_EDGE = " .,!;:\n"


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
