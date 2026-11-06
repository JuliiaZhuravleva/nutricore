"""R0: honest time in capture. Several time phrases that disagree leave the day and no
clock; a meal's name is a time too. Pure: no rows, no model."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.services import meal_words
from app.services.meal_words import APPROX, DAY_ONLY, EXACT, time_phrases, time_words

ZONE = ZoneInfo("Asia/Tbilisi")  # UTC+4
EVENING = datetime(2026, 11, 6, 17, 0, tzinfo=UTC)  # пт 6 ноября, 21:00 local
AFTERNOON = datetime(2026, 10, 26, 9, 20, tzinfo=UTC)  # пн 26 октября, 13:20 local
TODAY, YESTERDAY = date(2026, 11, 6), date(2026, 11, 5)


def _local(said) -> datetime:
    return said.moment.astimezone(ZONE).replace(tzinfo=None)


def _said(text, sent=EVENING):
    return time_words(text, sent, ZONE)


# --- several phrases ---------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "утром каша, в 19:30 паста",
        "в обед суп, а вечером паста",
        "утром каша, в обед суп",
    ],
)
def test_two_phrases_that_disagree_give_the_day_and_no_clock(text):
    said = _said(text)
    assert said.precision == DAY_ONLY
    assert _local(said) == datetime(2026, 11, 6, 12, 0)  # the sent day's noon
    assert said.phrase is None
    assert said.rest == text  # nothing cut: no phrase is the meal's
    assert said.of_day


def test_control_one_clock_alone_is_still_that_clock():
    said = _said("в 19:30 паста")
    assert (said.precision, _local(said)) == (EXACT, datetime(2026, 11, 6, 19, 30))
    assert said.phrase == "в 19:30"


def test_the_first_found_phrase_no_longer_wins():
    # Before R0 the clock finder ran first and the meal went to 19:30.
    said = _said("утром каша, в 19:30 паста")
    assert _local(said) != datetime(2026, 11, 6, 19, 30)


def test_yesterday_named_once_carries_the_other_phrase_to_yesterday():
    said = _said("вчера утром каша, вечером суп")
    assert said.precision == DAY_ONLY
    assert _local(said) == datetime(2026, 11, 5, 12, 0)
    assert not said.of_day  # the words named the day


def test_yesterday_is_kept_when_the_other_phrase_resolves_to_today():
    # 21:00: «утром» alone is today's 9:00, yet «вчера» names the day.
    said = _said("вчера каша, утром суп")
    assert (said.precision, _local(said).date()) == (DAY_ONLY, YESTERDAY)


def test_phrases_that_all_fall_on_yesterday_give_yesterday():
    # 10:00: both «в обед» and «вечером» are still ahead, so both are yesterday's.
    morning = datetime(2026, 11, 6, 6, 0, tzinfo=UTC)
    said = _said("в обед суп, а вечером паста", sent=morning)
    assert (said.precision, _local(said).date()) == (DAY_ONLY, YESTERDAY)


def test_two_phrases_on_one_moment_are_one_time():
    said = _said("утром каша, утром кофе")
    assert (said.precision, _local(said)) == (APPROX, datetime(2026, 11, 6, 9, 0))


# --- one phrase, the overlap masked -----------------------------------------


def test_yesterday_morning_is_one_phrase_not_two():
    said = _said("вчера утром каша")
    assert (said.precision, _local(said)) == (APPROX, datetime(2026, 11, 5, 9, 0))
    assert said.phrase == "вчера утром"
    assert said.rest == "каша"


def test_control_an_hour_at_the_start_is_exact():
    said = _said("в 13 суп")
    assert (said.precision, _local(said)) == (EXACT, datetime(2026, 11, 6, 13, 0))


def test_time_phrases_are_in_text_order_and_none_overlaps():
    found = time_phrases("вчера утром каша, в 19:30 паста, вечером чай", EVENING, ZONE)
    assert [one.phrase for one in found] == ["вчера утром", "в 19:30", "вечером"]


def test_time_phrases_of_nothing_is_empty_and_a_real_phrase_is_found():
    assert time_phrases("суп с хлебом", EVENING, ZONE) == []
    assert time_phrases(None, EVENING, ZONE) == []
    assert [p.phrase for p in time_phrases("суп утром", EVENING, ZONE)] == ["утром"]


def test_a_masked_phrase_makes_no_end_of_the_text_for_an_hour():
    # «в 2» stands in the middle; the phrase cut after it must not make it the end.
    found = time_phrases("суп в 2 вчера утром", EVENING, ZONE)
    assert [one.phrase for one in found] == ["вчера утром"]


# --- a meal's name -----------------------------------------------------------


@pytest.mark.parametrize(
    "text,hour",
    [
        ("на завтрак каша", 9),
        ("каша за завтраком", 9),
        ("в обед суп", 13),
        ("на обед суп", 13),
        ("суп за обедом", 13),
        ("на полдник йогурт", 16),
        ("на ужин паста", 19),
        ("паста за ужином", 19),
        ("В  Обед суп", 13),
    ],
)
def test_a_meals_name_is_an_approximate_clock(text, hour):
    said = _said(text)
    assert (said.precision, _local(said)) == (
        APPROX,
        datetime(2026, 11, 6, hour, 0),
    )
    assert said.of_day


def test_lunch_said_before_lunch_is_yesterdays():
    said = _said("в обед суп", sent=datetime(2026, 11, 6, 6, 0, tzinfo=UTC))
    assert _local(said) == datetime(2026, 11, 5, 13, 0)


def test_yesterday_with_a_meals_name_keeps_the_clock():
    said = _said("вчера на обед суп")
    assert (said.precision, _local(said)) == (APPROX, datetime(2026, 11, 5, 13, 0))
    assert said.phrase == "вчера на обед"


@pytest.mark.parametrize(
    "text", ["обеденный стол", "суп обед", "ужин из пасты", "завтраки", "обедом"]
)
def test_words_like_a_meals_name_stay_food(text):
    assert _said(text) is None


def test_the_meal_clocks_are_the_assumed_ones():
    hours = {name: clock.hour for name, clock in meal_words._MEAL_NAMES.items()}
    assert hours == {
        "на завтрак": 9,
        "за завтраком": 9,
        "в обед": 13,
        "на обед": 13,
        "за обедом": 13,
        "на полдник": 16,
        "на ужин": 19,
        "за ужином": 19,
    }


def test_the_afternoon_example_of_the_spec():
    # 13:20: «утром» is today's 9:00 and «в обед» today's 13:00.
    said = _said("утром каша, в обед суп", sent=AFTERNOON)
    assert (said.precision, _local(said)) == (DAY_ONLY, datetime(2026, 10, 26, 12, 0))
    assert _said("утром каша", sent=AFTERNOON).precision == APPROX  # control
