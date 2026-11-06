"""The free words of sections 5.3 and 5.4: time, and corrections. No model, no rows.

Word boundaries all the way: «вчерашний суп» stays food, and «плюсик» stays a word. All
the clocks are ASSUMED, as in the spec.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta
from typing import Optional

# --- 5.3 time words -------------------------------------------------------------------

EXACT, APPROX, DAY_ONLY = "exact", "approx", "day_only"

_PARTS = {"утром": time(9), "днем": time(14), "вечером": time(20)}
# A meal's name said as its time: «в обед суп». ASSUMED, like the parts.
_MEAL_NAMES = {
    "на завтрак": time(9),
    "за завтраком": time(9),
    "в обед": time(13),
    "на обед": time(13),
    "за обедом": time(13),
    "на полдник": time(16),
    "на ужин": time(19),
    "за ужином": time(19),
}
# The hours a part of the day covers, as day_card._part_of_day (lunch inside the day):
# a clock said with its own part is that clock, «утром в 8:30».
_WINDOWS = {"утром": (4, 11), "днем": (11, 18), "вечером": (18, 24)}
_MEAL_PARTS = {"завтрак": "утром", "обед": "днем", "полдник": "днем", "ужин": "вечером"}
# A part or a meal still ahead by no more than this is the one going on now: «на
# завтрак каша» at 8:30 is this breakfast, not yesterday's. ASSUMED.
_SOON = timedelta(hours=2)
_NOON = time(12)
_NIGHT = time(3)
_FLAGS = re.IGNORECASE

_CLOCK = re.compile(
    r"(?<![\w:.,])(?:(вчера)\s+)?(?:в\s+)?(\d{1,2}):(\d{2})(?![\d:])", _FLAGS
)
_MEAL_NAME = "|".join(name.replace(" ", r"\s+") for name in _MEAL_NAMES)
_YESTERDAY_PART = re.compile(
    rf"\bвчера\s+(утром|дн[её]м|вечером|{_MEAL_NAME})\b", _FLAGS
)
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
_TODAY = re.compile(r"\bсегодня\b", _FLAGS)
# What parts one clause from the next: «в обед суп, в 15:00 чай» is two meals.
_CLAUSE_EDGE = re.compile(r"[,;.!?—–\n]|\b(?:и|а|потом|затем)\b", _FLAGS)
_NIGHT_WORD = re.compile(r"\bночью\b", _FLAGS)
_PART = re.compile(r"\b(утром|дн[её]м|вечером)\b", _FLAGS)
_MEAL = re.compile(rf"\b({_MEAL_NAME})\b", _FLAGS)


@dataclass(frozen=True)
class SaidTime:
    moment: datetime  # in UTC
    precision: str
    phrase: Optional[str]  # None when several phrases left only the day
    rest: str  # the text without the phrase
    of_day: bool = False  # a clock of a day that the words did not name
    clock: Optional[time] = None  # what an of_day time keeps on another day


@dataclass(frozen=True)
class _Found:
    match: re.Match
    moment: datetime  # local
    precision: str
    of_day: bool = False
    clock: Optional[time] = None  # what an of_day phrase keeps on another day
    window: Optional[tuple] = None  # the hours of a part or a meal's name
    now: bool = False  # a part or a meal going on as it was sent

    @property
    def phrase(self) -> str:
        return self.match.group(0).strip()

    @property
    def yesterday(self) -> bool:
        return _YESTERDAY.search(self.phrase) is not None


def _plain(word: str) -> str:
    return word.casefold().replace("ё", "е")


def _clock_word(word: str) -> time:
    """The clock of a part of the day or of a meal's name, however spaced."""
    plain = " ".join(_plain(word).split())
    return _PARTS.get(plain) or _MEAL_NAMES[plain]


def _window(word: str) -> tuple:
    """The hours of a part of the day or of a meal's name."""
    plain = _plain(word)
    for meal, part in _MEAL_PARTS.items():
        if meal in plain:
            return _WINDOWS[part]
    return _WINDOWS[" ".join(plain.split())]


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


