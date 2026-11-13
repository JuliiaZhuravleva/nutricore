"""The dish base of section 6: ``dh:`` screens, from the menu and from a meal's dish.

On real rows, through the real application at the wire (``create_bot_application``):
what is asserted is what would reach Telegram. ``reader`` stands in for the model and is
asserted not to be asked where a screen promises no read; nothing here reaches the
OpenAI client.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.crud.crud_favorite_meal import crud_favorite_meal
from app.crud.crud_meal_item import crud_meal_item
from app.crud.crud_personal_food import crud_personal_food
from app.models import FavoriteMeal, Meal, MealItem, PersonalFood, RecordEvent, User
from app.services import (
    capture,
    close_row,
    dish_emoji,
    dishes,
    meal_card,
    meal_items,
    next_step,
)
from app.services import telegram as tg
from app.services.numbers import SHOW_NUMBERS
from tests.test_capture_corrections import tap as _tap
from tests.test_capture_router import (  # noqa: F401 (fixtures)
    OWNER,
    Session,
    db,
    owner,
    reader,
    send,
    text,
)

WED = datetime(
    2026, 10, 28, 4, 40, tzinfo=UTC
)  # ср 28 октября, 8:40 in the owner's zone
TUE = datetime(2026, 10, 27, 5, 10, tzinfo=UTC)  # вт 27 октября, 9:10
SAT = datetime(2026, 10, 24, 6, 0, tzinfo=UTC)  # сб 24 октября
GONE = datetime(2026, 10, 1, tzinfo=UTC)
ESTIMATE = "Это оценка, цифры примерные."
EARLY = "Ранние записи могут здесь не показываться: в них блюда не разделены."


@pytest.fixture(autouse=True)
def _no_screen_from_another_test(monkeypatch):
    """The delivery remembers what a message shows, and skips an unchanged edit."""
    monkeypatch.setattr(tg._card_delivery, "_screens", {})


def tap(data: str, message_id: int = 4900):
    """Each tap builds its own application at its own wire. The delivery remembers a
    message with the bot it came through, so it would edit through the last tap's."""
    tg._card_delivery._screens.clear()
    return _tap(data, message_id)


@pytest.fixture
def stranger(db):
    user = User(telegram_id=77)
    db.add(user)
    db.commit()
    return user


@pytest.fixture
def hard_day(db, owner):
    owner.settings = {**owner.settings, next_step.ENERGY: next_step.SIMPLE}
    db.commit()


def _dish(db, owner, name, **columns) -> PersonalFood:
    dish = PersonalFood(user_id=owner.id, canonical_name=name, **columns)
    db.add(dish)
    db.commit()
    return dish


def _per_100(kcal=180, proteins=12, fats=14, carbs=1, **more) -> dict:
    return {
        "per_100g_calories": kcal,
        "per_100g_proteins": proteins,
        "per_100g_fats": fats,
        "per_100g_carbs": carbs,
        "numbers_basis": "per_100",
        **more,
    }


def _eat(db, owner, dish, when=WED, *, name=None, description=None, **fields):
    meal = Meal(
        user_id=owner.id,
        description=description or dish.canonical_name,
        meal_time=when,
    )
    for key in ("time_precision", "deleted_at", "is_draft"):
        if key in fields:
            setattr(meal, key, fields.pop(key))
    db.add(meal)
    db.flush()
    item = crud_meal_item.add(
        db,
        meal_id=meal.id,
        name=name or dish.canonical_name,
        personal_food_id=dish.id,
        **fields,
    )
    db.commit()
    return meal, item


def _legacy(db, owner, description, when=SAT) -> Meal:
    meal = Meal(user_id=owner.id, description=description, meal_time=when)
    db.add(meal)
    db.commit()
    return meal


def _star(db, owner, dish, calories=360.0) -> FavoriteMeal:
    fav = FavoriteMeal(
        user_id=owner.id,
        name=dish.canonical_name,
        personal_food_id=dish.id,
        calories=calories,
        nutrients={"foods": [dish.canonical_name]},
    )
    db.add(fav)
    db.commit()
    return fav


def _kb(params: dict) -> list:
    markup = params.get("reply_markup")
    if markup is None:
        return []
    if isinstance(markup, str):
        markup = json.loads(markup)
    elif not isinstance(markup, dict):
        markup = markup.to_dict()
    return markup["inline_keyboard"]


def _text(button: dict) -> str:
    """The label as read: the no-break space after a dish's emoji reads as a space
    (``test_dish_emoji`` checks it is there)."""
    return button["text"].replace(dish_emoji.SEP, " ")


def _labels(params: dict) -> list:
    return [[_text(b) for b in row] for row in _kb(params)]


def _data(params: dict) -> list:
    return [[b["callback_data"] for b in row] for row in _kb(params)]


def _on(params: dict, label: str) -> str:
    for row in _kb(params):
        for button in row:
            if _text(button) == label:
                return button["callback_data"]
    raise AssertionError(f"no {label!r} in {_labels(params)}")


def _shown(data: str) -> dict:
    return tap(data).edits()[-1]


def _toast(wire) -> str | None:
    answers = [p for n, p in wire.calls if n == "answerCallbackQuery"]
    return answers[-1].get("text") if answers else None


def _typed_reply(body: str) -> dict:
    return send(text(body)).sent()[-1]


def _meals(db, owner) -> list:
    db.expire_all()
    return list(
        db.scalars(
            select(Meal).where(Meal.user_id == owner.id, Meal.deleted_at.is_(None))
        )
    )


def _fits(params: dict) -> None:
    rows = _labels(params)
    assert len(rows) <= 5, rows
    for row in rows:
        assert len(row) <= 3, rows
        budget = {1: 34, 2: 20, 3: 13}[len(row)]
        for label in row:
            assert len(label) <= budget, (label, rows)
    for row in _data(params):
        for data in row:
            assert len(data.encode()) <= 64, data


# --- O2 the list ----------------------------------------------------------------


def test_the_menu_keyboard_has_the_dishes_next_to_stats():
    assert [b.text for b in tg.main_keyboard.keyboard[1]] == [
        "📊 Статистика",
        "🍲 Блюда",
    ]


def test_o2a_empty_says_they_come_by_themselves(db, owner, reader):
    shown = send(text("🍲 Блюда")).sent()[-1]
    assert shown["text"] == (
        "<b>🍲 Блюда</b>\n"
        "Пока пусто. Блюда появятся сами, когда еда будет записываться."
    )
    assert _kb(shown) == []
    assert reader.text == []
    assert _meals(db, owner) == []  # the label is no meal


def test_o2_lists_by_latest_live_item_with_counts_and_stars(db, owner, reader):
    rice = _dish(db, owner, "рис")
    omelette = _dish(db, owner, "омлет с сыром")
    soup = _dish(db, owner, "суп с курицей")
    oats = _dish(db, owner, "овсянка")
    for _ in range(3):
        _eat(db, owner, rice, WED)
    for _ in range(12):
        _eat(db, owner, omelette, TUE)
    for _ in range(2):
        _eat(db, owner, soup, SAT)
    _star(db, owner, omelette)

    shown = send(text("🍲 Блюда")).sent()[-1]
    assert shown["text"] == "<b>🍲 Блюда</b>\nСначала недавние."
    assert _labels(shown) == [
        ["🍚 Рис · 3 раза"],
        ["⭐ 🍳 Омлет с сыром · 12 раз"],
        ["🍲 Суп с курицей · 2 раза"],
        ["🥣 Овсянка"],  # no live item: the name only
        ["🔍 Найти", "🍲 Порядок"],  # one page: no pager; 🍲 from two dishes
    ]
    assert _data(shown) == [
        [f"dh:o:{rice.id}:l0"],
        [f"dh:o:{omelette.id}:l0"],
        [f"dh:o:{soup.id}:l0"],
        [f"dh:o:{oats.id}:l0"],
        ["dh:f", "dt:o"],
    ]
    assert reader.text == []


def test_o2_counts_only_live_items_of_kept_meals(db, owner):
    rice = _dish(db, owner, "рис")
    _eat(db, owner, rice, WED)  # the one that counts
    _eat(db, owner, rice, TUE, deleted_at=GONE)
    _eat(db, owner, rice, TUE, is_draft=True)
    _eat(db, owner, rice, TUE, removed_at=GONE)
    shown = _shown("dh:l:0")
    assert _labels(shown)[0] == ["🍚 Рис · 1 раз"]


def test_o2_leaves_out_hidden_and_merged_with_a_control(db, owner):
    target = _dish(db, owner, "кефир")
    _dish(db, owner, "айран", hidden_at=GONE)
    _dish(db, owner, "тан", merged_into_id=target.id)
    labels = _labels(_shown("dh:l:0"))
    assert labels == [["🥛 Кефир"], ["🔍 Найти"]]  # control: a live one stays


def test_o2_star_goes_by_the_link_not_by_the_name(db, owner):
    rice = _dish(db, owner, "рис")
    fav = _star(db, owner, rice)
    fav.name = "мой рис"  # a favourite may be called otherwise
    db.commit()
    assert _labels(_shown("dh:l:0"))[0] == ["⭐ 🍚 Рис"]
    # Control: a removed star marks nothing.
    fav.removed_at = GONE
    db.commit()
    tg._card_delivery._screens.clear()
    assert _labels(_shown("dh:l:0"))[0] == ["🍚 Рис"]


def test_o2_a_long_name_is_cut_and_its_count_stays_in_the_budget(db, owner):
    dish = _dish(db, owner, "очень длинное название одного блюда на обед")
    _star(db, owner, dish)
    for _ in range(12):
        _eat(db, owner, dish)
    shown = _shown("dh:l:0")
    label = _labels(shown)[0][0]
    assert label.startswith("⭐ 🍽 Очень")
    assert label.endswith("… · 12 раз")
    _fits(shown)


@pytest.mark.parametrize(
    "times, word",
    [(1, "раз"), (2, "раза"), (4, "раза"), (5, "раз"), (11, "раз"), (21, "раз")],
)
def test_the_count_of_times_reads_as_russian(db, owner, times, word):
    assert dishes._times(times) == word


