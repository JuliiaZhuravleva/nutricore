"""R0 frozen: one table of what the time words of a message say. Every row is a text,
the local clock it was sent at on пт 6 ноября 2026 in Asia/Tbilisi, and whether a bare
time is allowed (a caption, a reply), against what ``time_words`` gives:

- ``-`` none: the meal keeps the moment it was sent (no words, or a meal going on now);
- ``exact 11-06 19:30`` / ``approx 11-05 09:00``: that local moment;
- ``day 11-05``: that day and no clock.

A trailing `` +`` / `` -`` also asserts ``of_day`` (a clock of a day the words did not
name). Pure: no rows, no model. A change of a row here is a change of behaviour."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.meal_words import DAY_ONLY, time_words

ZONE = ZoneInfo("Asia/Tbilisi")  # UTC+4


def _sent(clock: str) -> datetime:
    hour, minute = map(int, clock.split(":"))
    return datetime(2026, 11, 6, hour, minute, tzinfo=ZONE)


def render(said, of_day: bool = False) -> str:
    """The row's form of a ``SaidTime``."""
    if said is None:
        return "-"
    local = said.moment.astimezone(ZONE)
    if said.precision == DAY_ONLY:
        shown = f"day {local:%m-%d}"
    else:
        shown = f"{said.precision} {local:%m-%d %H:%M}"
    return f"{shown} {'+' if said.of_day else '-'}" if of_day else shown


