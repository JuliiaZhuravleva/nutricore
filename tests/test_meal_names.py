"""D4 (2026-11-14): a meal's name — the said one wins, else the hour, else none."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.models.meal import Meal
from app.services.day_card import APPROX, DAY_ONLY, meal_name
from app.services.meal_card import line_head
from app.services.meal_words import message_name, said_name, without_name

ZONE = ZoneInfo("Asia/Tbilisi")  # UTC+4


def _at(hour: int, minute: int = 0, **fields) -> Meal:
    local = datetime(2026, 11, 14, hour, minute, tzinfo=ZONE)
    return Meal(meal_time=local.astimezone(UTC), **fields)


@pytest.mark.parametrize(
    "text, name",
    [
        ("на обед суп", "обед"),
        ("в обед суп", "обед"),
        ("за ужином паста", "ужин"),
        ("на завтрак каша", "завтрак"),
        ("на полдник йогурт", "полдник"),
        ("на перекус яблоко", "перекус"),
        ("перекус: яблоко", "перекус"),
        ("Завтрак — овсянка", "завтрак"),
        ("вес 72,4, зарядку сделала, на завтрак каша", "завтрак"),
    ],
)
def test_a_said_name_is_found(text, name):
    assert said_name(text) == name


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "суп",
        "после обеда кофе",  # «обеда»: not the name of this meal
        "остатки ужина",
        "до завтрака чай",
        "на завтрак каша, на обед суп",  # two names: no one is this meal's
    ],
)
def test_no_name_when_none_or_two_are_said(text):
    assert said_name(text) is None


@pytest.mark.parametrize(
    "hour, minute, name",
    [
        (5, 59, None),
        (6, 0, "завтрак"),
        (11, 59, "завтрак"),
        (12, 0, "обед"),
        (15, 59, "обед"),
        (16, 0, "полдник"),
        (17, 59, "полдник"),
        (18, 0, "ужин"),
        (22, 59, "ужин"),
        (23, 0, None),
        (0, 40, None),
    ],
)
def test_the_hour_names_a_meal_with_no_said_name(hour, minute, name):
    assert meal_name(_at(hour, minute), ZONE) == name


def test_an_approximate_time_is_named_by_its_hour():
    assert meal_name(_at(13, time_precision=APPROX), ZONE) == "обед"


def test_a_meal_without_a_time_has_no_name_by_the_hour():
    assert meal_name(_at(12, time_precision=DAY_ONLY), ZONE) is None
    said = _at(12, time_precision=DAY_ONLY, meal_type="обед")
    assert meal_name(said, ZONE) == "обед"


def test_a_said_name_wins_over_the_hour():
    assert meal_name(_at(16, 30, meal_type="обед"), ZONE) == "обед"
    assert meal_name(_at(0, 40, meal_type="Перекус"), ZONE) == "перекус"


def test_an_unknown_stored_label_falls_back_to_the_hour():
    # The REST API took any string («snack»): it never reaches a title.
    assert meal_name(_at(9, meal_type="snack"), ZONE) == "завтрак"
    assert meal_name(_at(1, meal_type="snack"), ZONE) is None


@pytest.mark.parametrize(
    "meal, head",
    [
        (_at(9, 10), "Записан завтрак"),
        (_at(0, 40), "Записано"),
        (_at(16, 30, meal_type="обед"), "Записан обед"),
        (
            _at(21, time_source="said"),
            "Записан ужин на сб 14 ноября, 21:00",
        ),
        (
            _at(12, time_source="said", time_precision=DAY_ONLY),
            "Записано на сб 14 ноября, без времени",
        ),
    ],
)
def test_the_line_says_the_name(meal, head):
    assert line_head(meal, ZONE) == head


def test_several_photos_keep_their_count():
    assert line_head(_at(13), ZONE, "Записано по 2 фото") == "Записан обед по 2 фото"


@pytest.mark.parametrize(
    "text, left",
    [
        ("на обед гречка", "гречка"),
        ("гречка на ужин", "гречка"),
        ("за завтраком каша с бананом", "каша с бананом"),
        ("Завтрак — овсянка", "овсянка"),
        ("перекус: яблоко", "яблоко"),
        ("комплексный обед", "комплексный обед"),
        ("суп", "суп"),
    ],
)
def test_the_name_is_cut_from_the_food_words(text, left):
    assert without_name(text) == left


SENT = datetime(2026, 11, 14, 9, 30, tzinfo=ZONE)


@pytest.mark.parametrize(
    "text, name",
    [
        ("на обед суп", "обед"),
        ("суп", None),
        # Two meals in one message (a voice or a recap): neither name is the message's.
        ("утром овсянка, в обед суп", None),
        ("на завтрак каша, на обед суп", None),
    ],
)
def test_a_message_takes_a_name_only_when_it_is_one_meal(text, name):
    assert message_name(text, SENT, ZONE) == name
