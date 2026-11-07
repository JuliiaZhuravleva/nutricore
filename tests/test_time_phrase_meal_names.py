"""Stage 6, D2: a meal's name («в обед», «на ужин») counts as a clause's time in R0's
rule alone. The proof against the module before it (``610cde5``, read from git): over
the corpus and the phrases here, the only outputs that differ are texts of two or more
clauses one of which holds a meal's name, and each of those is now R0's result, the day
and no clock. Every other output is the old one. Pure: no rows, no model."""

from __future__ import annotations

import re
import subprocess
import sys
import types
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.services import meal_words
from tests.test_time_phrase_corpus import ROWS

BEFORE = "610cde5"
ROOT = Path(__file__).resolve().parent.parent
ZONE = ZoneInfo("Asia/Tbilisi")  # UTC+4
# The test's own reading of a meal's name, not the module's: «в обед», «на обед», «за
# обедом», «на завтрак», «за завтраком», «на ужин», «за ужином», «на полдник».
NAMED = re.compile(
    r"(?<!\w)(?:(?:в|на)\s+обед|за\s+обедом|на\s+завтрак|за\s+завтраком|на\s+ужин"
    r"|за\s+ужином|на\s+полдник)(?!\w)",
    re.IGNORECASE,
)

# Phrases the corpus has not: day recalls, voice-like ones, and near misses.
PHRASES = [
    "утром овсянка, в обед суп",
    "Утром овсянка, в обед суп.",
    "утром овсянка, в обед суп, вечером паста",
    "на завтрак овсянка, на ужин паста",
    "на завтрак омлет, на обед суп, на ужин рыба",
    "за завтраком каша, а за ужином паста",
    "за обедом суп, вечером йогурт",
    "в обед суп; вечером паста",
    "на полдник йогурт, вечером паста",
    "на полдник йогурт, на ужин паста",
    "в обед суп — в 19:00 паста",
    "в 8:00 каша, в обед суп",
    "вчера на обед суп, вечером паста",
    "вчера утром каша, на обед суп",
    "час назад суп, на завтрак каша",
    "ночью бутерброд, на завтрак каша",
    "на ужин рыба, вечером чай",
    "утром каша, на завтрак кофе",
    "на обед суп, на обед хлеб",
    "на обед суп и днём чай",
    "в обед суп, в 13:00 чай",
    "утром овсянка в обед суп",
    "за ужином паста, утром каша",
    "на завтрак каша и в 13:00 суп",
    "каша, на ужин паста",
    "суп, в обед",
    "в обед, вечером",
    "спасибо за обед, утром каша",
    "обеденный стол, утром каша",
    "на обеде суп, вечером паста",
    "завтраки, вечером паста",
    "суп обед, вечером паста",
    "в обеде мало соли, вечером паста",
    "на ужине паста, утром каша",
    "ужин из пасты, утром каша",
    "утром каша, обедом суп",
    "в обедневший суп, утром каша",
    "на завтраке каша, вечером суп",
]
CLOCKS = ["00:30", "06:30", "09:30", "13:20", "18:40", "21:00"]


def _module(source: str) -> types.ModuleType:
    name = "meal_words_before"
    module = types.ModuleType(name)
    sys.modules[name] = module  # its dataclasses look their module up there
    exec(
        compile(source, f"{BEFORE}:app/services/meal_words.py", "exec"), module.__dict__
    )
    return module


@pytest.fixture(scope="module")
def before():
    done = subprocess.run(
        ["git", "show", f"{BEFORE}:app/services/meal_words.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, f"git show {BEFORE} did not run: {done.stderr}"
    module = _module(done.stdout)
    assert not hasattr(module, "_MEAL_NAME")  # the control: it is the old module
    return module


def _sent(clock: str) -> datetime:
    hour, minute = map(int, clock.split(":"))
    return datetime(2026, 11, 6, hour, minute, tzinfo=ZONE)


def _inputs() -> list:
    corpus = [(text, sent, bare) for text, sent, bare, *_rest in ROWS]
    new = [(t, c, b) for t in PHRASES for c in CLOCKS for b in (False, True)]
    return sorted(set(corpus + new))


def _key(said):
    if said is None:
        return None
    return (said.moment, said.precision, said.phrase, said.rest, said.of_day)


def _several(text: str) -> bool:
    filled = [one for one in meal_words.clauses(text) if one.strip(" .,;—")]
    return len(filled) >= 2 and any(NAMED.search(one) for one in filled)


def test_there_are_at_least_thirty_new_phrases():
    assert len(PHRASES) >= 30
    assert not set(PHRASES) & {row[0] for row in ROWS}


def test_only_several_clauses_with_a_meals_name_changed(before):
    changed, same = [], []
    for text, clock, bare in _inputs():
        old = before.time_words(text, _sent(clock), ZONE, bare_ok=bare)
        new = meal_words.time_words(text, _sent(clock), ZONE, bare_ok=bare)
        (same if _key(old) == _key(new) else changed).append((text, clock, bare, new))
    assert same and changed  # known positives on both sides
    for text, clock, bare, new in changed:
        assert _several(text), (text, clock, bare)
        local = new.moment.astimezone(ZONE)
        assert new.precision == meal_words.DAY_ONLY, (text, clock)
        assert (new.phrase, new.rest, new.of_day) == (None, text, True), text
        assert local.time() == time(12), (text, clock)
    changed_texts = {row[0] for row in changed}
    # the spec's own words, and the near misses that must stay as they were
    assert "утром овсянка, в обед суп" in changed_texts
    assert "на завтрак овсянка, на ужин паста" in changed_texts
    for near in (
        "в обед суп",
        "каша, на ужин паста",
        "утром овсянка в обед суп",
        "обеденный стол, утром каша",
        "на обеде суп, вечером паста",
        "спасибо за обед, утром каша",
        "на ужин рыба, вечером чай",
    ):
        assert near not in changed_texts, near


@pytest.mark.parametrize("clock", ["21:05", "13:20"])
def test_d2_a_day_recall_is_the_day_and_no_clock(clock):
    said = meal_words.time_words("утром овсянка, в обед суп", _sent(clock), ZONE)
    assert (said.precision, said.phrase) == (meal_words.DAY_ONLY, None)
    assert said.moment.astimezone(ZONE) == datetime(2026, 11, 6, 12, tzinfo=ZONE)