# (text, sent local clock on 11-06, bare_ok, expected)
ROWS = [
    # --- one clock, one hour, «назад»: as before R0 -----------------------------
    ("в 19:30 паста", "21:00", False, "exact 11-06 19:30 +"),
    ("в 13 суп", "21:00", False, "exact 11-06 13:00"),
    ("в 13 суп", "13:20", False, "exact 11-06 13:00"),
    ("в 13", "13:20", True, "exact 11-06 13:00"),
    ("в 13:10", "13:20", True, "exact 11-06 13:10"),
    ("в 14:10", "13:20", True, "exact 11-05 14:10 +"),  # still ahead: yesterday's
    ("в 12:10", "13:20", True, "exact 11-06 12:10"),
    ("в 12", "13:20", True, "exact 11-06 12:00"),
    ("в 8:00 каша", "10:30", False, "exact 11-06 08:00"),
    ("в 8:00 каша", "07:00", False, "exact 11-05 08:00"),
    ("сегодня в 9:00 каша", "10:00", False, "exact 11-06 09:00"),
    ("вчера 23:10", "13:20", True, "exact 11-05 23:10 -"),
    ("вчера суп в 13:00", "21:00", False, "exact 11-05 13:00 -"),
    ("час назад суп", "13:20", False, "approx 11-06 12:20"),
    ("час назад", "13:20", True, "approx 11-06 12:20"),
    ("суп полчаса назад", "13:20", False, "approx 11-06 12:50"),
    ("ещё сыр час назад", "13:20", False, "approx 11-06 12:20"),
    # --- not time words ----------------------------------------------------------
    ("вчерашний суп", "13:20", False, "-"),
    ("в 3 раза больше супа", "13:20", False, "-"),
    ("3 часа назад суп", "13:20", False, "-"),
    ("суп с хлебом", "21:00", False, "-"),
    ("обеденный стол", "21:00", False, "-"),
    ("суп обед", "21:00", False, "-"),
    ("ужин из пасты", "21:00", False, "-"),
    ("завтраки", "21:00", False, "-"),
    ("обедом", "21:00", False, "-"),
    # --- a time word alone is no meal, unless bare_ok ------------------------------
    ("утром", "13:20", False, "-"),
    ("утром", "13:20", True, "approx 11-06 09:00 +"),
    ("вчера", "13:20", True, "day 11-05 -"),
    ("вечером", "21:00", True, "approx 11-06 20:00"),
    # a part going on now: the sent moment, its own clock kept for a reply
    ("вечером", "18:30", True, "approx 11-06 18:30 +"),
    ("утром", "08:00", True, "approx 11-06 08:00"),
    ("в обед", "11:30", True, "approx 11-06 11:30"),
    ("на обед", "12:40", True, "approx 11-06 12:40 +"),
    # --- «вчера» with a part, a meal's name, the night ------------------------------
    ("вчера суп", "13:20", False, "day 11-05 -"),
    ("вчера утром каша", "21:00", False, "approx 11-05 09:00 -"),
    ("Вчера утром суп", "13:20", False, "approx 11-05 09:00"),
    ("вчера вечером суп", "13:20", False, "approx 11-05 20:00"),
    ("суп вчера днём", "13:20", False, "approx 11-05 14:00"),
    ("вчера на обед суп", "21:00", False, "approx 11-05 13:00 -"),
    ("вчера паста на ужин", "21:00", False, "approx 11-05 19:00"),
    ("вчера на ужин суп", "16:30", False, "approx 11-05 19:00"),
    ("вчера на завтрак каша", "06:30", False, "approx 11-05 09:00"),
    ("суп в 2 вчера утром", "21:00", False, "approx 11-05 09:00"),
    ("вчера ночью бутерброд", "10:00", False, "day 11-05 -"),
    ("вчера ночью бутерброд", "21:00", False, "day 11-05 -"),
    ("вчера ночью бутерброд", "00:30", False, "day 11-05 -"),
    ("бутерброд вчера ночью", "13:20", False, "day 11-05 -"),
    ("вчера ночью", "10:00", True, "day 11-05 -"),
    ("вчера ночью в 23:30 бутерброд", "10:00", False, "exact 11-05 23:30"),
    # --- the night ----------------------------------------------------------------
    ("ночью суп", "13:20", False, "approx 11-06 03:00 +"),
    ("ночью бутерброд", "10:00", False, "approx 11-06 03:00"),
    ("ночью бутерброд", "00:30", False, "-"),  # this night, going on
    ("ночью бутерброд", "02:00", False, "-"),
    ("ночью бутерброд", "23:50", False, "-"),
    ("ночью в 2:00 бутерброд", "10:00", False, "exact 11-06 02:00"),
    # --- a part or a meal's name said live: never yesterday --------------------------
    ("утром каша", "06:30", False, "-"),
    ("утром каша", "08:30", False, "-"),
    ("утром каша", "12:40", False, "approx 11-06 09:00"),
    ("утром каша", "13:20", False, "approx 11-06 09:00"),
    ("утром каша", "23:50", False, "approx 11-06 09:00"),
    ("утром каша", "00:30", False, "approx 11-05 09:00"),
    ("хлеб утром", "13:20", False, "approx 11-06 09:00"),
    ("на завтрак каша", "06:30", False, "-"),
    ("на завтрак каша", "08:30", False, "-"),
    ("на завтрак овсянка", "08:30", False, "-"),
    ("на завтрак каша", "12:40", False, "approx 11-06 09:00"),
    ("на завтрак каша", "00:30", False, "approx 11-05 09:00"),
    ("каша за завтраком", "21:00", False, "approx 11-06 09:00 +"),
    ("в обед суп", "06:30", False, "approx 11-05 13:00"),
    ("в обед суп", "10:00", False, "-"),
    ("в обед суп", "12:40", False, "-"),
    ("в обед суп", "13:20", False, "approx 11-06 13:00 +"),
    ("в обед суп", "16:30", False, "approx 11-06 13:00"),
    ("в обед суп", "00:30", False, "approx 11-05 13:00"),
    ("В  Обед суп", "21:00", False, "approx 11-06 13:00"),
    ("на обед суп", "21:00", False, "approx 11-06 13:00"),
    ("суп за обедом", "21:00", False, "approx 11-06 13:00"),
    ("днём суп", "13:20", False, "-"),
    ("днём суп", "08:30", False, "approx 11-05 14:00"),
    ("на полдник йогурт", "21:00", False, "approx 11-06 16:00"),
    ("на полдник йогурт", "12:40", False, "-"),
    ("на ужин суп", "06:30", False, "approx 11-05 19:00"),
    ("на ужин паста", "07:00", False, "approx 11-05 19:00"),
    ("на ужин суп", "12:40", False, "approx 11-05 19:00"),
    ("на ужин суп", "16:30", False, "-"),
    ("на ужин паста", "18:40", False, "-"),
    ("на ужин суп", "23:50", False, "approx 11-06 19:00"),
    ("на ужин суп", "00:30", False, "approx 11-05 19:00"),
    ("паста за ужином", "21:00", False, "approx 11-06 19:00"),
    ("вечером паста", "16:30", False, "-"),
    ("вечером паста", "18:40", False, "-"),
    ("вечером паста", "19:00", False, "-"),
    ("вечером паста", "12:40", False, "approx 11-05 20:00"),
    ("вечером паста", "23:50", False, "approx 11-06 20:00"),
    ("суп, вечером", "13:20", False, "approx 11-05 20:00"),
    ("сегодня утром каша", "06:30", False, "-"),
    ("сегодня утром каша", "21:00", False, "approx 11-06 09:00"),
    # --- a clock with its own part or meal's name is one time -----------------------
    ("утром в 8:30 овсянка", "21:00", False, "exact 11-06 08:30"),
    ("овсянка утром в 8:30", "21:00", False, "exact 11-06 08:30"),
    ("вечером в 19:30 паста", "21:00", False, "exact 11-06 19:30"),
    ("на ужин в 19:30 паста", "21:00", False, "exact 11-06 19:30"),
    ("на ужин паста в 19:30", "21:00", False, "exact 11-06 19:30"),
    ("на обед в 13:30 суп", "21:00", False, "exact 11-06 13:30"),
    ("на обед суп в 13:30", "21:00", False, "exact 11-06 13:30"),
    ("утром, в 12:00 каша", "21:00", False, "exact 11-06 12:00"),
    ("на завтрак в 8:00 овсянка", "08:30", False, "exact 11-06 08:00"),
    ("вчера вечером в 20:00 паста", "10:00", False, "exact 11-05 20:00 -"),
    ("вчера вечером в 20:00 паста", "21:00", False, "exact 11-05 20:00 -"),
    # --- two clauses, two meals: the day and no clock -------------------------------
    ("утром каша, в 19:30 паста", "21:00", False, "day 11-06 +"),
    ("утром каша, в 19:30 паста", "10:00", False, "day 11-06 +"),
    (
        "утром каша в 19:30 паста",
        "21:00",
        False,
        "day 11-06",
    ),  # one clause, other hours
    ("в обед суп, а вечером паста", "21:00", False, "day 11-06"),
    ("в обед суп, а вечером паста", "10:00", False, "day 11-06"),  # lunch is on
    ("в обед суп, а вечером паста", "07:00", False, "day 11-05"),  # both far ahead
    ("утром каша, в обед суп", "21:00", False, "day 11-06 +"),
    ("утром каша, в обед суп", "13:20", False, "day 11-06"),
    ("утром каша, в обед суп", "09:30", False, "day 11-06"),  # lunch on, 9:00 past
    ("на ужин суп, вечером чай", "18:40", False, "-"),  # both going on: one moment
    ("на завтрак каша, в обед суп", "06:30", False, "day 11-06"),
    ("в обед суп, в 15:00 чай", "21:00", False, "day 11-06"),
    ("в обед суп и в 16:00 чай", "21:00", False, "day 11-06"),
    ("утром каша, в 10:30 йогурт", "21:00", False, "day 11-06"),
    ("днём суп, полдник в 17:00", "21:00", False, "day 11-06"),
    ("на обед суп, в 16:30 печенье", "18:00", False, "day 11-06"),
    ("на обед суп, в 13:30 чай", "21:00", False, "day 11-06"),
    ("на ужин паста, в 19:30 чай", "21:00", False, "day 11-06"),
    ("утром каша, утром кофе", "21:00", False, "approx 11-06 09:00"),  # one moment
    ("утром каша, утром кофе", "08:30", False, "-"),
    ("утром каша, в 13:00 суп", "13:20", False, "day 11-06"),
    ("в 8:00 каша, в 10:00 кофе", "10:30", False, "day 11-06"),
    # --- «вчера» and «сегодня» across clauses ------------------------------------
    ("вчера утром каша, вечером суп", "21:00", False, "day 11-05 -"),
    ("вчера каша, утром суп", "21:00", False, "day 11-05 -"),
    ("вчера суп, утром каша", "10:00", False, "day 11-05"),
    ("вчера пицца, сегодня утром каша", "10:00", False, "day 11-06"),
    ("вчера суп, сегодня в 9:00 каша", "10:00", False, "day 11-06"),
    ("на завтрак каша, вчера в 22:00 торт", "21:00", False, "day 11-06 +"),
    ("вчера в обед суп, сегодня на завтрак каша", "10:00", False, "day 11-06"),
    ("вчера суп, ночью бутерброд", "10:00", False, "day 11-05"),
]


@pytest.mark.parametrize("text,sent,bare_ok,expected", ROWS)
def test_time_phrase_corpus(text, sent, bare_ok, expected):
    said = time_words(text, _sent(sent).astimezone(ZONE), ZONE, bare_ok=bare_ok)
    assert render(said, expected.endswith((" +", " -"))) == expected


def test_the_corpus_has_no_row_twice():
    keys = [(text, sent, bare_ok) for text, sent, bare_ok, _ in ROWS]
    assert len(keys) == len(set(keys))