def test_o2_pages_say_where_they_are_and_a_page_past_the_end_shows_the_last(db, owner):
    made = [_dish(db, owner, f"блюдо {i}") for i in range(9)]
    for index, dish in enumerate(made):
        _eat(db, owner, dish, WED - timedelta(hours=index))

    first = send(text("🍲 Блюда")).sent()[-1]
    assert first["text"] == "<b>🍲 Блюда</b>\nСначала недавние. Страница 1 из 3."
    assert _labels(first)[-1] == ["🔍 Найти", "🍲 Порядок", "▶️"]
    assert _data(first)[-1] == ["dh:f", "dt:o", "dh:l:1"]
    assert _labels(first)[0] == ["🍽 Блюдо 0 · 1 раз"]

    middle = _shown("dh:l:1")
    assert "Страница 2 из 3." in middle["text"]
    assert _labels(middle)[-1] == ["◀️", "🔍 Найти", "▶️"]
    assert _data(middle)[-1] == ["dh:l:0", "dh:f", "dh:l:2"]
    assert _data(middle)[0] == [f"dh:o:{made[4].id}:l1"]  # the page travels

    past = _shown("dh:l:99")
    assert "Страница 3 из 3." in past["text"]
    assert _labels(past)[0] == ["🍽 Блюдо 8 · 1 раз"]
    assert _labels(past)[-1] == ["◀️", "🔍 Найти"]
    _fits(middle)


def test_o2h_hides_the_counts_on_a_hard_day(db, owner, hard_day):
    rice = _dish(db, owner, "рис")
    omelette = _dish(db, owner, "омлет")
    _eat(db, owner, rice, WED)
    _eat(db, owner, omelette, TUE)
    _star(db, owner, omelette)
    shown = _shown("dh:l:0")
    assert _labels(shown)[:2] == [["🍚 Рис"], ["⭐ 🍳 Омлет"]]
    # Control: the same rows on an ordinary day carry them.
    owner.settings = {k: v for k, v in owner.settings.items() if k != next_step.ENERGY}
    db.commit()
    tg._card_delivery._screens.clear()
    assert _labels(_shown("dh:l:0"))[0] == ["🍚 Рис · 1 раз"]


# --- what a bad button gets -----------------------------------------------------


def test_a_gone_dish_redraws_the_list_with_its_notice(db, owner):
    rice = _dish(db, owner, "рис")
    shown = _shown(f"dh:o:{rice.id + 1000}:l0")
    assert shown["text"].startswith(
        "<i>Этого блюда уже нет в списке.</i>\n\n<b>🍲 Блюда"
    )
    assert _labels(shown)[0] == ["🍚 Рис"]


@pytest.mark.parametrize(
    "verb",
    [
        "dh:o:{id}:l0",
        "dh:mo:{id}:l0",
        "dh:rn:{id}",
        "dh:nm:{id}",
        "dh:n:{id}:1",
        "dh:h:{id}:0",
        "dh:fv:{id}:1",
        "dh:log:{id}",
        "dh:x:{id}",
        "dh:jl:{id}:0",
        "dh:jf:{id}",
        "dh:j:{id}:{mine}",
        "dh:j:{mine}:{id}",
    ],
)
def test_a_foreign_dish_gets_todays_card_and_a_warning(
    db, owner, stranger, caplog, verb
):
    theirs = _dish(db, stranger, "чужое")
    mine = _dish(db, owner, "моё")
    with caplog.at_level(logging.WARNING, logger="app.services.dishes"):
        shown = _shown(verb.format(id=theirs.id, mine=mine.id))
    assert "чужое" not in shown["text"].lower()
    assert "<b>📊" in shown["text"]
    assert "unknown or foreign" in caplog.text
    db.expire_all()
    assert db.get(PersonalFood, theirs.id).hidden_at is None
    assert db.get(PersonalFood, theirs.id).merged_into_id is None
    assert db.get(PersonalFood, mine.id).merged_into_id is None
    assert meal_card.has_slot(db, OWNER) is False


@pytest.mark.parametrize(
    "data",
    [
        "dh:o:abc:l0",
        "dh:o:1:z9",
        "dh:zz:1",
        "dh:l:x",
        f"dh:o:{10**15}:l0",
        "dh:u:abc",
    ],
)
def test_malformed_data_gets_todays_card_and_a_warning(db, owner, caplog, data):
    with caplog.at_level(logging.WARNING, logger="app.services.dishes"):
        shown = _shown(data)
    assert "<b>📊" in shown["text"]
    assert "unknown or foreign" in caplog.text


def test_control_an_own_dish_answers_without_a_warning(db, owner, caplog):
    rice = _dish(db, owner, "рис")
    with caplog.at_level(logging.WARNING, logger="app.services.dishes"):
        shown = _shown(f"dh:o:{rice.id}:l0")
    assert shown["text"].startswith("<b>🍲 Рис</b>")
    assert "unknown or foreign" not in caplog.text


def test_a_foreign_event_is_not_undone(db, owner, stranger, caplog):
    theirs = _dish(db, stranger, "чужое", hidden_at=GONE)
    event = RecordEvent(
        user_id=stranger.id,
        kind="food_hide",
        food_id=theirs.id,
        before={"hidden_at": None, "favourites": []},
        after={"hidden_at": 1.0, "favourites": []},
    )
    db.add(event)
    db.commit()
    with caplog.at_level(logging.WARNING, logger="app.services.dishes"):
        shown = _shown(f"dh:u:{event.id}")
    assert "<b>📊" in shown["text"]
    assert "unknown or foreign" in caplog.text
    db.expire_all()
    assert db.get(PersonalFood, theirs.id).hidden_at is not None


# --- O2b, O2c, O2d search -------------------------------------------------------


def test_o2b_asks_and_the_answer_is_a_search_not_a_meal(db, owner, reader):
    rice = _dish(db, owner, "Рис")
    with_veg = _dish(db, owner, "рис с овощами")
    _dish(db, owner, "суп")
    _eat(db, owner, rice, WED)
    _eat(db, owner, with_veg, TUE)

    asked = _shown("dh:f")
    assert asked["text"] == "<b>🔍 Найти блюдо</b>\nНапиши название."
    assert _labels(asked) == [["⬅️ Все блюда"]]
    assert _data(asked) == [["dh:l:0"]]

    before = len(_meals(db, owner))
    found = _typed_reply("РИС")  # Cyrillic case, in Python: SQLite's lower() is ASCII
    assert found["text"] == "<b>🍲 Блюда</b>\nНашлось по «РИС»:"
    assert _labels(found) == [
        ["🍚 Рис · 1 раз"],
        ["🍚 Рис с овощами · 1 раз"],
        ["⬅️ Все блюда"],
    ]
    assert _data(found)[0] == [f"dh:o:{rice.id}:l0"]
    assert reader.text == []
    assert len(_meals(db, owner)) == before
    assert meal_card.has_slot(db, OWNER) is False  # found: the wait is over


def test_o2d_nothing_found_keeps_the_slot_open(db, owner, reader):
    _dish(db, owner, "рис")
    tap("dh:f")
    missed = _typed_reply("борщ")
    assert missed["text"] == (
        "<b>🔍 Найти блюдо</b>\nПо «борщ» ничего не нашлось. Напиши иначе."
    )
    assert _labels(missed) == [["⬅️ Все блюда"]]
    assert meal_card.has_slot(db, OWNER) is True
    found = _typed_reply("рис")
    assert found["text"] == "<b>🍲 Блюда</b>\nНашлось по «рис»:"
    assert reader.text == []
    assert _meals(db, owner) == []


def test_search_finds_by_an_old_name_and_never_a_hidden_dish(db, owner):
    _dish(db, owner, "омлет", aliases=["яичница"])
    _dish(db, owner, "яичный салат", hidden_at=GONE)
    tap("dh:f")
    found = _typed_reply("яичн")
    assert _labels(found) == [["🍳 Омлет"], ["⬅️ Все блюда"]]


def test_search_escapes_what_was_typed(db, owner):
    tap("dh:f")
    missed = _typed_reply("<b>x</b>")
    assert "По «&lt;b&gt;x&lt;/b&gt;» ничего" in missed["text"]


def test_search_shows_no_more_rows_than_the_layout_allows(db, owner):
    for index in range(7):
        _dish(db, owner, f"рис {index}")
    tap("dh:f")
    found = _typed_reply("рис")
    _fits(found)
    assert len(_labels(found)) == 5


def test_o2c_hides_counts_on_a_hard_day(db, owner, hard_day):
    rice = _dish(db, owner, "рис")
    _eat(db, owner, rice)
    tap("dh:f")
    assert _labels(_typed_reply("рис"))[0] == ["🍚 Рис"]


def test_any_tap_closes_the_search_and_the_next_text_is_food(db, owner, reader):
    _dish(db, owner, "рис")
    tap("dh:f")
    assert meal_card.has_slot(db, OWNER) is True  # the dh: tap itself does not close it
    tap("dh:l:0")
    db.expire_all()
    assert meal_card.has_slot(db, OWNER) is False
    send(text("рис с овощами"))
    assert len(_meals(db, owner)) == 1


def test_a_tap_elsewhere_closes_a_dish_slot(db, owner):
    tap("dh:f")
    tap("fav:p:0")
    db.expire_all()
    assert meal_card.has_slot(db, OWNER) is False


def test_the_pager_middle_keeps_the_slot_and_changes_nothing(db, owner):
    tap("dh:f")
    wire = tap("dh:nop")
    assert wire.edits() == []
    db.expire_all()
    assert meal_card.has_slot(db, OWNER) is True


def test_the_menu_label_is_no_search_answer(db, owner, reader):
    _dish(db, owner, "рис")
    tap("dh:f")
    shown = _typed_reply("🍲 Блюда")
    assert shown["text"] == "<b>🍲 Блюда</b>\nСначала недавние."
    db.expire_all()
    assert meal_card.has_slot(db, OWNER) is False


def test_a_search_is_over_after_ten_minutes(db, owner, reader):
    tap("dh:f")
    db.expire_all()
    user = db.get(User, owner.id)
    ask = dict(user.settings[meal_card.ASK])
    ask["at"] = (datetime.now(UTC) - timedelta(minutes=11)).isoformat()
    user.settings = {**user.settings, meal_card.ASK: ask}
    db.commit()
    send(text("рис с овощами"))
    assert len(_meals(db, owner)) == 1  # food, not a search


