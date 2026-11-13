"""🍲 The tidy-up's finder: the pure ``plan`` on hand-made dishes, and ``find`` on rows.

Each rule has its pair that must and its pair that must not, so a rule loosened or
tightened by accident shows on one side. Nothing here reaches a model.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

import pytest

from app.crud.crud_personal_food import AnnQueryError, crud_personal_food
from app.services import dish_tidy as tidy
from app.services.dish_tidy import Decline, Dish

NOW = datetime(2026, 11, 15, 9, 0, tzinfo=UTC)
TODAY = date(2026, 11, 15)


def _rows(*dishes, near=None, declines=None, today=TODAY):
    found = tidy.plan(list(dishes), near or {}, declines or {}, NOW, today=today)
    return [(row.kind, row.source_id, row.target_id, row.ticked) for row in found.rows]


def _pair(one: str, other: str, **kw):
    """(kind, ticked) of two dishes by name, or None when they stay apart."""
    rows = _rows(Dish(1, one, kcal=kw.get("a")), Dish(2, other, kcal=kw.get("b")))
    assert len(rows) <= 1
    return (rows[0][0], rows[0][3]) if rows else None


# --- the name key -------------------------------------------------------------------


@pytest.mark.parametrize(
    "one, other",
    [
        ("соус томатный", "томатный соус"),
        ("это гречка", "гречка"),
        ("сырок (Бренд, 40 г)", "сырок 40 г"),
        ("Йогурт — Бренд", "йогурт"),
        ("Сырок, ёжик", "сырок ежик"),
        ("сырок 40 гр", "сырок 40g"),
    ],
)
def test_near_identical_names_merge_by_themselves(one, other):
    assert _pair(one, other) == (tidy.SAME, True)


@pytest.mark.parametrize(
    "one, other",
    [
        ("сметана 15 %", "сметана 26%"),
        ("гречка с маслом", "гречка"),
        ("кофе без сахара", "кофе"),
        ("кефир (безлактозный)", "кефир"),
        ("сыр", "сырок глазированный"),
        ("белый хлеб", "белок"),
        ("суп", "борщ"),
    ],
)
def test_told_apart_names_never_pair(one, other):
    assert _pair(one, other) is None


def test_a_percent_on_one_side_only_does_not_block_a_ticked_proposal():
    assert _pair("молоко 2,5%", "молоко") == (tidy.MERGE, True)


def test_different_sizes_are_proposed_unticked():
    assert _pair("сырок 40 г", "сырок 45 г") == (tidy.MERGE, False)


@pytest.mark.parametrize(
    "one, other",
    [("гречка", "гречкой"), ("томатный соус", "соус томатного")],
)
def test_word_forms_are_proposed_ticked(one, other):
    assert _pair(one, other) == (tidy.MERGE, True)


@pytest.mark.parametrize(
    "one, other",
    [
        ("гречка отварная", "гречка"),
        ("морковь по-домашнему", "морковь"),
        ("Шоколад White 39g", "Шоколад"),
        ("сырок глазированный", "глазированный сырок ваниль"),
    ],
)
def test_a_word_or_two_more_is_proposed_unticked(one, other):
    # An added word is often another dish: the person ticks it, not the bot.
    assert _pair(one, other) == (tidy.MERGE, False)


def test_an_added_word_with_close_embeddings_is_ticked():
    one, other = Dish(1, "гречка"), Dish(2, "гречка отварная")
    assert _rows(one, other, near={(1, 2): 0.12}) == [(tidy.MERGE, 2, 1, True)]
    assert _rows(one, other, near={(1, 2): 0.18}) == [(tidy.MERGE, 2, 1, False)]


def test_three_words_more_is_too_many():
    assert _pair("сырок", "сырок в шоколаде ваниль большой") is None
    # The control: two more is still proposed.
    assert _pair("сырок", "сырок ваниль большой") == (tidy.MERGE, False)


def test_stems_keep_suffixes_apart():
    assert tidy.same_word("гречка", "гречкой")
    assert not tidy.same_word("сыр", "сырок")
    assert not tidy.same_word("белый", "белок")


# --- the numbers --------------------------------------------------------------------


@pytest.mark.parametrize(
    "b, expected",
    [(109, (tidy.SAME, True)), (112, (tidy.MERGE, True)), (135, None)],
)
def test_kcal_9_12_and_35_percent_apart(b, expected):
    assert _pair("рис", "рис", a=100, b=b) == expected


def test_one_side_without_numbers_has_nothing_to_disagree_with():
    assert _pair("рис", "рис", a=100, b=None) == (tidy.SAME, True)


def test_grams_a_person_set_far_apart_turn_same_into_a_proposal():
    one = Dish(1, "сырник", grams=100)
    other = Dish(2, "сырник", grams=130)
    assert _rows(one, other) == [(tidy.MERGE, 2, 1, True)] or _rows(one, other) == [
        (tidy.MERGE, 1, 2, True)
    ]
    near = Dish(2, "сырник", grams=110)
    assert _rows(one, near)[0][0] == tidy.SAME


# --- embeddings -----------------------------------------------------------------------


def test_close_embeddings_propose_and_far_ones_do_not():
    one, other = Dish(1, "борщ"), Dish(2, "суп свекольный")
    assert _rows(one, other, near={(1, 2): 0.12}) == [(tidy.MERGE, 2, 1, True)]
    assert _rows(one, other, near={(1, 2): 0.16}) == []


def test_embeddings_never_override_a_connector():
    one, other = Dish(1, "гречка с маслом"), Dish(2, "гречка")
    assert _rows(one, other, near={(1, 2): 0.05}) == []


def test_a_failed_close_pairs_query_leaves_the_stems_and_says_so(monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise AnnQueryError("close pairs failed for user_id=7: OperationalError")

    monkeypatch.setattr(crud_personal_food, "close_pairs", broken)

    class _User:
        id = 7

    with caplog.at_level(logging.ERROR, logger="app.services.dish_tidy"):
        assert tidy.close_pairs(None, _User(), {1, 2}) == {}
    assert "dish tidy: close pairs failed for user_id=7, stems only" in caplog.text


def test_close_pairs_keeps_only_the_dishes_asked_about(monkeypatch):
    monkeypatch.setattr(
        crud_personal_food,
        "close_pairs",
        lambda *a, **k: {(1, 2): 0.1, (2, 9): 0.1},
    )

    class _User:
        id = 7

    assert tidy.close_pairs(None, _User(), {1, 2, 3}) == {(1, 2): 0.1}


# --- the target and the groups ------------------------------------------------------


def test_the_target_is_the_dish_whose_numbers_the_person_set():
    typed = Dish(5, "томатный соус", by_user=True)
    eaten = Dish(1, "соус томатный", items=9)
    assert _rows(typed, eaten) == [(tidy.SAME, 1, 5, True)]


def test_then_the_most_eaten_then_the_shorter_then_the_older():
    assert _rows(Dish(1, "гречка", items=1), Dish(2, "гречка", items=5))[0][2] == 2
    assert _rows(Dish(1, "гречка отварная"), Dish(2, "гречка"))[0][2] == 2
    assert _rows(Dish(3, "гречка"), Dish(2, "гречка"))[0][2] == 2


def test_at_most_three_sources_per_target():
    dishes = [Dish(i, "суп") for i in range(1, 7)]
    rows = _rows(*dishes)
    assert len(rows) == 3 and {row[2] for row in rows} == {1}


def test_only_a_dish_pairing_the_target_itself_joins_it():
    # «a» pairs «a b», «a b» pairs «a b c d»; «a» and «a b c d» do not (3 words more).
    rows = _rows(
        Dish(1, "сырок", items=9),
        Dish(2, "сырок ваниль"),
        Dish(3, "сырок ваниль большой круглый"),
    )
    assert (tidy.MERGE, 2, 1, False) in rows
    assert all(row[1] != 3 for row in rows)


def test_the_caps_hold():
    dishes = [Dish(i, f"блюдо{i}") for i in range(1, 30)]
    dishes += [Dish(100 + i, f"блюдо{i}") for i in range(1, 30)]
    rows = _rows(*dishes)
    assert sum(1 for row in rows if row[0] == tidy.SAME) == tidy.SAME_CAP


# --- ⭐ ------------------------------------------------------------------------------


def _days(*ago):
    return frozenset(TODAY - timedelta(days=n) for n in ago)


def test_three_days_in_the_window_earn_a_star_and_one_day_does_not():
    assert _rows(Dish(1, "овсянка", items=3, days=_days(0))) == []
    assert _rows(Dish(1, "овсянка", items=3, days=_days(0, 3, 27))) == [
        (tidy.STAR, 1, None, True)
    ]
    # Day 28 back is outside the 28 days ending today.
    assert _rows(Dish(1, "овсянка", items=3, days=_days(0, 3, 28))) == []


def test_starred_or_once_unstarred_dishes_are_not_proposed():
    days = _days(0, 1, 2)
    assert _rows(Dish(1, "овсянка", days=days, starred=True)) == []
    assert _rows(Dish(1, "овсянка", days=days, star_removed=True)) == []


def test_a_star_is_counted_after_the_merges():
    # Two near-identical dishes, two days each: merged, they have four.
    one = Dish(1, "овсянка", items=2, days=_days(0, 1))
    other = Dish(2, "Овсянка", items=2, days=_days(5, 6))
    rows = _rows(one, other)
    assert rows[0][0] == tidy.SAME
    assert (tidy.STAR, rows[0][2], None, True) in rows


def test_a_proposed_source_is_not_also_proposed_for_a_star():
    one = Dish(1, "гречка", items=9)
    other = Dish(2, "гречка отварная", items=3, days=_days(0, 1, 2))
    rows = _rows(one, other)
    assert rows == [(tidy.MERGE, 2, 1, False)]


# --- declines ------------------------------------------------------------------------


def test_a_decline_holds_for_90_days_and_not_91():
    one, other = Dish(1, "гречка"), Dish(2, "гречка отварная")
    key, fingerprint = tidy.pair_key(1, 2), tidy.pair_fingerprint(one, other)
    held = {key: Decline(fingerprint, NOW - timedelta(days=90))}
    gone = {key: Decline(fingerprint, NOW - timedelta(days=91))}
    assert _rows(one, other, declines=held) == []
    assert _rows(one, other, declines=gone) == [(tidy.MERGE, 2, 1, False)]


def test_a_rename_reopens_a_declined_pair():
    one, other = Dish(1, "гречка"), Dish(2, "гречка отварная")
    declined = {tidy.pair_key(1, 2): Decline(tidy.pair_fingerprint(one, other), NOW)}
    renamed = Dish(2, "гречка рассыпчатая")
    assert _rows(one, renamed, declines=declined) == [(tidy.MERGE, 2, 1, False)]


def test_a_hand_unmerge_holds_whatever_changes():
    one, other = Dish(1, "гречка"), Dish(2, "гречка")
    declined = {tidy.pair_key(1, 2): Decline(None, NOW)}
    assert _rows(one, other, declines=declined) == []
    assert _rows(one, Dish(2, "гречка отварная"), declines=declined) == []


def test_a_declined_star_comes_back_only_when_it_changes():
    dish = Dish(1, "овсянка", kcal=90, days=_days(0, 1, 2))
    declined = {tidy.star_key(1): Decline(tidy.star_fingerprint(dish), NOW)}
    assert _rows(dish, declines=declined) == []
    changed = Dish(1, "овсянка", kcal=120, days=_days(0, 1, 2))
    assert _rows(changed, declines=declined) == [(tidy.STAR, 1, None, True)]