def _named_clock(match, word: str, local: datetime, zone) -> _Found:
    """A part's or a meal's clock on its latest day. One still ahead by no more than
    ``_SOON`` is going on now, and has the sent moment."""
    clock = _clock_word(word)
    today = _at(local.date(), clock, zone)
    now = local < today <= local + _SOON
    moment = local if now else _latest(local, clock, zone)
    return _Found(match, moment, APPROX, True, clock, _window(word), now)


def _clock_of(match, hour: int, minute: int, local: datetime, zone) -> Optional[_Found]:
    if hour > 23 or minute > 59:
        return None
    clock = time(hour, minute)
    if match.group(1):
        day = local.date() - timedelta(days=1)
        return _Found(match, _at(day, clock, zone), EXACT)
    return _Found(match, _latest(local, clock, zone), EXACT, of_day=True, clock=clock)


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
    word = match.group(1)
    return _Found(
        match, _at(day, _clock_word(word), zone), APPROX, window=_window(word)
    )


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
    return None if match is None else _named_clock(match, match.group(1), local, zone)


def _find_meal(text: str, local: datetime, zone) -> Optional[_Found]:
    match = _MEAL.search(text)
    return None if match is None else _named_clock(match, match.group(1), local, zone)


_FINDERS = (
    _find_clock,
    _find_yesterday_part,
    _find_hour,
    _find_ago,
    _find_yesterday,
    _find_night,
    _find_part,
    _find_meal,
)
# What a found phrase leaves in its place. Not a space: «в 13» counts only at either
# end of the text, and a phrase found before it must not make an end of the middle.
_MASK = "\x00"


def _without(text: str, found: list) -> str:
    """The text with every one of these phrases cut."""
    for one in sorted(found, key=lambda one: one.match.start(), reverse=True):
        text = f"{text[: one.match.start()]} {text[one.match.end() :]}"
    return " ".join(text.split()).strip(" ,.;:—-")


def _found(text: str, local: datetime, zone) -> list:
    """Every phrase, each finder in the table's order, each run until it finds no
    more; a found span is masked, so no phrase is found twice. In finder order."""
    found = []
    for find in _FINDERS:
        while (one := find(text, local, zone)) is not None:
            found.append(one)
            start, end = one.match.span()
            text = f"{text[:start]}{_MASK * (end - start)}{text[end:]}"
    return found


def time_phrases(text: Optional[str], sent: datetime, zone) -> list:
    """All the time phrases of a text, in text order, none overlapping another."""
    if not text:
        return []
    found = _found(text, sent.astimezone(zone), zone)
    return sorted(found, key=lambda one: one.match.start())


def _between(one: _Found, other: _Found, text: str) -> str:
    """The words between two phrases, whichever comes first."""
    first, second = sorted((one, other), key=lambda each: each.match.start())
    return text[first.match.end() : second.match.start()]


def _next_to(one: _Found, other: _Found, text: str) -> bool:
    """Nothing but a space or a comma between: «утром, в 12:00 каша»."""
    return re.fullmatch(r"[\s,]*", _between(one, other, text)) is not None


def _one_clause(one: _Found, other: _Found, text: str) -> bool:
    """No comma, dash or «и»/«а» between: «на обед суп в 13:30» is one clause, «в обед
    суп, в 15:00 чай» two."""
    return _CLAUSE_EDGE.search(_between(one, other, text)) is None


def _beside(part: _Found, clock: _Found, text: str) -> bool:
    """A part or a meal's name said with a clock: right next to it, «утром в 8:30»,
    «в 8:30 утром», or in its clause and of its hours, «на обед суп в 13:30». A clock
    in another clause is another meal's: «в обед суп, в 15:00 чай»."""
    if _next_to(part, clock, text):
        return True
    low, high = part.window
    hour = (clock.clock or clock.moment.time()).hour
    return _one_clause(part, clock, text) and low <= hour < high