def test_a_photo_in_a_search_is_still_a_meal_and_closes_it(db, owner, reader):
    from tests.test_capture_router import photo

    tap("dh:f")
    send(photo())
    db.expire_all()
    assert meal_card.has_slot(db, OWNER) is False
    assert len(_meals(db, owner)) == 1


# --- O3 the card ----------------------------------------------------------------


def test_o3_closed_numbers_exact_screen(db, owner, reader):
    dish = _dish(db, owner, "омлет с сыром", **_per_100(), usual_grams=200.0)
    for when in [WED] + [TUE] * 11:
        _eat(db, owner, dish, when)
    _star(db, owner, dish)
    shown = _shown(f"dh:o:{dish.id}:l0")
    assert shown["text"] == (
        "<b>🍲 Омлет с сыром</b>\nБыло 12 раз, последний раз ср 28 октября."
    )
    assert _labels(shown) == [
        ["🍽 Записать", "✓ ⭐ В избранном"],
        ["🕘 Когда было", "🔢 Цифры"],
        ["Ещё ›"],
        ["⬅️ Все блюда"],
    ]
    assert _data(shown) == [
        [f"dh:log:{dish.id}", f"dh:fv:{dish.id}:0"],
        [f"dh:h:{dish.id}:0", f"dh:n:{dish.id}:1"],
        [f"dh:mo:{dish.id}:l0"],
        ["dh:l:0"],
    ]
    assert reader.text == []
    _fits(shown)


def test_o3a_numbers_open_exact_screen(db, owner):
    dish = _dish(db, owner, "омлет с сыром", **_per_100(), usual_grams=200.0)
    for when in [WED] + [TUE] * 11:
        _eat(db, owner, dish, when)
    _star(db, owner, dish)
    shown = _shown(f"dh:n:{dish.id}:1")
    assert shown["text"] == (
        "<b>🍲 Омлет с сыром</b>\n"
        "Обычная порция: 200 г.\n"
        "Было 12 раз, последний раз ср 28 октября.\n"
        "<blockquote expandable>На 100 г: 🔥 ≈ 180 ккал · Б 12 · Ж 14 · У 1</blockquote>\n"
        f"{ESTIMATE}"
    )
    assert _labels(shown) == [
        ["🍽 Записать", "✓ ⭐ В избранном"],
        ["🕘 Когда было", "✓ 🔢 Цифры"],
        ["✏️ Править цифры", "Ещё ›"],
        ["⬅️ Все блюда"],
    ]
    assert _on(shown, "✓ 🔢 Цифры") == f"dh:n:{dish.id}:0"
    assert _on(shown, "✏️ Править цифры") == f"dh:nm:{dish.id}"
    _fits(shown)
    closed = _shown(f"dh:n:{dish.id}:0")
    assert "blockquote" not in closed["text"]
    assert "🔢 Цифры" in _labels(closed)[1]


def test_o3a_typed_numbers_go_without_the_estimate_mark(db, owner):
    dish = _dish(db, owner, "омлет", **_per_100(numbers_quality="user"))
    _eat(db, owner, dish)
    shown = _shown(f"dh:n:{dish.id}:1")
    assert "На 100 г: 🔥 180 ккал · Б 12 · Ж 14 · У 1" in shown["text"]
    assert "≈" not in shown["text"]
    assert ESTIMATE not in shown["text"]


def test_a_number_prints_the_same_at_any_size(db, owner):
    dish = _dish(db, owner, "сало", **_per_100(kcal=3000))
    shown = _shown(f"dh:n:{dish.id}:1")
    assert "🔥 ≈ 3000 ккал" in shown["text"]


def test_o3b_a_portion_with_no_weight(db, owner):
    dish = _dish(db, owner, "омлет с сыром")
    for _ in range(12):
        _eat(db, owner, dish, WED, numbers_source="none")
    _legacy(db, owner, "омлет с сыром, хлеб")
    _star(db, owner, dish)
    shown = _shown(f"dh:n:{dish.id}:1:f7")
    assert shown["text"] == (
        "<b>🍲 Омлет с сыром</b>\n"
        "Было 12 раз или больше: не все ранние записи здесь считаются.\n"
        "<blockquote expandable>Цифры на порцию, без веса: ≈ 360 ккал.</blockquote>"
    )
    assert _labels(shown) == [
        ["🍽 Записать", "✓ ⭐ В избранном"],
        ["🕘 Когда было", "✓ 🔢 Цифры"],
        ["✏️ Править цифры", "Ещё ›"],
        ["⬅️ Избранное"],
    ]
    assert _on(shown, "⬅️ Избранное") == "fav:p:0"
    _fits(shown)


def test_o3c_no_numbers_no_meals_no_when(db, owner):
    dish = _dish(db, owner, "хлеб")
    closed = _shown(f"dh:o:{dish.id}:l0")
    assert closed["text"] == "<b>🍲 Хлеб</b>\nПока без приёмов."
    assert _labels(closed) == [
        ["🍽 Записать", "⭐ В избранное"],
        ["🔢 Цифры"],
        ["Ещё ›"],
        ["⬅️ Все блюда"],
    ]
    shown = _shown(f"dh:n:{dish.id}:1")
    assert shown["text"] == (
        "<b>🍲 Хлеб</b>\n"
        "Пока без приёмов.\n"
        "<blockquote expandable>Цифр пока нет.</blockquote>"
    )
    assert _labels(shown) == [
        ["🍽 Записать", "⭐ В избранное"],
        ["✓ 🔢 Цифры", "✏️ Править цифры"],
        ["Ещё ›"],
        ["⬅️ Все блюда"],
    ]
    assert _on(shown, "⭐ В избранное") == f"dh:fv:{dish.id}:1"
    # Control: one meal brings the button.
    _eat(db, owner, dish)
    assert "🕘 Когда было" in str(_labels(_shown(f"dh:o:{dish.id}:l0")))


def test_o3h_hard_day_exact_screen(db, owner, hard_day):
    dish = _dish(
        db, owner, "омлет с сыром", usual_grams=200.0, usual_grams_by_user=True
    )
    for when in [WED] + [TUE] * 11:
        _eat(db, owner, dish, when)
    _star(db, owner, dish)
    shown = _shown(f"dh:o:{dish.id}:l0")
    assert shown["text"] == ("<b>🍲 Омлет с сыром</b>\nПоследний раз ср 28 октября.")
    assert _labels(shown) == [
        ["🍽 Записать", "✓ ⭐ В избранном"],
        ["⬅️ Все блюда"],
    ]


def test_the_typed_portion_shows_with_numbers_closed_a_model_one_does_not(db, owner):
    typed = _dish(db, owner, "омлет", usual_grams=200.0, usual_grams_by_user=True)
    model = _dish(db, owner, "суп", usual_grams=300.0, usual_grams_by_user=False)
    assert "Обычная порция: 200 г." in _shown(f"dh:o:{typed.id}:l0")["text"]
    assert "Обычная порция" not in _shown(f"dh:o:{model.id}:l0")["text"]


def test_with_the_numbers_setting_on_they_are_open_and_no_toggle(db, owner):
    owner.settings = {**owner.settings, SHOW_NUMBERS: True}
    db.commit()
    dish = _dish(db, owner, "омлет", **_per_100())
    _eat(db, owner, dish)
    shown = _shown(f"dh:o:{dish.id}:l0")
    assert "На 100 г:" in shown["text"]
    assert "🔢 Цифры" not in str(_labels(shown))
    assert "✏️ Править цифры" in str(_labels(shown))


def test_when_numbers_are_hidden_no_edit_is_offered(db, owner):
    dish = _dish(db, owner, "омлет", **_per_100())
    assert "✏️ Править цифры" not in str(_labels(_shown(f"dh:o:{dish.id}:l0")))


@pytest.mark.parametrize(
    "token, label, target",
    [
        ("l2", "⬅️ Все блюда", "dh:l:2"),
        ("f9", "⬅️ Избранное", "fav:p:0"),
        ("i55", "⬅️ К блюду в приёме", "ml:i:55:r"),
        ("i55-t", "⬅️ К блюду в приёме", "ml:i:55:t"),
        ("i55-h7", "⬅️ К блюду в приёме", "ml:i:55:h7"),
    ],
)
def test_the_back_row_goes_by_the_token_also_after_nested_taps(
    db, owner, token, label, target
):
    dish = _dish(db, owner, "омлет")
    _eat(db, owner, dish)
    shown = _shown(f"dh:o:{dish.id}:{token}")
    assert _labels(shown)[-1] == [label]
    assert _data(shown)[-1] == [target]
    # Through the numbers, the star, the history and Ещё, and back.
    opened = _shown(_on(shown, "🔢 Цифры"))
    assert _data(opened)[-1] == [target]
    starred = _shown(_on(opened, "⭐ В избранное"))
    assert _data(starred)[-1] == [target]
    history = _shown(_on(starred, "🕘 Когда было"))
    assert _shown(_on(history, "⬅️ К блюду"))["text"].startswith("<b>🍲 Омлет</b>")
    assert _data(_shown(_on(history, "⬅️ К блюду")))[-1] == [target]
    more = _shown(_on(starred, "Ещё ›"))
    name = _shown(_on(more, "✏️ Название"))
    assert _data(_shown(_on(name, "⬅️ К блюду")))[-1] == [target]


# --- times eaten (3.3) ----------------------------------------------------------


def test_counts_add_legacy_meals_of_the_same_whole_description(db, owner):
    dish = _dish(db, owner, "Омлет")
    _eat(db, owner, dish, TUE)
    _legacy(db, owner, "омлет", WED)  # equal in Python, Cyrillic case
    _legacy(db, owner, "ОМЛЕТ", SAT)
    shown = _shown(f"dh:o:{dish.id}:l0")["text"]
    assert "Было 3 раза, последний раз ср 28 октября." in shown


def test_a_legacy_meal_holding_more_than_the_name_says_or_more(db, owner):
    dish = _dish(db, owner, "омлет")
    _eat(db, owner, dish, TUE)
    _legacy(db, owner, "омлет и кофе", WED)
    shown = _shown(f"dh:o:{dish.id}:l0")["text"]
    assert "Было 1 раз или больше: не все ранние записи здесь считаются." in shown


def test_control_an_unrelated_legacy_meal_changes_nothing(db, owner):
    dish = _dish(db, owner, "омлет")
    _eat(db, owner, dish, TUE)
    _legacy(db, owner, "суп", WED)
    shown = _shown(f"dh:o:{dish.id}:l0")["text"]
    assert "Было 1 раз, последний раз вт 27 октября." in shown


