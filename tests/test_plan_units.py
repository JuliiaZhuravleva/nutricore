"""One message, several actions: the parts with no rows and no model.

The grammar the planner answers in, the check of what it said against the message and
the dish book, the times of the parts, and the door (``wants_plan``).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services import plan_grammar as grammar
from app.services import plan_service
from app.services.meal_words import part_times, says_several
from app.services.plan_service import Book, Known, check
from app.services.plan_words import DISH, MEALS, wants_plan

ZONE = ZoneInfo("Asia/Tbilisi")


def _at(hour: int, minute: int = 0, day: int = 26) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=ZONE)


def _book(*dishes) -> Book:
    """``("гречка",)`` or ``("сырок глазированный", "сырки глазированные")``."""
    book = Book()
    for number, names in enumerate(dishes, start=1):
        dish_id = 100 + number
        known = Known(dish_id, names[0], tuple(names[1:]))
        book.known[dish_id] = known
        for name in known.names:
            book.names.setdefault(plan_service.norm(name), set()).add(dish_id)
        book.lines.append(dish_id)
    return book


# --- the grammar ---------------------------------------------------------------------


def test_one_registry_feeds_the_prompt_and_the_parser():
    docs = grammar.docs()
    for verb in grammar.VERBS:
        assert verb.syntax in docs
        parsed = grammar.parse(verb.example)
        if verb.name == grammar.NONE:
            assert parsed.none and not parsed.commands
        else:
            assert [c.verb for c in parsed.commands] == [verb.name], verb.example
    assert grammar.docs() in plan_service.SYSTEM


def test_prose_fences_and_bullets_are_not_commands():
    parsed = grammar.parse(
        'Here are the commands:\n```\n- meal "утром каша"\n2. meal «в обед суп»\n```'
    )
    assert [c.text for c in parsed.meals] == ["утром каша", "в обед суп"]
    assert parsed.bad == []


def test_a_command_line_that_does_not_parse_is_refused_with_a_reason():
    parsed = grammar.parse('star\nrename #2 "суп" "борщ"\nmerge #1 "суп"')
    assert parsed.commands == []
    assert [b.reason for b in parsed.bad] == [grammar.MALFORMED] * 3


def test_the_caps_keep_the_first_and_say_so():
    meals = "\n".join(f'meal "часть {n}"' for n in range(grammar.MEALS_MAX + 1))
    parsed = grammar.parse(meals)
    assert len(parsed.meals) == grammar.MEALS_MAX and parsed.over_meals
    stars = "\n".join(f'star #{n} "блюдо"' for n in range(1, grammar.DISH_MAX + 2))
    parsed = grammar.parse(stars)
    assert len(parsed.dish) == grammar.DISH_MAX and parsed.over_dish


def test_merge_reads_its_refs_and_into():
    (command,) = grammar.parse('merge #1 "сырки" #2 "сырок" into #2 "сырок"').commands
    assert [r.number for r in command.refs] == [1, 2]
    assert command.into == grammar.Ref(2, "сырок")


# --- the check: meals ----------------------------------------------------------------

DAY = "на завтрак овсянка, в обед суп, вечером сырники"


def test_three_verbatim_spans_are_three_meals_in_order():
    parsed = grammar.parse(
        'meal "в обед суп"\nmeal "на завтрак овсянка"\nmeal "вечером сырники"'
    )
    checked = check(parsed, DAY, _book())
    assert [p.text for p in checked.meals] == [
        "на завтрак овсянка",
        "в обед суп",
        "вечером сырники",
    ]
    assert checked.missed == [] and not checked.single


def test_case_yo_and_quotes_are_tolerated_a_paraphrase_is_not():
    text = "Утром съёл «Сырник», в обед суп"
    ok = check(
        grammar.parse('meal "утром съел сырник"\nmeal "в обед суп"'), text, _book()
    )
    assert [p.text for p in ok.meals] == ["Утром съёл «Сырник»", "в обед суп"]
    bad = check(grammar.parse('meal "утром сырники"\nmeal "в обед суп"'), text, _book())
    assert [p.text for p in bad.meals] == ["в обед суп"]
    assert bad.refused and plan_service.WHY_WORDS in bad.refused[0].why


def test_overlapping_spans_take_the_words_once():
    parsed = grammar.parse(
        'meal "на завтрак овсянка"\nmeal "овсянка, в обед суп"\nmeal "вечером сырники"'
    )
    checked = check(parsed, DAY, _book())
    assert "на завтрак овсянка" in [p.text for p in checked.meals]
    assert len(checked.meals) == 2  # the overlapping one is not a meal of its own


def test_food_the_planner_dropped_is_named_back():
    parsed = grammar.parse('meal "на завтрак овсянка"\nmeal "в обед суп"')
    checked = check(parsed, DAY, _book())
    assert any("сырники" in m for m in checked.missed)


def test_one_meal_and_nothing_else_is_single():
    checked = check(grammar.parse('meal "утром каша"'), "утром каша", _book())
    assert checked.single
    assert check(grammar.parse("none"), "утром каша", _book()).single


# --- the check: dishes ---------------------------------------------------------------


def test_a_dish_by_number_and_name_is_found():
    book = _book(("гречка",), ("борщ",))
    checked = check(
        grammar.parse('star #1 "гречка"'), "добавь гречку в избранное", book
    )
    assert [(a.verb, a.ids) for a in checked.acts] == [("star", (101,))]
    assert not checked.refused


def test_an_exact_name_beats_a_number_that_fits_by_prefix():
    book = _book(("сырники",), ("сыр",))
    checked = check(grammar.parse('hide #1 "сыр"'), "убери сыр из блюд", book)
    assert [(a.verb, a.ids) for a in checked.acts] == [("hide", (102,))]


def test_a_number_beyond_the_list_or_another_name_is_refused():
    book = _book(("гречка",))
    for line in ('star #9 "пицца"', 'star "пицца"'):
        checked = check(grammar.parse(line), "добавь пиццу в избранное", book)
        assert checked.acts == []
        assert checked.refused and plan_service.WHY_NONE in checked.refused[0].why


def test_a_name_that_fits_several_asks_to_be_precise():
    book = _book(("суп куриный",), ("суп грибной",))
    checked = check(grammar.parse('hide "суп"'), "убери суп из блюд", book)
    assert checked.acts == []
    (refusal,) = checked.refused
    assert "суп куриный" in refusal.why and "суп грибной" in refusal.why


def test_merge_without_into_keeps_one_of_them():
    book = _book(("сырок глазированный",), ("сырки глазированные",))
    checked = check(
        grammar.parse('merge #1 "сырок глазированный" #2 "сырки глазированные"'),
        "сырки глазированные — одно блюдо",
        book,
    )
    (act,) = checked.acts
    assert set(act.ids) == {101, 102} and act.target in (101, 102)


def test_merging_a_dish_with_itself_is_refused():
    book = _book(("сырок глазированный", "сырки глазированные"))
    checked = check(
        grammar.parse('merge #1 "сырок глазированный" "сырки глазированные"'),
        "сырки глазированные — одно блюдо",
        book,
    )
    assert checked.acts == []
    assert checked.refused and plan_service.WHY_ONE in checked.refused[0].why


def test_a_new_name_must_be_the_persons_words():
    book = _book(("орехи",))
    text = "переименуй орехи в орехи жареные"
    ok = check(grammar.parse('rename #1 "орехи" to "орехи жареные"'), text, book)
    assert [a.new_name for a in ok.acts] == ["орехи жареные"]
    bad = check(grammar.parse('rename #1 "орехи" to "миндаль"'), text, book)
    assert bad.acts == [] and bad.refused


# --- the times of the parts ----------------------------------------------------------


def _local(said) -> tuple:
    moment = said.moment.astimezone(ZONE)
    return moment.day, moment.hour, said.precision


def test_a_day_log_at_night_is_today_at_each_meals_hour():
    spans = ["на завтрак овсянка", "в обед суп", "вечером сырники"]
    times = part_times(spans, _at(21), ZONE)
    assert [_local(t) for t in times][:2] == [(26, 9, "approx"), (26, 13, "approx")]
    assert _local(times[2])[:1] == (26,)
    assert [t.rest for t in times] == ["овсянка", "суп", "сырники"]


def test_the_same_log_at_two_is_yesterday():
    spans = ["на завтрак овсянка", "в обед суп", "вечером сырники"]
    times = part_times(spans, _at(14), ZONE)
    assert {t.moment.astimezone(ZONE).day for t in times} == {25}


@pytest.mark.parametrize(
    "hour, minute, spans",
    [
        (12, 30, ["на завтрак каша", "на обед суп"]),
        (19, 30, ["на обед суп", "на ужин паста"]),
        (19, 0, ["утром каша", "вечером паста"]),
    ],
)
def test_a_meal_name_just_ahead_keeps_today(hour, minute, spans):
    first, second = part_times(spans, _at(hour, minute), ZONE)
    assert {t.moment.astimezone(ZONE).day for t in (first, second)} == {26}
    assert first.precision == "approx" and second.precision == "day_only"


def test_an_exact_clock_ahead_still_means_yesterday():
    times = part_times(["в 9:00 каша", "в 13:00 суп"], _at(12, 30), ZONE)
    assert {t.moment.astimezone(ZONE).day for t in times} == {25}


def test_yesterday_said_outright_is_yesterday():
    spans = ["вчера утром каша", "вечером суп"]
    times = part_times(spans, _at(22), ZONE)
    assert {t.moment.astimezone(ZONE).day for t in times} == {25}


def test_today_with_a_clock_still_ahead_keeps_the_day_alone():
    spans = ["сегодня утром каша", "вечером суп"]
    first, second = part_times(spans, _at(14), ZONE)
    assert _local(first) == (26, 9, "approx")
    assert second.moment.astimezone(ZONE).day == 26
    assert second.precision == "day_only"


def test_a_part_without_time_words_keeps_the_day_alone():
    first, second = part_times(["утром каша", "яблоко"], _at(21), ZONE)
    assert _local(first) == (26, 9, "approx")
    assert second.precision == "day_only" and second.moment.astimezone(ZONE).day == 26


def test_a_reminders_day_wins():
    day = (_at(21) - timedelta(days=2)).date()
    times = part_times(["утром каша", "в обед суп"], _at(10), ZONE, reminder_day=day)
    assert {t.moment.astimezone(ZONE).day for t in times} == {24}


# --- the door ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, want",
    [
        ("сырки глазированные — одно блюдо", DISH),
        ("добавь гречку в избранное", DISH),
        ("переименуй орехи в орехи жареные", DISH),
        ("убери пиццу из блюд", DISH),
        (DAY, MEALS),
        ("утром каша, вечером суп", MEALS),
        ("утром каша", None),
        ("гречка с котлетой", None),
        ("добавь гречку в избранное?", None),  # asked, not done
        ("утром суп, хлеб", None),  # one timed clause
    ],
)
def test_wants_plan(text, want):
    assert wants_plan(text, _at(21), ZONE) == want


def test_wants_plan_meals_is_r0s_own_rule():
    for text in (DAY, "утром суп, хлеб", "утром суп, в обед", "в 9:00 каша"):
        several = says_several(text, _at(21), ZONE)
        assert (wants_plan(text, _at(21), ZONE) == MEALS) == several, text


# --- the one retry (cases seen on the real model, 2026-11-14) ------------------------


def _asked(monkeypatch, *answers) -> list:
    """Fake planner: the given answers in turn; returns the list of prompts sent."""
    sent, left = [], list(answers)

    async def fake(svc, messages, telegram_id, input_ref):
        sent.append([m["content"] for m in messages])
        return {"answer": left.pop(0)}

    monkeypatch.setattr(plan_service, "_ask", fake)
    return sent


def _read(text, want, book):
    import asyncio

    return asyncio.run(
        plan_service.read(
            None, text, want, book, _at(21), telegram_id=None, input_ref="t"
        )
    )


def test_an_ambiguous_name_is_not_asked_again(monkeypatch):
    book = _book(("суп куриный",), ("суп грибной",))
    sent = _asked(monkeypatch, 'hide "суп"', 'hide #1 "суп куриный"\nhide #2 "суп"')
    checked = _read("убери суп из блюд", DISH, book)
    assert len(sent) == 1  # the live model hid both soups when asked again
    assert checked.acts == [] and "подходит несколько" in checked.refused[0].why


def test_a_dish_verb_answered_none_is_asked_once_more(monkeypatch):
    book = _book(("борщ",))
    sent = _asked(monkeypatch, "none", 'unstar #1 "борщ"')
    checked = _read("убери борщ из избранного", DISH, book)
    assert len(sent) == 2 and plan_service.NO_DISH_LINE in sent[1][-1]
    assert [a.verb for a in checked.acts] == ["unstar"]


def test_one_and_the_same_food_is_not_pushed_to_a_dish_line(monkeypatch):
    sent = _asked(monkeypatch, 'meal "утром и вечером одно и то же — овсянка"')
    checked = _read("утром и вечером одно и то же — овсянка", DISH, _book(("овсянка",)))
    assert len(sent) == 1 and checked.single


# --- what goes back to today's one-meal way ------------------------------------------


def test_food_said_one_and_the_same_goes_back_when_the_planner_says_none():
    checked = check(grammar.parse("none"), "весь день ел одно и то же", _book())
    assert plan_service.gives_back(DISH, "весь день ел одно и то же", checked)
    # a real dish verb answered none is not food: it gets «Не нашлось блюда»
    assert not plan_service.gives_back(DISH, "убери пиццу из блюд", checked)


def test_a_dish_message_with_garbled_dish_lines_is_a_failure_not_a_meal():
    parsed = grammar.parse('meal "суп"\nstar')
    checked = check(parsed, "суп, и добавь гречку в избранное", _book(("гречка",)))
    assert checked.bad == 1 and plan_service.unreadable(DISH, checked)
    assert not plan_service.unreadable(MEALS, checked)
