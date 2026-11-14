"""The planner's command lines: one registry for the prompt's docs and the parser.

The model answers in lines such as ``meal "на завтрак омлет"`` or ``star #3 "суп"``.
Anything that is not a command line (prose, bullets' marks, code fences) is skipped; a
command line that does not parse is refused with a reason. No model, no rows here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

MEALS_MAX = 8
DISH_MAX = 10

MEAL, MERGE, STAR, UNSTAR, HIDE, RENAME, NONE = (
    "meal",
    "merge",
    "star",
    "unstar",
    "hide",
    "rename",
    "none",
)
DISH_VERBS = (MERGE, STAR, UNSTAR, HIDE, RENAME)


@dataclass(frozen=True)
class Verb:
    name: str
    syntax: str
    doc: str
    example: str


VERBS = (
    Verb(
        MEAL,
        'meal "<part of the message>"',
        "one meal eaten at its own time. Copy the whole part of the message about "
        "it, time words included. Foods eaten together are one meal.",
        'meal "на завтрак омлет с сыром"',
    ),
    Verb(
        MERGE,
        'merge #n "<name>" #n "<name>" [into #n "<name>"]',
        "the person says these dishes are one dish. `into` names the one to keep, "
        "only if the person said which.",
        'merge #4 "котлета куриная" #9 "куриная котлета"',
    ),
    Verb(
        STAR, 'star #n "<name>"', "add the dish to favourites (⭐).", 'star #2 "борщ"'
    ),
    Verb(
        UNSTAR,
        'unstar #n "<name>"',
        "take the dish out of favourites.",
        'unstar #2 "борщ"',
    ),
    Verb(
        HIDE,
        'hide #n "<name>"',
        "remove the dish from the person's dish list.",
        'hide #7 "пицца"',
    ),
    Verb(
        RENAME,
        'rename #n "<name>" to "<new name>"',
        "rename the dish. The new name is copied from the message.",
        'rename #5 "салат" to "салат греческий"',
    ),
    Verb(NONE, "none", "nothing above applies.", "none"),
)
_BY_NAME = {verb.name: verb for verb in VERBS}


def docs() -> str:
    """The commands as the prompt states them, from the same registry as the parser."""
    lines = []
    for verb in VERBS:
        lines.append(f"{verb.syntax}\n    {verb.doc}\n    e.g. {verb.example}")
    return "\n".join(lines)


@dataclass(frozen=True)
class Ref:
    number: Optional[int]
    name: str


@dataclass(frozen=True)
class Command:
    verb: str
    line: str
    refs: tuple = ()
    into: Optional[Ref] = None
    text: Optional[str] = None  # meal: the part; rename: the new name


@dataclass(frozen=True)
class Bad:
    line: str
    reason: str


@dataclass
class Parsed:
    commands: list = field(default_factory=list)
    bad: list = field(default_factory=list)
    none: bool = False
    over_meals: bool = False
    over_dish: bool = False

    @property
    def meals(self) -> list:
        return [c for c in self.commands if c.verb == MEAL]

    @property
    def dish(self) -> list:
        return [c for c in self.commands if c.verb in DISH_VERBS]


MALFORMED = "malformed"
_BULLET = re.compile(r"^(?:[-*•]\s+|\d{1,2}[.)]\s+)")
_TOKEN = re.compile(r'#(\d{1,4})|"([^"]*)"|«([^»]*)»|“([^”]*)”|„([^“”]*)[“”]|(\S+)')


def _tokens(line: str) -> list:
    """("#", n) | ("q", text) | ("w", word)."""
    out = []
    for match in _TOKEN.finditer(line):
        number, *quoted, word = match.groups()
        if number is not None:
            out.append(("#", int(number)))
        elif word is not None:
            out.append(("w", word))
        else:
            out.append(("q", next(q for q in quoted if q is not None)))
    return out


def _ref(tokens: list, at: int) -> tuple:
    """(Ref, next index), or (None, at) when no ref starts here."""
    number = None
    if at < len(tokens) and tokens[at][0] == "#":
        number, at = tokens[at][1], at + 1
    if at < len(tokens) and tokens[at][0] == "q" and tokens[at][1].strip():
        return Ref(number, tokens[at][1].strip()), at + 1
    return None, at


def _command(verb: str, line: str, tokens: list) -> Optional[Command]:
    """None: the arguments do not parse."""
    rest = tokens[1:]
    if verb == NONE:
        return Command(NONE, line) if not rest else None
    if verb == MEAL:
        if len(rest) == 1 and rest[0][0] == "q" and rest[0][1].strip():
            return Command(MEAL, line, text=rest[0][1].strip())
        return None
    if verb in (STAR, UNSTAR, HIDE):
        ref, at = _ref(rest, 0)
        return Command(verb, line, refs=(ref,)) if ref and at == len(rest) else None
    if verb == RENAME:
        ref, at = _ref(rest, 0)
        if ref is None or at + 2 != len(rest):
            return None
        if rest[at] != ("w", "to") or rest[at + 1][0] != "q":
            return None
        new = rest[at + 1][1].strip()
        return Command(RENAME, line, refs=(ref,), text=new) if new else None
    refs, into, at = [], None, 0
    while at < len(rest):
        if rest[at] == ("w", "into"):
            into, at = _ref(rest, at + 1)
            if into is None or at != len(rest):
                return None
            break
        ref, after = _ref(rest, at)
        if ref is None:
            return None
        refs.append(ref)
        at = after
    if len(refs) + (into is not None) < 2:
        return None
    return Command(MERGE, line, refs=tuple(refs), into=into)


def parse(answer: Optional[str]) -> Parsed:
    """The command lines of an answer, capped: past ``MEALS_MAX`` meals or
    ``DISH_MAX`` dish changes the rest are left out and the flag says so."""
    parsed = Parsed()
    for raw in (answer or "").splitlines():
        line = _BULLET.sub("", raw.strip()).strip()
        if not line or line.startswith("```"):
            continue
        tokens = _tokens(line)
        if not tokens or tokens[0][0] != "w":
            continue
        verb = tokens[0][1].casefold().rstrip(":")
        if verb not in _BY_NAME:
            continue  # prose
        command = _command(verb, line, tokens)
        if command is None:
            parsed.bad.append(Bad(line, MALFORMED))
            continue
        if verb == NONE:
            parsed.none = True
            continue
        if verb == MEAL and len(parsed.meals) >= MEALS_MAX:
            parsed.over_meals = True
            continue
        if verb in DISH_VERBS and len(parsed.dish) >= DISH_MAX:
            parsed.over_dish = True
            continue
        parsed.commands.append(command)
    return parsed