def test_a_meal_with_items_is_not_legacy(db, owner):
    other = _dish(db, owner, "суп")
    dish = _dish(db, owner, "омлет")
    _eat(db, owner, other, WED, description="омлет")  # says омлет, holds суп
    assert "Пока без приёмов." in _shown(f"dh:o:{dish.id}:l0")["text"]


def test_a_legacy_description_answers_to_every_name_of_the_dish(db, owner):
    dish = _dish(db, owner, "омлет", aliases=["яичница"])
    _legacy(db, owner, "яичница", WED)
    assert "Было 1 раз," in _shown(f"dh:o:{dish.id}:l0")["text"]


def test_no_counted_meal_but_a_mention_says_so_without_a_zero(db, owner):
    dish = _dish(db, owner, "омлет")
    _legacy(db, owner, "омлет и кофе", WED)
    shown = _shown(f"dh:o:{dish.id}:l0")["text"]
    assert EARLY in shown
    assert "0 раз" not in shown


def test_times_used_is_not_what_the_card_counts(db, owner):
    dish = _dish(db, owner, "омлет", times_used=99)
    assert "99" not in _shown(f"dh:o:{dish.id}:l0")["text"]


# --- ⭐ -------------------------------------------------------------------------


def test_the_star_on_and_off_and_the_same_target_twice(db, owner, reader):
    dish = _dish(db, owner, "омлет", **_per_100(), usual_grams=200.0)
    shown = _shown(f"dh:fv:{dish.id}:1")
    assert "✓ ⭐ В избранном" in str(_labels(shown))
    db.expire_all()
    fav = crud_favorite_meal.active_of_dish(db, dish.id)
    assert fav.usual_grams == 200.0
    assert fav.calories == pytest.approx(360.0)
    assert fav.name == "омлет"

    again = tap(f"dh:fv:{dish.id}:1")
    assert _toast(again) == meal_items.SAME_TOAST
    assert again.edits() == []

    wire = tap(f"dh:fv:{dish.id}:0")
    assert _toast(wire) == "Убрано из избранного"
    assert "⭐ В избранное" in str(_labels(wire.edits()[-1]))
    db.expire_all()
    assert crud_favorite_meal.active_of_dish(db, dish.id) is None
    assert reader.text == []


# --- O4 rename ------------------------------------------------------------------


def test_o4_asks_and_renames_and_the_old_name_still_logs_this_dish(db, owner, reader):
    dish = _dish(db, owner, "омлет с сыром")
    meal, item = _eat(db, owner, dish)
    asked = _shown(f"dh:rn:{dish.id}")
    assert asked["text"] == ("<b>✏️ Название: омлет с сыром</b>\nНапиши новое название.")
    assert _labels(asked) == [["⬅️ К блюду"]]
    assert _data(asked) == [[f"dh:o:{dish.id}:l0"]]

    shown = _typed_reply("омлет")
    assert shown["text"].startswith("<i>✓ Теперь: омлет.</i>\n\n<b>🍲 Омлет</b>")
    assert "↩️ Вернуть" in str(_labels(shown))
    _fits(shown)
    db.expire_all()
    fresh = db.get(PersonalFood, dish.id)
    assert fresh.canonical_name == "омлет"
    assert fresh.aliases == ["омлет с сыром"]
    assert db.get(MealItem, item.id).name == "омлет с сыром"  # past items keep theirs
    assert db.scalar(select(RecordEvent).where(RecordEvent.kind == "rename"))

    before = len(_meals(db, owner))
    send(text("омлет с сыром"))
    meals = _meals(db, owner)
    assert len(meals) == before + 1
    assert reader.text == []  # a known name costs no read
    newest = max(meals, key=lambda m: m.id)
    assert crud_meal_item.live(db, newest.id)[0].personal_food_id == dish.id


def test_o4a_a_taken_name_offers_a_merge_in_any_case(db, owner, reader):
    dish = _dish(db, owner, "омлет с сыром")
    taken = _dish(db, owner, "Омлет")
    tap(f"dh:rn:{dish.id}")
    shown = _typed_reply("ОМЛЕТ")  # Cyrillic: not caught by SQLite's lower()
    assert shown["text"] == (
        "<b>✏️ Такое блюдо уже есть</b>\n"
        "«Омлет» уже есть в блюдах. Объединить их? Прошлые приёмы останутся как есть."
    )
    assert _labels(shown) == [["🔗 Объединить"], ["⬅️ К блюду"]]
    assert _data(shown) == [[f"dh:j:{dish.id}:{taken.id}"], [f"dh:o:{dish.id}:l0"]]
    db.expire_all()
    assert db.get(PersonalFood, dish.id).canonical_name == "омлет с сыром"
    # Its button does what it says.
    merged = _shown(f"dh:j:{dish.id}:{taken.id}")
    assert "<b>🍲 Омлет</b>" in merged["text"]


def test_o4a_a_hidden_holder_is_offered_and_comes_back_with_the_merge(db, owner):
    dish = _dish(db, owner, "омлет с сыром")
    taken = _dish(db, owner, "омлет", hidden_at=GONE)
    tap(f"dh:rn:{dish.id}")
    shown = _typed_reply("омлет")
    assert _data(shown)[0] == [f"dh:j:{dish.id}:{taken.id}"]
    _shown(_data(shown)[0][0])
    db.expire_all()
    assert db.get(PersonalFood, taken.id).hidden_at is None
    assert db.get(PersonalFood, dish.id).merged_into_id == taken.id


def test_o4a_a_merged_holder_offers_where_it_went(db, owner):
    dish = _dish(db, owner, "омлет с сыром")
    target = _dish(db, owner, "яичница")
    _dish(db, owner, "омлет", merged_into_id=target.id)
    tap(f"dh:rn:{dish.id}")
    shown = _typed_reply("омлет")
    assert "«Яичница» уже есть" in shown["text"]
    assert _data(shown)[0] == [f"dh:j:{dish.id}:{target.id}"]


def test_a_name_merged_into_this_dish_cannot_be_taken_and_says_so(db, owner):
    dish = _dish(db, owner, "яичница")
    _dish(db, owner, "омлет", merged_into_id=dish.id)
    tap(f"dh:rn:{dish.id}")
    shown = _typed_reply("омлет")
    assert shown["text"].startswith(dishes.NAME_IS_THIS_DISH + "\n\n<b>🍲 Яичница</b>")
    db.expire_all()
    assert db.get(PersonalFood, dish.id).canonical_name == "яичница"


def test_only_its_own_alias_is_free_to_come_back(db, owner):
    dish = _dish(db, owner, "омлет", aliases=["яичница"])
    tap(f"dh:rn:{dish.id}")
    shown = _typed_reply("яичница")
    assert shown["text"].startswith("<i>✓ Теперь: яичница.</i>")
    db.expire_all()
    assert db.get(PersonalFood, dish.id).aliases == ["омлет"]


def test_the_same_name_is_no_change_and_writes_no_event(db, owner):
    dish = _dish(db, owner, "омлет")
    tap(f"dh:rn:{dish.id}")
    shown = _typed_reply("омлет")
    assert shown["text"].startswith("<b>🍲 Омлет</b>")
    assert db.scalar(select(RecordEvent).where(RecordEvent.kind == "rename")) is None


def test_a_case_only_rename_is_a_rename_of_itself(db, owner):
    dish = _dish(db, owner, "омлет")
    tap(f"dh:rn:{dish.id}")
    shown = _typed_reply("Омлет")
    assert shown["text"].startswith("<i>✓ Теперь: Омлет.</i>")
    db.expire_all()
    assert db.get(PersonalFood, dish.id).aliases == []


def test_rename_undo_restores_and_a_later_rename_refuses_it(db, owner):
    dish = _dish(db, owner, "омлет с сыром")
    tap(f"dh:rn:{dish.id}")
    shown = _typed_reply("омлет")
    undo = _on(shown, "↩️ Вернуть")
    assert re.fullmatch(r"dh:u:\d+", undo)

    back = _shown(undo)
    assert back["text"].startswith("<i>✓ Возвращено</i>\n\n<b>🍲 Омлет с сыром</b>")
    db.expire_all()
    fresh = db.get(PersonalFood, dish.id)
    assert (fresh.canonical_name, fresh.aliases) == ("омлет с сыром", [])
    # The undo can itself be undone.
    again = _shown(_on(back, "↩️ Вернуть"))
    assert again["text"].startswith("<i>✓ Возвращено</i>\n\n<b>🍲 Омлет</b>")

    # A rename since: the guard, on an undo not yet used.
    tap(f"dh:rn:{dish.id}")
    undo = _on(_typed_reply("яичница"), "↩️ Вернуть")
    tap(f"dh:rn:{dish.id}")
    _typed_reply("кефир")
    refused = _shown(undo)
    assert refused["text"].startswith(meal_items.UNDO_REFUSED)
    db.expire_all()
    assert db.get(PersonalFood, dish.id).canonical_name == "кефир"


def test_a_second_tap_on_undo_changes_nothing(db, owner):
    dish = _dish(db, owner, "омлет с сыром")
    tap(f"dh:rn:{dish.id}")
    undo = _on(_typed_reply("омлет"), "↩️ Вернуть")
    tap(undo)
    wire = tap(undo)
    assert _toast(wire) == meal_items.SAME_TOAST
    events = db.scalars(select(RecordEvent).where(RecordEvent.kind == "rename")).all()
    assert len(list(events)) == 2


def test_a_renamed_dish_merged_meanwhile_answers_as_o3(db, owner):
    dish = _dish(db, owner, "омлет с сыром")
    target = _dish(db, owner, "омлет")
    tap(f"dh:rn:{dish.id}")
    dish.merged_into_id = target.id
    db.commit()
    shown = _typed_reply("яичница")
    assert shown["text"].startswith("<i>Это блюдо объединено с «омлет».</i>")
    db.expire_all()
    assert db.get(PersonalFood, dish.id).canonical_name == "омлет с сыром"


# --- O5 numbers -----------------------------------------------------------------


