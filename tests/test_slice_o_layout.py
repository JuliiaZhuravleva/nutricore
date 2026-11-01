"""Layout and copy over slice O, by what a person can tap: 10.3's «Layout as in N».

A walk through the real application at the wire from the menu's ways in, on real rows
with the worst ids and names, taps one button of every callback shape it meets. A new
screen is checked for being reachable, not for being listed here. ``reader`` stands in
for the model, so nothing reaches the OpenAI client.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.crud.crud_meal_item import crud_meal_item
from app.models import FavoriteMeal, Meal, PersonalFood
from app.services import dishes, favorites, meal_card, next_step
from tests.test_capture_router import (  # noqa: F401 (fixtures)
    Session,
    db,
    owner,
    reader,
)
from tests.test_dishes import (  # noqa: F401 (fixtures)
    _data,
    _fits,
    _labels,
    _no_screen_from_another_test,
    _per_100,
    tap,
)
from tests.test_emoji_set import _is_mark, build_tiles
from tests.test_meal_card import _named_without_their_label
from tests.test_user_text import findings

# The worst ids the columns hold, with room for the rows the taps go on to make.
INT4 = 2**31 - 1 - 200
ZONE = ZoneInfo("Asia/Tbilisi")
LONG = "очень длинное название блюда на целый обед и ужин"
ROOFS = ("ml:", "dh:", "fav:", "st:")
_DRAWN = {mark for mark, _, _ in build_tiles.SET}


def _rows_at_the_worst(db, owner):
    now = datetime.now(UTC)
    today = now.astimezone(ZONE).date()
    foods, meals = [], []
    for index in range(14):
        food = PersonalFood(
            id=INT4 - index,
            user_id=owner.id,
            canonical_name=f"{LONG} {index}",
            usual_grams=200.0,
            **_per_100(),
        )
        db.add(food)
        foods.append(food)
    db.flush()
    for index, food in enumerate(foods[:3]):
        meal = Meal(
            id=INT4 - index,
            user_id=owner.id,
            description=food.canonical_name,
            meal_time=now - timedelta(days=index + 1),
        )
        db.add(meal)
        db.flush()
        crud_meal_item.add(
            db,
            meal_id=meal.id,
            name=food.canonical_name,
            personal_food_id=food.id,
            grams=200.0,
        )
        meals.append(meal)
    db.add(
        FavoriteMeal(
            id=INT4,
            user_id=owner.id,
            name=foods[0].canonical_name,
            personal_food_id=foods[0].id,
            calories=360.0,
            nutrients={"foods": [foods[0].canonical_name]},
        )
    )
    db.commit()
    return foods, meals, today


def _shape(data: str) -> str:
    return re.sub(r"\d+", "#", data)


def _walk(seeds: list) -> dict:
    """What each tap showed, by its data: all the seeds, and one tap of each shape."""
    queue, seen, tapped, shown = list(seeds), set(), set(), {}
    while queue and len(tapped) < 300:
        data = queue.pop(0)
        if data in tapped or (_shape(data) in seen and data not in seeds):
            continue
        seen.add(_shape(data))
        tapped.add(data)
        wire = tap(data)
        if not wire.edits():  # a toast, or a screen unchanged
            continue
        screen = wire.edits()[-1]
        shown[data] = screen
        for row in _data(screen):
            queue += [cb for cb in row if cb.startswith(ROOFS)]
    return shown


def _seeds(foods, meals, today: date) -> list:
    dish, other = foods[0].id, foods[1].id
    empty = f"{today - timedelta(days=20):%Y%m%d}"
    return [
        "dh:l:0",
        "dh:l:1",
        "dh:f",
        f"dh:o:{dish}:l0",
        f"dh:n:{dish}:1",
        f"dh:mo:{dish}:l0",
        f"dh:rn:{dish}",
        f"dh:nm:{dish}",
        f"dh:pg:{dish}",
        f"dh:gr:{dish}",
        f"dh:h:{dish}:0",
        f"dh:jl:{dish}:0",
        f"dh:jf:{dish}",
        f"dh:o:{dish}:i{INT4}-h{INT4}",
        f"dh:n:{dish}:1:i{INT4}-h{INT4}",
        "fav:p:0",
        f"fav:o:{INT4}:0",
        f"fav:g:{INT4}",
        "st:t",
        "st:y",
        "st:w",
        f"ml:dl:{empty}:w",
        f"ml:da:{empty}:w",
        f"ml:o:{meals[0].id}:w",
        f"ml:mo:{meals[0].id}:w",
        f"dh:log:{dish}",
        f"dh:j:{other}:{foods[2].id}",
        f"dh:x:{foods[3].id}",
    ]


@pytest.fixture(params=["ordinary", "hard day"])
def walked(request, db, owner, reader):
    if request.param == "hard day":
        owner.settings = {**owner.settings, next_step.ENERGY: next_step.SIMPLE}
        db.commit()
    foods, meals, today = _rows_at_the_worst(db, owner)
    return _walk(_seeds(foods, meals, today))


def _titles(shown: dict) -> str:
    return "\n".join(screen["text"].split("\n", 1)[0] for screen in shown.values())


def test_the_walk_reaches_every_screen_of_the_slice(walked):
    """Control for all below: a walk that stopped early would pass them for nothing."""
    titles = _titles(walked)
    for title in (
        "🍲 ",  # O2, O3
        "🔗 ",  # O7
        "🕘 ",  # O6
        "⚖️ Порция",  # O14a
        "🏷 Группы",  # O14b
        "🍲 Ещё",  # O14
        "✏️ Название",  # O4
        "✏️ Цифры",  # O5
        "⭐ ",  # O9, O11
        "🍽 Приёмы, ",  # O1
        "➕ Приём, ",  # O1b
        "📊 7 дней",  # O13a
    ):
        assert title in titles, title
    assert len(walked) > 40


def test_every_screen_fits_at_the_worst_names_and_ids(walked):
    for data, screen in walked.items():
        try:
            _fits(screen)
        except AssertionError as e:
            raise AssertionError(f"{data}: {e}") from e


def test_control_the_fit_refuses_each_breach():
    fine = {
        "reply_markup": {"inline_keyboard": [[{"text": "a", "callback_data": "b"}]]}
    }
    _fits(fine)
    for breach in (
        [[{"text": "a", "callback_data": "b"}]] * 6,
        [[{"text": "a", "callback_data": "b"}] * 4],
        [[{"text": "я" * 35, "callback_data": "b"}]],
        [[{"text": "я" * 21, "callback_data": "b"}] * 2],
        [[{"text": "я" * 14, "callback_data": "b"}] * 3],
        [[{"text": "a", "callback_data": "b" * 65}]],
    ):
        with pytest.raises(AssertionError):
            _fits({"reply_markup": {"inline_keyboard": breach}})


def _has_a_verb(data: str) -> bool:
    if data.startswith("st:"):
        return True  # the tabs' own tests read these
    return (
        data in (meal_card.NOP, favorites.NOP)
        or dishes.is_known_shape(data)
        or any(pattern.fullmatch(data) for pattern, _ in meal_card._VERBS)
        or any(pattern.fullmatch(data) for pattern, _ in favorites._VERBS)
    )


def test_every_button_drawn_answers_a_real_verb(walked):
    """By the verbs' own patterns: the walk changes rows, so some of what it taps later
    is rightly stale, and a stale button goes to today's card."""
    drawn = {cb for screen in walked.values() for row in _data(screen) for cb in row}
    assert {"dh:", "ml:", "fav:"} <= {cb[: cb.index(":") + 1] for cb in drawn}
    assert [data for data in drawn if not _has_a_verb(data)] == []
    # Controls: each table refuses what it has no verb for.
    for data in ("dh:zz:1", "ml:zz:1", "fav:zz:1", "ml:da:notaday:t"):
        assert not _has_a_verb(data), data
    assert _has_a_verb("ml:da:20261025:w")