def _tied(one: _Found, other: _Found, text: str) -> bool:
    """Two phrases about one meal: in one clause, and no «сегодня» between."""
    if _TODAY.search(_between(one, other, text)):
        return False
    return _next_to(one, other, text) or _one_clause(one, other, text)


def _one_time(found: list, text: str, local: datetime, zone) -> list:
    """The phrases left once those that say one time are one. «вчера» puts a clock of
    no named day on yesterday only when the two are about one meal: «вчера суп в
    13:00», not «вчера суп, утром каша»; a clock outranks its own part or meal's name;
    a day alone adds nothing to a time on that day that it is said with."""
    named = [one for one in found if one.yesterday]
    day = local.date() - timedelta(days=1)
    found = [
        (
            replace(one, moment=_at(day, one.clock, zone), of_day=False, now=False)
            if one.of_day and any(_tied(one, said, text) for said in named)
            else one
        )
        for one in found
    ]
    clocks = [one for one in found if one.precision == EXACT]
    found = [
        one
        for one in found
        if not (one.window and any(_beside(one, clock, text) for clock in clocks))
    ]
    timed = [one for one in found if one.precision != DAY_ONLY]
    return [
        one
        for one in found
        if not (
            one.precision == DAY_ONLY
            and any(
                said.moment.date() == one.moment.date()
                and (said.yesterday or _tied(said, one, text))
                for said in timed
            )
        )
    ]


def _day_of(one: _Found, named: list, text: str):
    """The day a phrase speaks of. A clock of no named day said after «вчера», with no
    «сегодня» between, is of yesterday too: «вчера утром каша, вечером суп». One said
    before it keeps its own: «на завтрак каша, вчера в 22:00 торт»."""
    if one.of_day:
        for said in named:
            if said.match.start() < one.match.start() and not _TODAY.search(
                _between(said, one, text)
            ):
                return said.moment.date()
    return one.moment.date()


def _common_day(kept: list, found: list, text: str, local: datetime):
    """The day phrases that disagree on the clock still agree on: the one all of them
    speak of, else the day it was sent."""
    named = [one for one in found if one.yesterday]
    days = {_day_of(one, named, text) for one in kept}
    return days.pop() if len(days) == 1 else local.date()


_RANK = {EXACT: 0, APPROX: 1, DAY_ONLY: 2}


def time_words(
    text: Optional[str], sent: datetime, zone, *, bare_ok: bool = False
) -> Optional[SaidTime]:
    """The time the phrases say. None if there is none, or, unless ``bare_ok``, if
    nothing else is there: a time word alone is nobody's dish, and nobody's time. A
    part or a meal going on as it was sent is no time either, when food is said with
    it: the sent moment stays. Said alone, it keeps its own ``clock`` too, for a reply
    that puts it on another day («вечером» at 18:30 to yesterday's meal is ≈ 20:00).
    Phrases that say one time are one («утром в 8:30»);
    those that still name different moments give their day and no clock: «утром каша,
    в 19:30 паста» is two meals, and neither time is the record's."""
    if not text:
        return None
    local = sent.astimezone(zone)
    found = _found(text, local, zone)
    if not found:
        return None
    kept = _one_time(found, text, local, zone)
    if len({one.moment for one in kept}) > 1:
        day = _common_day(kept, found, text, local)
        named = day != local.date() and any(one.yesterday for one in found)
        return SaidTime(
            _at(day, _NOON, zone).astimezone(UTC), DAY_ONLY, None, text, not named
        )
    best = min(kept, key=lambda one: _RANK[one.precision])
    rest = _without(text, found)
    if rest and best.now:
        return None
    if not rest and not bare_ok:
        return None
    return SaidTime(
        best.moment.astimezone(UTC),
        best.precision,
        best.phrase,
        rest,
        best.of_day,
        best.clock if best.of_day else None,
    )


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