def test_o5_asks_and_writes_for_future_meals_only(db, owner, reader):
    dish = _dish(
        db,
        owner,
        "омлет с сыром",
        **_per_100(),
        usual_grams=200.0,
        numbers_quality="medium",
    )
    meal, item = _eat(
        db, owner, dish, base_calories=360.0, base_grams=200.0, numbers_source="dish"
    )
    asked = _shown(f"dh:nm:{dish.id}")
    assert asked["text"] == (
        "<b>✏️ Цифры: омлет с сыром</b>\n"
        "На 100 г, четыре числа: ккал, белки, жиры, углеводы. Например: 150 10 8 2"
    )
    assert _labels(asked) == [["⬅️ К блюду"]]

    shown = _typed_reply("150 10 8 2")
    assert shown["text"].startswith(
        "<i>✓ Для будущих приёмов. Прошлые остаются как были.</i>\n\n<b>🍲 Омлет с сыром</b>"
    )
    assert "На 100 г: 🔥 150 ккал · Б 10 · Ж 8 · У 2" in shown["text"]  # typed: no ≈
    assert "↩️ Вернуть" in str(_labels(shown))
    _fits(shown)
    db.expire_all()
    fresh = db.get(PersonalFood, dish.id)
    assert float(fresh.per_100g_calories) == 150.0
    assert float(fresh.per_100g_carbs) == 2.0
    assert fresh.numbers_by_user is True
    assert fresh.numbers_quality == "user"
    assert fresh.numbers_basis == "per_100"
    assert db.scalar(select(RecordEvent).where(RecordEvent.kind == "numbers"))
    # The past stays.
    assert db.get(MealItem, item.id).base_calories == 360.0
    # The future does not: a typed name now serves 200 g at the new numbers.
    send(text("омлет с сыром"))
    newest = max(_meals(db, owner), key=lambda m: m.id)
    assert newest.id != meal.id
    assert crud_meal_item.live(db, newest.id)[0].base_calories == pytest.approx(300.0)
    assert reader.text == []


@pytest.mark.parametrize(
    "typed, expected",
    [
        ("150, 10, 8, 2", (150, 10, 8, 2)),
        ("150,10,8,2", (150, 10, 8, 2)),
        ("150.5 10,5 8 2", (150.5, 10.5, 8, 2)),
        ("  150   10  8  2 ", (150, 10, 8, 2)),
        ("3000 10 8 2", (3000, 10, 8, 2)),  # no plausibility verdict
    ],
)
def test_o5_takes_spaces_or_commas(db, owner, typed, expected):
    dish = _dish(db, owner, "омлет")
    tap(f"dh:nm:{dish.id}")
    _typed_reply(typed)
    db.expire_all()
    fresh = db.get(PersonalFood, dish.id)
    assert (
        float(fresh.per_100g_calories),
        float(fresh.per_100g_proteins),
        float(fresh.per_100g_fats),
        float(fresh.per_100g_carbs),
    ) == expected


@pytest.mark.parametrize(
    "typed",
    ["суп с курицей", "150 10 8", "150 10 8 2 1", "сто 10 8 2", "1234567 1 1 1"],
)
def test_o5_a_wait_never_eats_food(db, owner, reader, typed):
    dish = _dish(db, owner, "омлет")
    tap(f"dh:nm:{dish.id}")
    send(text(typed))
    db.expire_all()
    assert meal_card.has_slot(db, OWNER) is False
    assert db.get(PersonalFood, dish.id).per_100g_calories is None
    assert len(_meals(db, owner)) == 1  # the words went down the router: a meal


def test_numbers_by_user_survive_a_learned_write(db, owner):
    dish = _dish(db, owner, "омлет")
    tap(f"dh:nm:{dish.id}")
    _typed_reply("150 10 8 2")
    db.expire_all()
    crud_personal_food.upsert(
        db,
        user_id=owner.id,
        canonical_name="омлет",
        meal_id=None,
        per_100g_calories=999,
        per_100g_proteins=1,
        per_100g_fats=1,
        per_100g_carbs=1,
    )
    db.expire_all()
    assert float(db.get(PersonalFood, dish.id).per_100g_calories) == 150.0


def test_numbers_undo_restores_and_a_later_edit_refuses_it(db, owner):
    dish = _dish(db, owner, "омлет", **_per_100(), numbers_quality="medium")
    tap(f"dh:nm:{dish.id}")
    undo = _on(_typed_reply("150 10 8 2"), "↩️ Вернуть")
    back = _shown(undo)
    assert back["text"].startswith("<i>✓ Возвращено</i>")
    db.expire_all()
    fresh = db.get(PersonalFood, dish.id)
    assert float(fresh.per_100g_calories) == 180.0
    assert fresh.numbers_by_user is False
    assert fresh.numbers_quality == "medium"

    undo_of_undo = _on(back, "↩️ Вернуть")
    tap(f"dh:nm:{dish.id}")
    _typed_reply("100 1 1 1")
    refused = _shown(undo_of_undo)
    assert refused["text"].startswith(meal_items.UNDO_REFUSED)
    db.expire_all()
    assert float(db.get(PersonalFood, dish.id).per_100g_calories) == 100.0


# --- O6 when it was -------------------------------------------------------------


def test_o6_exact_lines_chips_and_the_early_note(db, owner):
    dish = _dish(db, owner, "омлет с сыром")
    m1, _ = _eat(db, owner, dish, WED)
    m2, _ = _eat(db, owner, dish, TUE, factor=2.0)
    m3, _ = _eat(db, owner, dish, SAT, time_precision="day_only")
    _legacy(db, owner, "омлет с сыром, кофе")
    shown = _shown(f"dh:h:{dish.id}:0")
    assert shown["text"] == (
        "<b>🕘 Омлет с сыром</b>\n"
        "ср 28 октября, 8:40\n"
        "вт 27 октября, 9:10 · 2 порции\n"
        "сб 24 октября, без времени\n"
        f"{EARLY}"
    )
    assert _labels(shown) == [["28 окт", "27 окт", "24 окт"], ["⬅️ К блюду"]]
    assert _data(shown) == [
        [
            f"ml:o:{m1.id}:h{dish.id}",
            f"ml:o:{m2.id}:h{dish.id}",
            f"ml:o:{m3.id}:h{dish.id}",
        ],
        [f"dh:o:{dish.id}:l0"],
    ]


def test_o6_no_early_note_when_nothing_can_be_missing(db, owner):
    dish = _dish(db, owner, "омлет")
    _eat(db, owner, dish, WED)
    assert EARLY not in _shown(f"dh:h:{dish.id}:0")["text"]


def test_o6_a_chip_opens_the_meal_with_the_way_back(db, owner):
    dish = _dish(db, owner, "омлет")
    meal, _ = _eat(db, owner, dish, WED)
    shown = _shown(f"ml:o:{meal.id}:h{dish.id}")
    assert _on(shown, "⬅️ Когда было") == f"dh:h:{dish.id}:0"


def test_o6_pages_of_nine_with_a_no_op_middle_and_duplicate_dates(db, owner):
    dish = _dish(db, owner, "омлет")
    for index in range(20):
        _eat(db, owner, dish, WED - timedelta(minutes=index))
    shown = _shown(f"dh:h:{dish.id}:0")
    assert shown["text"].count("ср 28 октября") == 9
    assert _labels(shown)[0] == ["28 окт", "28 окт · 2", "28 окт · 3"]
    assert _labels(shown)[3] == ["1/3", "▶️"]
    assert _data(shown)[3] == ["dh:nop", f"dh:h:{dish.id}:1"]
    _fits(shown)
    last = _shown(f"dh:h:{dish.id}:99")
    assert last["text"].count("ср 28 октября") == 2
    assert _labels(last)[-2] == ["◀️", "3/3"]


def test_o6_holds_no_deleted_or_removed_or_draft(db, owner):
    dish = _dish(db, owner, "омлет")
    _eat(db, owner, dish, WED)
    _eat(db, owner, dish, TUE, deleted_at=GONE)
    _eat(db, owner, dish, TUE, removed_at=GONE)
    _eat(db, owner, dish, TUE, is_draft=True)
    shown = _shown(f"dh:h:{dish.id}:0")["text"]
    assert "ср 28 октября" in shown  # control
    assert "вт 27" not in shown


def test_o6_of_a_dish_with_no_meals_is_its_card(db, owner):
    dish = _dish(db, owner, "омлет")
    assert _shown(f"dh:h:{dish.id}:0")["text"].startswith("<b>🍲 Омлет</b>")


def test_o6_of_a_merged_dish_holds_its_items_under_the_target(db, owner):
    source = _dish(db, owner, "омлет с сыром")
    target = _dish(db, owner, "омлет")
    _eat(db, owner, source, TUE)
    _eat(db, owner, target, WED)
    tap(f"dh:j:{source.id}:{target.id}")
    shown = _shown(f"dh:h:{target.id}:0")["text"]
    assert "ср 28 октября" in shown and "вт 27 октября" in shown


# --- O7 merge -------------------------------------------------------------------


def test_o7_picker_exact_and_a_shared_word_first(db, owner):
    source = _dish(db, owner, "омлет с сыром")
    near = _dish(db, owner, "омлет")
    recent = _dish(db, owner, "яичница")
    _eat(db, owner, recent, WED)
    _dish(db, owner, "сыр")
    _dish(db, owner, "омлет с ветчиной")
    _dish(db, owner, "скрытый омлет", hidden_at=GONE)
    shown = _shown(f"dh:jl:{source.id}:0")
    assert shown["text"] == (
        "<b>🔗 Омлет с сыром: с чем объединить?</b>\n"
        "Прошлые приёмы и их цифры останутся как есть."
    )
    labels = _labels(shown)
    assert labels[0:2] == [
        ["🍳 Омлет с ветчиной", "🧀 Сыр"],
        ["🍳 Омлет", "🍳 Яичница"],
    ]
    assert labels[2:] == [["🔍 Найти"], ["⬅️ К блюду"]]
    assert _on(shown, "🍳 Омлет") == f"dh:jq:{source.id}:{near.id}"
    assert _on(shown, "🔍 Найти") == f"dh:jf:{source.id}"
    _fits(shown)


def test_o7_pages_say_where_they_are(db, owner):
    source = _dish(db, owner, "омлет с сыром")
    for index in range(9):
        _dish(db, owner, f"блюдо {index}")
    shown = _shown(f"dh:jl:{source.id}:0")
    assert shown["text"].endswith("Страница 1 из 3.")
    assert _labels(shown)[-2] == ["🔍 Найти", "▶️"]
    assert _data(shown)[-2] == [f"dh:jf:{source.id}", f"dh:jl:{source.id}:1"]
    assert "Страница 3 из 3." in _shown(f"dh:jl:{source.id}:99")["text"]
    _fits(shown)


