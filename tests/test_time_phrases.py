"""R0: honest time in capture. Clauses that each say a different time leave the day and
no clock; everything else reads as before R0. Pure: no rows, no model. The full table
is ``test_time_phrase_corpus.py``."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.meal_words import APPROX, DAY_ONLY, EXACT, clauses, time_words

ZONE = ZoneInfo("Asia/Tbilisi")  # UTC+4
EVENING = datetime(2026, 11, 6, 17, 0, tzinfo=UTC)  # пт 6 ноября, 21:00 local
TEN = datetime(2026, 11, 6, 6, 0, tzinfo=UTC)  # пт 6 ноября, 10:00 local


def _local(said) -> datetime:
    return said.moment.astimezone(ZONE).replace(tzinfo=None)


def _said(text, sent=EVENING, bare_ok=False):
    return time_words(text, sent, ZONE, bare_ok=bare_ok)


# --- the clauses -------------------------------------------------------------


@pytest.mark.parametrize(
    "text,parts",
    [
        ("утром каша, вечером суп", ["утром каша", " вечером суп"]),
        ("утром каша; вечером суп", ["утром каша", " вечером суп"]),
        ("утром каша — вечером суп", ["утром каша ", " вечером суп"]),
        ("утром каша и вечером суп", ["утром каша ", " вечером суп"]),
        ("утром каша а вечером суп", ["утром каша ", " вечером суп"]),
        ("утром каша, а вечером суп", ["утром каша", " ", " вечером суп"]),
        ("Утром каша И вечером суп", ["Утром каша ", " вечером суп"]),
    ],
)
def test_a_text_is_cut_at_a_comma_semicolon_dash_and_a_standalone_i_or_a(text, parts):
    assert clauses(text) == parts


@pytest.mark.parametrize(
    "text", ["вечером суп", "кашаи суп", "суп ибо вкусно", "рис-курица", "утром каша."]
)
def test_letters_inside_a_word_and_other_marks_cut_nothing(text):
    assert clauses(text) == [text]


# --- two clauses, two times: the day and no clock ----------------------------


@pytest.mark.parametrize(
    "text",
    [
        "утром каша, в 19:30 паста",
        "утром каша и вечером суп",
        "в 19:30 паста, а утром каша",
        "утром каша — вечером суп",
    ],
)
def test_two_clauses_that_disagree_give_the_sent_day_and_no_clock(text):
    said = _said(text)
    assert said.precision == DAY_ONLY
    assert _local(said) == datetime(2026, 11, 6, 12, 0)  # the day's noon
    assert said.phrase is None
    assert said.rest == text  # nothing cut: no phrase is the meal's
    assert said.of_day


def test_control_before_r0_the_first_finder_won():
    # The clock finder runs first: alone, «в 19:30» is that clock.
    said = _said("в 19:30 паста")
    assert (said.precision, _local(said)) == (EXACT, datetime(2026, 11, 6, 19, 30))
    assert (said.phrase, said.rest) == ("в 19:30", "паста")


def test_moments_on_one_day_give_that_day():
    # 10:00: «в 19:30» is yesterday's and «вчера утром» too.
    said = _said("вчера утром каша, в 19:30 паста", sent=TEN)
    assert (said.precision, _local(said)) == (DAY_ONLY, datetime(2026, 11, 5, 12, 0))


def test_moments_on_two_days_with_yesterday_said_give_yesterday():
    said = _said("вчера утром каша, вечером суп")  # 11-05 9:00 and 11-06 20:00
    assert (said.precision, _local(said)) == (DAY_ONLY, datetime(2026, 11, 5, 12, 0))


@pytest.mark.parametrize(
    "text",
    ["вчера пицца, сегодня утром каша", "сегодня утром каша, вчера вечером суп"],
)
def test_today_said_keeps_the_sent_day(text):
    said = _said(text, sent=TEN if text.startswith("вчера") else EVENING)
    assert (said.precision, _local(said).date()) == (
        DAY_ONLY,
        datetime(2026, 11, 6).date(),
    )


def test_moments_on_two_days_and_no_day_named_give_the_sent_day():
    said = _said("утром каша, в 19:30 паста", sent=TEN)  # 11-06 9:00, 11-05 19:30
    assert (said.precision, _local(said)) == (DAY_ONLY, datetime(2026, 11, 6, 12, 0))


def test_two_bare_clauses_that_disagree_are_the_day_too():
    said = _said("утром, вечером", bare_ok=True)
    assert (said.precision, said.phrase, said.rest) == (
        DAY_ONLY,
        None,
        "утром, вечером",
    )


# --- otherwise exactly as before R0 --------------------------------------------


def test_two_clauses_on_one_moment_read_as_before():
    said = _said("утром каша, утром кофе")
    assert (said.precision, _local(said)) == (APPROX, datetime(2026, 11, 6, 9, 0))
    assert (said.phrase, said.rest) == ("утром", "каша, утром кофе")


def test_one_timed_clause_reads_as_before():
    said = _said("суп, вечером")
    assert (said.precision, said.phrase, said.rest) == (APPROX, "вечером", "суп")


def test_a_meals_name_is_still_no_time():
    # The meal-name finder of the first R0 is dropped: only «утром» is a time here.
    said = _said("утром каша, в обед суп")
    assert (said.precision, said.phrase) == (APPROX, "утром")
    assert _said("в обед суп") is None


def test_a_time_word_alone_is_no_meal_unless_bare():
    assert _said("утром") is None
    assert _said("утром", bare_ok=True).phrase == "утром"


@pytest.mark.parametrize("text", [None, "", "суп с хлебом", "вчерашний суп, хлеб"])
def test_no_time_words_say_nothing(text):
    assert _said(text) is None