def _marks(words: str) -> set:
    return {ch for ch in words if _is_mark(ch)}


def test_every_mark_on_a_screen_is_drawn(walked):
    seen = set()
    for screen in walked.values():
        seen |= _marks(screen["text"])
        seen |= {m for row in _labels(screen) for label in row for m in _marks(label)}
    assert {"🍲", "⬅", "➕", "🔗"} <= seen  # the scan sees them
    assert seen - _DRAWN == set()


def _pagers(screen) -> list:
    return [row for row in _labels(screen) if row[:1] == ["◀️"]]


def test_a_list_of_more_than_one_page_says_where_it_is(walked):
    assert any(_pagers(screen) for screen in walked.values())  # control
    for data, screen in walked.items():
        for pager in _pagers(screen):
            if "ml:op:" in str(_data(screen)):
                continue  # N1's chips: the text lists every dish
            # O2, O7: ◀️ 🔍 ▶️ and the place in the text, as the spec draws them.
            told = re.search(r"Страница \d+ из \d+\.", screen["text"])
            assert told or re.fullmatch(r"\d+/\d+", pager[1]), (data, pager)
            assert pager[2] == "▶️", (data, pager)
            # Under the rows it pages, with at most the way back below it.
            assert pager in _labels(screen)[-2:], (data, _labels(screen))


def _sentences(screen) -> str:
    """Without a card's data lines, which lead with their own mark (🕐 13:20, 🍽 Рис);
    O9's ✏️ is a button with no words, so that sentence does name it whole."""
    lines = screen["text"].replace("Кнопка ✏️:", "Кнопка:").split("\n")
    return "\n".join(line for line in lines if not _is_mark(line[:1]))


def test_copy_no_short_name_of_a_button_no_gender_no_verdict(walked):
    labels = {
        label
        for screen in walked.values()
        for row in _labels(screen)
        for label in row
        if not label.startswith("⭐ ")  # a mark of state on a picked dish
    }
    assert {"🔗 Объединить", "🗑 Из списков"} <= labels  # the walk saw them
    assert "🗑 Убрать" not in labels and "⬅️ Блюда" not in labels
    for data, screen in walked.items():
        said = "\n".join([screen["text"], *[x for row in _labels(screen) for x in row]])
        assert findings(data, said) == [], data
        assert _named_without_their_label(_sentences(screen), labels) == [], data
        for line in screen["text"].split("\n"):
            if "🔥" in line:
                assert "🔥 ≈" in line, line


def test_control_the_copy_scan_sees_what_it_refuses():
    assert findings("x", "Записала, это слишком")
    assert _named_without_their_label("Заголовок\nЖми «Объединить».", {"🔗 Объединить"})
    # The scan still sees a mark in a sentence after the data lines are gone.
    typed = {"text": "Заголовок\n🔗 Рис\nЖми 🔗 и всё."}
    assert _named_without_their_label(_sentences(typed), {"🔗 Объединить"})
    assert not _named_without_their_label(
        _sentences({"text": "🔗 Рис"}), {"🔗 Объединить"}
    )


def test_a_day_add_shows_no_count_or_verdict_at_all(db, owner, reader):
    # O1a: an empty day is shown as empty; no slots, no totals.
    screen = tap("ml:dl:20261025:t").edits()[-1]
    assert not re.search(r"\d+ (раз|при[её]м)", screen["text"])
    assert "ккал" not in screen["text"]