def test_merge_find_uses_o2b_text_and_lists_targets(db, owner, reader):
    source = _dish(db, owner, "омлет с сыром")
    target = _dish(db, owner, "Яичница")
    asked = _shown(f"dh:jf:{source.id}")
    assert asked["text"] == "<b>🔍 Найти блюдо</b>\nНапиши название."
    assert _labels(asked) == [["⬅️ К блюду"]]
    missed = _typed_reply("борщ")
    assert "По «борщ» ничего не нашлось. Напиши иначе." in missed["text"]
    assert meal_card.has_slot(db, OWNER) is True
    found = _typed_reply("ЯИЧ")
    assert _on(found, "🍳 Яичница") == f"dh:jq:{source.id}:{target.id}"
    assert _labels(found)[-1] == ["⬅️ К блюду"]
    assert reader.text == []
    assert _meals(db, owner) == []


def test_a_merge_chip_asks_first_with_both_names_whole(db, owner):
    # Owner 2026-11-10: a chip shows the start of a name and merged at once.
    source = _dish(db, owner, "сырники со сметаной и ягодным вареньем")
    target = _dish(db, owner, "сырники домашние из рыночного творога")
    picker = _shown(f"dh:jl:{source.id}:0")
    chip = next(label for row in _labels(picker) for label in row if "…" in label)
    asked = _shown(_on(picker, chip))
    assert "«Сырники со сметаной и ягодным вареньем»" in asked["text"]
    assert "«Сырники домашние из рыночного творога»" in asked["text"]
    assert "Прошлые приёмы и их цифры останутся как есть." in asked["text"]
    assert _data(asked) == [
        [f"dh:j:{source.id}:{target.id}"],
        [f"dh:jl:{source.id}:0"],
    ]
    _fits(asked)
    assert _fresh_dish(db, source).merged_into_id is None  # nothing moved yet
    back = _shown(_on(asked, "⬅️ Выбрать другое"))
    assert back["text"].startswith("<b>🔗 Сырники со сметаной")
    _shown(_on(asked, "🔗 Да, объединить"))
    assert _fresh_dish(db, source).merged_into_id == target.id


def test_the_ask_carries_the_origin_on_both_buttons(db, owner):
    source = _dish(db, owner, "омлет с сыром")
    target = _dish(db, owner, "омлет")
    asked = _shown(f"dh:jq:{source.id}:{target.id}:l2")
    assert _data(asked) == [
        [f"dh:j:{source.id}:{target.id}:l2"],
        [f"dh:jl:{source.id}:0:l2"],
    ]


def test_the_ask_for_a_dish_merged_into_itself_shows_the_dish(db, owner):
    source = _dish(db, owner, "омлет")
    shown = _shown(f"dh:jq:{source.id}:{source.id}")
    assert "Объединить блюда?" not in shown["text"]
    assert _fresh_dish(db, source).merged_into_id is None


def _fresh_dish(db, dish):
    db.expire_all()
    return db.get(PersonalFood, dish.id)


def test_merge_find_never_offers_the_dish_itself(db, owner):
    source = _dish(db, owner, "омлет")
    tap(f"dh:jf:{source.id}")
    assert "ничего не нашлось" in _typed_reply("омлет")["text"]


def test_o7a_relinks_and_keeps_names_numbers_and_says_together(db, owner):
    source = _dish(db, owner, "омлет с сыром", **_per_100(kcal=250))
    target = _dish(db, owner, "омлет", **_per_100(kcal=180))
    _, moved = _eat(db, owner, source, WED, base_calories=500.0)
    _, stays = _eat(db, owner, target, TUE, base_calories=200.0)
    _star(db, owner, target)
    shown = _shown(f"dh:j:{source.id}:{target.id}")
    assert shown["text"] == (
        "<i>✓ «Омлет с сыром» теперь в блюде «омлет». "
        "Прошлые приёмы и их цифры на месте.</i>\n\n"
        "<b>🍲 Омлет</b>\n"
        "Вместе было 2 раза, последний раз ср 28 октября."
    )
    assert _labels(shown) == [
        ["🍽 Записать", "✓ ⭐ В избранном"],
        ["🕘 Когда было", "🔢 Цифры"],
        ["Ещё ›", "↩️ Вернуть"],
        ["⬅️ Все блюда"],
    ]
    assert re.fullmatch(r"dh:u:\d+", _on(shown, "↩️ Вернуть"))
    db.expire_all()
    fresh = db.get(MealItem, moved.id)
    assert fresh.personal_food_id == target.id  # the control: the dish did change
    assert fresh.original_food_id == source.id
    assert fresh.name == "омлет с сыром"
    assert fresh.base_calories == 500.0
    assert db.get(MealItem, stays.id).personal_food_id == target.id
    assert db.get(PersonalFood, source.id).merged_into_id == target.id
    assert float(db.get(PersonalFood, target.id).per_100g_calories) == 180.0
    event = db.scalar(select(RecordEvent).where(RecordEvent.kind == "food_merge"))
    assert event.food_id == source.id


def test_merge_undo_weeks_later_keeps_the_new_meals_on_the_target(db, owner, reader):
    source = _dish(db, owner, "омлет с сыром")
    target = _dish(db, owner, "омлет")
    _, moved = _eat(db, owner, source, TUE)
    shown = _shown(f"dh:j:{source.id}:{target.id}")
    undo = _on(shown, "↩️ Вернуть")
    # Meanwhile: a new meal under the target, and the old name typed.
    _, later = _eat(db, owner, target, WED)
    send(text("омлет с сыром"))
    typed = max(_meals(db, owner), key=lambda m: m.id)
    typed_item = crud_meal_item.live(db, typed.id)[0]
    assert typed_item.personal_food_id == target.id  # the old name resolves to it
    assert reader.text == []
    # Weeks pass.
    event = db.scalar(select(RecordEvent).where(RecordEvent.kind == "food_merge"))
    event.created_at = datetime.now(UTC) - timedelta(days=30)
    db.commit()

    back = _shown(undo)
    assert back["text"].startswith("<i>✓ Возвращено</i>\n\n<b>🍲 Омлет с сыром</b>")
    db.expire_all()
    assert db.get(PersonalFood, source.id).merged_into_id is None
    assert db.get(MealItem, moved.id).personal_food_id == source.id
    assert db.get(MealItem, later.id).personal_food_id == target.id
    assert db.get(MealItem, typed_item.id).personal_food_id == target.id
    # Undone in its turn: merged again.
    again = _shown(_on(back, "↩️ Вернуть"))
    assert "<b>🍲 Омлет</b>" in again["text"]
    db.expire_all()
    assert db.get(PersonalFood, source.id).merged_into_id == target.id
    assert db.get(MealItem, moved.id).personal_food_id == target.id


def test_merge_undo_refused_when_the_dish_moved_on(db, owner):
    source = _dish(db, owner, "омлет с сыром")
    target = _dish(db, owner, "омлет")
    other = _dish(db, owner, "яичница")
    undo = _on(_shown(f"dh:j:{source.id}:{target.id}"), "↩️ Вернуть")
    merge_again = _on(_shown(undo), "↩️ Вернуть")
    tap(f"dh:j:{source.id}:{other.id}")
    assert _toast(tap(undo)) == meal_items.SAME_TOAST  # already undone once
    refused = _shown(merge_again)
    assert refused["text"].startswith(meal_items.UNDO_REFUSED)
    assert "<b>🍲 Яичница</b>" in refused["text"]
    db.expire_all()
    assert db.get(PersonalFood, source.id).merged_into_id == other.id


def test_merge_undo_guard_when_the_merged_dish_changed_by_hand(db, owner):
    source = _dish(db, owner, "омлет с сыром")
    target = _dish(db, owner, "омлет")
    other = _dish(db, owner, "яичница")
    undo = _on(_shown(f"dh:j:{source.id}:{target.id}"), "↩️ Вернуть")
    db.expire_all()
    db.get(PersonalFood, source.id).merged_into_id = other.id
    db.commit()
    refused = _shown(undo)
    assert refused["text"].startswith(meal_items.UNDO_REFUSED)
    db.expire_all()
    assert db.get(PersonalFood, source.id).merged_into_id == other.id


def test_a_merge_chain_undoes_in_either_order(db, owner):
    first = _dish(db, owner, "омлет с сыром")
    middle = _dish(db, owner, "омлет с травами")
    last = _dish(db, owner, "омлет")
    _, item = _eat(db, owner, first)
    undo_first = _on(_shown(f"dh:j:{first.id}:{middle.id}"), "↩️ Вернуть")
    undo_middle = _on(_shown(f"dh:j:{middle.id}:{last.id}"), "↩️ Вернуть")
    db.expire_all()
    assert db.get(MealItem, item.id).personal_food_id == last.id
    tap(undo_middle)
    db.expire_all()
    assert db.get(MealItem, item.id).personal_food_id == middle.id
    tap(undo_first)
    db.expire_all()
    assert db.get(MealItem, item.id).personal_food_id == first.id


def test_a_chain_undone_from_its_start_takes_the_items_from_the_end(db, owner):
    first = _dish(db, owner, "омлет с сыром")
    middle = _dish(db, owner, "омлет с травами")
    last = _dish(db, owner, "омлет")
    _, item = _eat(db, owner, first)
    undo_first = _on(_shown(f"dh:j:{first.id}:{middle.id}"), "↩️ Вернуть")
    tap(f"dh:j:{middle.id}:{last.id}")
    tap(undo_first)
    db.expire_all()
    assert db.get(MealItem, item.id).personal_food_id == first.id


def test_merge_repoints_the_star_or_soft_removes_it_and_undo_brings_it(db, owner):
    source = _dish(db, owner, "омлет с сыром")
    target = _dish(db, owner, "омлет")
    fav = _star(db, owner, source)
    undo = _on(_shown(f"dh:j:{source.id}:{target.id}"), "↩️ Вернуть")
    db.expire_all()
    assert db.get(FavoriteMeal, fav.id).personal_food_id == target.id
    assert db.get(FavoriteMeal, fav.id).removed_at is None
    tap(undo)
    db.expire_all()
    assert db.get(FavoriteMeal, fav.id).personal_food_id == source.id

    own = _star(db, owner, target)
    undo = _on(_shown(f"dh:j:{source.id}:{target.id}"), "↩️ Вернуть")
    db.expire_all()
    assert db.get(FavoriteMeal, fav.id).removed_at is not None
    assert db.get(FavoriteMeal, own.id).removed_at is None  # the target keeps its own
    tap(undo)
    db.expire_all()
    assert db.get(FavoriteMeal, fav.id).removed_at is None


def test_o7_stale_a_merged_or_hidden_source_shows_its_card(db, owner):
    target = _dish(db, owner, "омлет")
    merged = _dish(db, owner, "омлет с сыром", merged_into_id=target.id)
    hidden = _dish(db, owner, "айран", hidden_at=GONE)
    for data in (f"dh:jl:{merged.id}:0", f"dh:j:{merged.id}:{target.id}"):
        shown = _shown(data)
        assert shown["text"].startswith(
            "<i>Это блюдо уже объединено.</i>\n\n<b>🍲 Омлет</b>"
        )
        tg._card_delivery._screens.clear()
    shown = _shown(f"dh:jl:{hidden.id}:0")
    assert shown["text"].startswith(
        "<i>Это блюдо уже объединено.</i>\n\n<b>🍲 Айран</b>"
    )


def test_merging_a_dish_with_itself_changes_nothing(db, owner):
    dish = _dish(db, owner, "омлет")
    shown = _shown(f"dh:j:{dish.id}:{dish.id}")
    assert shown["text"].startswith("<b>🍲 Омлет</b>")
    db.expire_all()
    assert db.get(PersonalFood, dish.id).merged_into_id is None


def test_a_merge_onto_a_gone_target_redraws_the_list(db, owner):
    source = _dish(db, owner, "омлет")
    shown = _shown(f"dh:j:{source.id}:{source.id + 500}")
    assert shown["text"].startswith("<i>Этого блюда уже нет в списке.</i>")
    db.expire_all()
    assert db.get(PersonalFood, source.id).merged_into_id is None


def test_a_merged_dish_opens_as_the_target_with_the_notice(db, owner):
    target = _dish(db, owner, "омлет")
    merged = _dish(db, owner, "омлет с сыром", merged_into_id=target.id)
    shown = _shown(f"dh:o:{merged.id}:l0")
    assert shown["text"].startswith(
        "<i>Это блюдо объединено с «омлет».</i>\n\n<b>🍲 Омлет</b>"
    )


# --- O14 and O8 ------------------------------------------------------------------


def test_o14_exact_screen(db, owner):
    dish = _dish(db, owner, "омлет с сыром")
    shown = _shown(f"dh:mo:{dish.id}:l2")
    assert shown["text"] == (
        "<b>🍲 Ещё: омлет с сыром</b>\n"
        "🗑 Из списков: блюда не будет в «Блюдах» и при выборе блюда, "
        "прошлые приёмы останутся."
    )
    assert _labels(shown) == [
        ["✏️ Название", "🔗 Объединить"],
        ["⚖️ Порция", "🏷 Группы"],
        ["🗑 Из списков"],
        ["⬅️ К блюду"],
    ]
    assert _data(shown)[-1] == [f"dh:o:{dish.id}:l2"]
    assert _on(shown, "✏️ Название") == f"dh:rn:{dish.id}:l2"
    assert _on(shown, "🗑 Из списков") == f"dh:x:{dish.id}:l2"
    assert _on(shown, "🔗 Объединить") == f"dh:jl:{dish.id}:0:l2"


def test_the_table_shapes_hold_when_the_origin_is_the_default_list(db, owner):
    dish = _dish(db, owner, "омлет")
    shown = _shown(f"dh:mo:{dish.id}:l0")
    assert _data(shown) == [
        [f"dh:rn:{dish.id}", f"dh:jl:{dish.id}:0"],
        [f"dh:pg:{dish.id}", f"dh:gr:{dish.id}"],
        [f"dh:x:{dish.id}"],
        [f"dh:o:{dish.id}:l0"],
    ]


def test_o8_hides_at_once_and_the_star_goes_and_undo_brings_both(db, owner):
    dish = _dish(db, owner, "омлет с сыром")
    meal, item = _eat(db, owner, dish)
    fav = _star(db, owner, dish)
    shown = _shown(f"dh:x:{dish.id}")
    assert shown["text"] == (
        "<b>🗑 Убрано из списков: омлет с сыром</b>\n"
        "Прошлые приёмы на месте. Блюдо вернётся, если снова его записать.\n"
        "Вернуть можно и позже, пока эта кнопка на экране."
    )
    assert _labels(shown) == [["↩️ Вернуть"], ["⬅️ Все блюда"]]
    undo = _on(shown, "↩️ Вернуть")
    assert re.fullmatch(r"dh:u:\d+", undo)
    db.expire_all()
    assert db.get(PersonalFood, dish.id).hidden_at is not None
    assert db.get(FavoriteMeal, fav.id).removed_at is not None
    assert db.get(MealItem, item.id).removed_at is None  # the past stays
    tg._card_delivery._screens.clear()
    assert "омлет" not in _shown("dh:l:0")["text"].lower()
    assert "Омлет" not in str(_labels(_shown("dh:l:0")))

    back = _shown(undo)
    assert back["text"].startswith("<i>✓ Возвращено</i>\n\n<b>🍲 Омлет с сыром</b>")
    db.expire_all()
    assert db.get(PersonalFood, dish.id).hidden_at is None
    assert db.get(FavoriteMeal, fav.id).removed_at is None
    # A second tap on the old button: its card, no change.
    wire = tap(undo)
    assert _toast(wire) == meal_items.SAME_TOAST


def test_o8_eating_it_again_brings_it_back(db, owner, reader):
    dish = _dish(db, owner, "омлет с сыром", **_per_100(), usual_grams=200.0)
    shown = _shown(f"dh:x:{dish.id}")
    undo = _on(shown, "↩️ Вернуть")
    send(text("омлет с сыром"))
    assert reader.text == []
    db.expire_all()
    assert db.get(PersonalFood, dish.id).hidden_at is None
    assert "Омлет с сыром" in str(_labels(_shown("dh:l:0")))
    # Stale: undo on a restored dish opens its card.
    restored = _shown(undo)
    assert "<b>🍲 Омлет с сыром</b>" in restored["text"]
    db.expire_all()
    assert db.get(PersonalFood, dish.id).hidden_at is None


def test_a_hidden_dish_shows_its_card_with_the_way_back_in_reach(db, owner, hard_day):
    dish = _dish(db, owner, "омлет")
    undo = _on(_shown(f"dh:x:{dish.id}"), "↩️ Вернуть")
    shown = _shown(f"dh:o:{dish.id}:l0")
    assert shown["text"].startswith("<b>🍲 Омлет</b>")
    assert _on(shown, "↩️ Вернуть") == undo


def test_hiding_twice_is_the_same_target(db, owner):
    dish = _dish(db, owner, "омлет")
    tap(f"dh:x:{dish.id}")
    wire = tap(f"dh:x:{dish.id}")
    assert _toast(wire) == meal_items.SAME_TOAST
    events = list(
        db.scalars(select(RecordEvent).where(RecordEvent.kind == "food_hide"))
    )
    assert len(events) == 1


def test_a_hidden_dish_is_gone_from_the_picker_and_control_a_live_one_stays(db, owner):
    keep = _dish(db, owner, "кефир")
    dish = _dish(db, owner, "омлет")
    meal, _ = _eat(db, owner, keep)
    tap(f"dh:x:{dish.id}")
    shown = _shown(f"ml:pk:{meal.id}:a:r:0")
    assert "омлет" not in str(_labels(shown))
    assert "кефир" in str(_labels(shown))  # the picker's words are the dish's own


@pytest.mark.parametrize("verb", ["dh:mo:{id}:l0", "dh:rn:{id}", "dh:nm:{id}"])
def test_rare_verbs_on_a_hidden_dish_answer_as_o3(db, owner, verb):
    dish = _dish(db, owner, "омлет", hidden_at=GONE)
    shown = _shown(verb.format(id=dish.id))
    assert shown["text"].startswith("<b>🍲 Омлет</b>")
    assert meal_card.has_slot(db, OWNER) is False


# --- O10 from a dish ------------------------------------------------------------


def test_o10_logs_a_portion_free_and_goes_back_to_the_dish(db, owner, reader):
    dish = _dish(db, owner, "омлет с сыром", **_per_100(), usual_grams=200.0)
    wire = tap(f"dh:log:{dish.id}:l2")
    shown = wire.edits()[-1]
    assert shown["text"] == "Записано: омлет с сыром."
    assert _toast(wire) == "Записано"
    meals = _meals(db, owner)
    assert len(meals) == 1
    assert _labels(shown) == [
        [close_row.LABEL],
        ["↩️ Отменить", "🕐 Время"],
        ["⬅️ К блюду"],
    ]
    assert _data(shown) == [
        [close_row.CALLBACK],
        [f"fav:u:{meals[0].id}", f"ml:t:{meals[0].id}:r"],
        [f"dh:o:{dish.id}:l2"],
    ]
    item = crud_meal_item.live(db, meals[0].id)[0]
    assert item.personal_food_id == dish.id
    assert item.numbers_source == "dish"
    assert item.base_calories == pytest.approx(360.0)
    assert reader.text == [] and reader.image == []


def test_o10_of_a_starred_dish_is_a_favourite_log_with_a_soft_undo(db, owner, reader):
    dish = _dish(db, owner, "омлет с сыром")
    fav = _star(db, owner, dish, calories=360.0)
    wire = tap(f"dh:log:{dish.id}")
    assert wire.edits()[-1]["text"] == "Записано: омлет с сыром."
    meal = _meals(db, owner)[0]
    assert meal.resolution_source == "favorite"
    assert meal.calories == 360.0
    item = crud_meal_item.live(db, meal.id)[0]
    assert item.personal_food_id == dish.id
    db.expire_all()
    assert db.get(FavoriteMeal, fav.id).use_count == 1

    tap(_data(wire.edits()[-1])[1][0])
    db.expire_all()
    assert db.get(Meal, meal.id).deleted_at is not None  # soft
    assert reader.text == []


def test_o10_of_a_dish_with_no_star_has_a_working_undo_too(db, owner):
    dish = _dish(db, owner, "омлет", **_per_100(), usual_grams=200.0)
    shown = tap(f"dh:log:{dish.id}").edits()[-1]
    undo = _on(shown, "↩️ Отменить")
    meal = _meals(db, owner)[0]
    tap(undo)
    db.expire_all()
    assert db.get(Meal, meal.id).deleted_at is not None


def test_o10_a_double_tap_on_the_same_message_is_one_meal(db, owner):
    dish = _dish(db, owner, "омлет", **_per_100(), usual_grams=200.0)
    tap(f"dh:log:{dish.id}", message_id=4900)
    wire = tap(f"dh:log:{dish.id}", message_id=4900)
    assert _toast(wire) == "Уже записано"
    assert wire.edits() == []
    assert len(_meals(db, owner)) == 1


def test_control_a_later_tap_or_another_message_logs_again(db, owner):
    dish = _dish(db, owner, "омлет", **_per_100(), usual_grams=200.0)
    tap(f"dh:log:{dish.id}", message_id=4900)
    tap(f"dh:log:{dish.id}", message_id=4901)
    assert len(_meals(db, owner)) == 2
    db.expire_all()
    for meal in _meals(db, owner):
        meal.created_at = datetime.now(UTC) - timedelta(seconds=30)
    db.commit()
    tap(f"dh:log:{dish.id}", message_id=4900)
    assert len(_meals(db, owner)) == 3


def test_o10_logging_a_hidden_dish_brings_it_back(db, owner):
    dish = _dish(db, owner, "омлет", hidden_at=GONE)
    tap(f"dh:log:{dish.id}")
    db.expire_all()
    assert db.get(PersonalFood, dish.id).hidden_at is None


def test_a_reply_to_o10_means_that_meal(db, owner):
    from app.models import MealMessage

    dish = _dish(db, owner, "омлет")
    tap(f"dh:log:{dish.id}", message_id=4900)
    row = db.scalar(select(MealMessage).where(MealMessage.message_id == 4900))
    assert row.meal_id == _meals(db, owner)[0].id
    # Control: the dish card on the message unmaps it again.
    tap(f"dh:o:{dish.id}:l0", message_id=4900)
    db.expire_all()
    assert db.scalar(select(MealMessage).where(MealMessage.message_id == 4900)) is None


# --- the entry points -----------------------------------------------------------


def test_n2_offers_the_dish_with_its_way_back(db, owner):
    dish = _dish(db, owner, "курица")
    meal, item = _eat(
        db, owner, dish, base_calories=330.0, base_grams=240.0, numbers_source="model"
    )
    shown = _shown(f"ml:i:{item.id}:r")
    assert _labels(shown)[3] == ["🔢 Цифры", "🍲 Всё о блюде"]
    data = _on(shown, "🍲 Всё о блюде")
    assert data == f"dh:o:{dish.id}:i{item.id}"
    _fits(shown)
    card = _shown(data)
    assert card["text"].startswith("<b>🍲 Курица</b>")
    assert _labels(card)[-1] == ["⬅️ К блюду в приёме"]
    back = _shown(_on(card, "⬅️ К блюду в приёме"))
    assert back["text"].startswith("<b>🍲 Курица</b>\nВ приёме")


def test_n2_from_a_day_list_keeps_its_origin_through_the_dish(db, owner):
    dish = _dish(db, owner, "курица")
    meal, item = _eat(
        db, owner, dish, base_calories=330.0, base_grams=240.0, numbers_source="model"
    )
    shown = _shown(f"ml:i:{item.id}:t")
    data = _on(shown, "🍲 Всё о блюде")
    assert data == f"dh:o:{dish.id}:i{item.id}-t"
    assert _on(_shown(data), "⬅️ К блюду в приёме") == f"ml:i:{item.id}:t"


def test_n2_with_an_undo_still_fits(db, owner):
    dish = _dish(db, owner, "курица")
    meal, item = _eat(
        db, owner, dish, base_calories=330.0, base_grams=240.0, numbers_source="model"
    )
    shown = _shown(f"ml:q:{item.id}:2:r")
    _fits(shown)
    assert "🍲 Всё о блюде" in str(_labels(shown))


def test_n2_with_no_linked_dish_does_not_offer_it(db, owner):
    dish = _dish(db, owner, "курица")
    meal, item = _eat(
        db, owner, dish, base_calories=330.0, base_grams=240.0, numbers_source="model"
    )
    item.personal_food_id = None
    db.commit()
    assert "🍲 Всё о блюде" not in str(_labels(_shown(f"ml:i:{item.id}:r")))


def test_n2h_does_not_offer_it_on_a_hard_day(db, owner, hard_day):
    dish = _dish(db, owner, "курица")
    meal, item = _eat(
        db, owner, dish, base_calories=330.0, base_grams=240.0, numbers_source="model"
    )
    assert "🍲 Всё о блюде" not in str(_labels(_shown(f"ml:i:{item.id}:r")))


def test_m9a_offers_both_starred_and_all_dishes(db, owner):
    shown = send(text("🍽 Добавить приём пищи")).sent()[-1]
    assert shown["text"] == capture.M9A_NEW_MEAL
    assert _labels(shown) == [["⭐ Избранное", "🍲 Блюда"]]
    assert _data(shown) == [["fav:p:0", "dh:l:0"]]


def test_m12_says_where_the_dishes_went():
    assert capture.M12_ROLLOUT == (
        "Теперь еда записывается сразу, без подтверждения. "
        "В меню появились «🍲 Блюда». "
        "Как это работает: ⚙️ Настройки → ℹ️ Как это работает."
    )


def test_a_slot_of_the_card_still_closes_on_a_dish_tap(db, owner):
    dish = _dish(db, owner, "курица")
    meal, item = _eat(db, owner, dish)
    tap(f"ml:g:{item.id}:r")
    db.expire_all()
    assert meal_card.has_slot(db, OWNER) is True  # control
    tap("dh:l:0")
    db.expire_all()
    assert meal_card.has_slot(db, OWNER) is False


# --- layout -----------------------------------------------------------------------


def test_every_screen_fits_at_the_worst_names_and_ids(db, owner):
    name = "очень длинное название блюда на целый обед и ужин"
    dish = _dish(db, owner, name, **_per_100(), usual_grams=200.0)
    other = _dish(db, owner, name + " второе")
    for index in range(12):
        _dish(db, owner, f"{name} {index}")
    _eat(db, owner, dish)
    for data in (
        "dh:l:0",
        "dh:l:1",
        "dh:f",
        f"dh:o:{dish.id}:l0",
        f"dh:n:{dish.id}:1",
        f"dh:mo:{dish.id}:l0",
        f"dh:rn:{dish.id}",
        f"dh:nm:{dish.id}",
        f"dh:h:{dish.id}:0",
        f"dh:jl:{dish.id}:0",
        f"dh:jl:{dish.id}:1",
        f"dh:jf:{dish.id}",
        f"dh:log:{dish.id}",
        f"dh:o:{dish.id}:i{10**9}-h{10**9}",
        f"dh:n:{dish.id}:1:i{10**9}-h{10**9}",
        f"dh:j:{other.id}:{dish.id}",
        f"dh:x:{dish.id}",
    ):
        tg._card_delivery._screens.clear()
        _fits(_shown(data))


def test_every_dh_button_answers_a_real_verb(db, owner):
    """Every button drawn is a shape the module knows."""
    dish = _dish(db, owner, "омлет", **_per_100(), usual_grams=200.0)
    other = _dish(db, owner, "сыр")
    _eat(db, owner, dish)
    seen = set()
    for data in (
        "dh:l:0",
        "dh:f",
        f"dh:o:{dish.id}:l0",
        f"dh:o:{dish.id}:f3",
        f"dh:o:{dish.id}:i9-t",
        f"dh:n:{dish.id}:1",
        f"dh:mo:{dish.id}:l0",
        f"dh:h:{dish.id}:0",
        f"dh:jl:{dish.id}:0",
        f"dh:j:{other.id}:{dish.id}",
    ):
        tg._card_delivery._screens.clear()
        for row in _data(_shown(data)):
            seen.update(cb for cb in row if cb.startswith("dh:"))
    assert seen
    for data in seen:
        assert dishes.is_known_shape(data), data
    assert not dishes.is_known_shape("dh:zz:1")  # control


# --- what an undo must not overwrite ------------------------------------------------


def test_a_rename_undo_refused_when_the_old_name_went_to_another_dish(db, owner):
    dish = _dish(db, owner, "омлет с сыром")
    tap(f"dh:rn:{dish.id}")
    undo = _on(_typed_reply("омлет"), "↩️ Вернуть")
    other = _dish(db, owner, "Омлет с сыром")
    refused = _shown(undo)
    assert refused["text"].startswith(meal_items.UNDO_REFUSED)
    db.expire_all()
    assert db.get(PersonalFood, dish.id).canonical_name == "омлет"
    assert db.get(PersonalFood, other.id).canonical_name == "Омлет с сыром"


def test_a_hide_undo_refused_when_it_was_hidden_again_since(db, owner):
    dish = _dish(db, owner, "айран")
    undo = _on(_shown(f"dh:x:{dish.id}"), "↩️ Вернуть")
    db.expire_all()
    db.get(PersonalFood, dish.id).hidden_at = GONE
    db.commit()
    refused = _shown(undo)
    assert refused["text"].startswith(meal_items.UNDO_REFUSED)
    db.expire_all()
    assert db.get(PersonalFood, dish.id).hidden_at is not None


def test_a_merge_undo_leaves_an_item_a_person_moved_since(db, owner):
    source = _dish(db, owner, "омлет с сыром")
    target = _dish(db, owner, "омлет")
    elsewhere = _dish(db, owner, "яичница")
    _meal, stays = _eat(db, owner, source, WED)
    _meal, moved = _eat(db, owner, source, TUE)
    undo = _on(_shown(f"dh:j:{source.id}:{target.id}"), "↩️ Вернуть")
    db.expire_all()
    db.get(MealItem, moved.id).personal_food_id = elsewhere.id
    db.commit()
    _shown(undo)
    db.expire_all()
    assert db.get(MealItem, stays.id).personal_food_id == source.id  # the control
    assert db.get(MealItem, moved.id).personal_food_id == elsewhere.id
