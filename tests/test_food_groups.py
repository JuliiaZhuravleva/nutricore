"""Food groups on a meal (stage 5, slice B): the tags, the draft, what is saved."""

import json
import logging

from app.services import food_groups as fg
from app.services import meal_draft as md
from app.services.openai_service import _FOOD_GROUPS_RULE
from app.services.product_lookup_service import parse_nutrition

_ITEMS = [
    {"name": "гречка", "groups": ["grain"]},
    {"name": "тофу", "groups": ["legumes", "anchor"]},
    {"name": "брокколи", "groups": ["veg"]},
]
_NUTRITION = {
    "foods": ["гречка", "тофу", "брокколи"],
    "calories": 400.0,
    "protein": 25.0,
    "fats": 12.0,
    "carbs": 50.0,
    "portion": "≈ 350г",
    "portion_grams": 350.0,
    "food_items": _ITEMS,
}


def _draft(**nutrition):
    draft = {
        "nutrition": {**_NUTRITION, **nutrition},
        "resolution_signals": {"portion_grams": 350.0},
    }
    return md.start(
        draft,
        header="Разбор фото:",
        badge="",
        detail_lines=[],
        estimate=True,
        base_grams=350.0,
        chips=True,
        basis="оценка по фото",
    )


# --- what the model says --------------------------------------------------------


def test_items_keep_known_groups_in_screen_order():
    items = fg.clean_items([{"name": " тофу ", "groups": ["anchor", "legumes"]}])
    assert items == [{"name": "тофу", "groups": ["legumes", "anchor"]}]


def test_garbage_tags_lose_the_tag_not_the_meal_and_are_logged(caplog):
    with caplog.at_level(logging.WARNING, logger=fg.__name__):
        items = fg.clean_items(
            [
                "тофу",
                {"name": "", "groups": ["veg"]},
                {"name": "суп", "groups": "veg"},
                {"name": "хлеб", "groups": ["grain", "vitamins", 7]},
            ]
        )
    assert items == [{"name": "хлеб", "groups": ["grain"]}]
    assert "dropped unknown groups" in caplog.text
    assert "malformed item" in caplog.text
    assert fg.clean_items("тофу") == []
    assert fg.clean_items(None) == []


def test_parse_nutrition_cleans_tags_and_a_reply_without_them_still_parses():
    raw = {**_NUTRITION, "food_items": [{"name": "тофу", "groups": ["nonsense"]}]}
    assert parse_nutrition(json.dumps(raw))["food_items"] == [
        {"name": "тофу", "groups": []}
    ]
    bare = {k: v for k, v in _NUTRITION.items() if k != "food_items"}
    assert "food_items" not in parse_nutrition(json.dumps(bare))


def test_the_prompt_names_every_group_key():
    for key in fg.GROUPS:
        assert f'"{key}"' in _FOOD_GROUPS_RULE


# --- the draft -------------------------------------------------------------------


def test_draft_shows_the_groups_line_in_screen_order():
    assert (
        "🏷 Группы: 🫘 бобовые · ⚓ белковая опора · 🥦 овощи · 🌾 цельные злаки"
        in md.text(_draft())
    )


def test_no_groups_means_no_line_at_all():
    for draft in (_draft(food_items=[]), _draft(food_items=None)):
        text = md.text(draft)
        assert "Группы" not in text and "нет" not in text


def test_groups_screen_has_all_eight_and_a_named_way_back():
    draft = _draft()
    draft["groups_open"] = True

    assert md.text(draft) == md.GROUPS_TEXT
    rows = md.markup(draft).inline_keyboard
    assert [len(row) for row in rows] == [3, 3, 2, 1]
    assert rows[-1][0].text == "⬅️ К записи"
    labels = [b.text for row in rows[:-1] for b in row]
    assert "⚓ Опора ✓" in labels and "🍎 Фрукты" in labels
    # The mark stays first, so custom_emoji can draw it; the check goes last.
    marks = [mark for mark, _label, _word in fg.GROUPS.values()]
    assert all(text.startswith(mark) for text, mark in zip(labels, marks))
    for row in rows:
        for button in row:
            assert len(button.callback_data.encode()) <= 64
            assert len(button.text) <= 12


def test_toggle_flips_one_group_and_marks_the_edit_as_the_users():
    draft = _draft()
    assert md.toggle_group(draft, "fruit")
    assert md.toggle_group(draft, "veg")
    assert draft["groups"] == ["legumes", "anchor", "fruit", "grain"]
    assert draft["groups_by_user"] is True


def test_toggle_refuses_a_key_it_does_not_know():
    draft = _draft()
    before = list(draft["groups"])
    assert md.toggle_group(draft, "vitamins") is False
    assert draft["groups"] == before and draft["groups_by_user"] is False


def test_saving_keeps_the_analysis_tags_and_the_confirmed_groups_side_by_side():
    draft = _draft()
    md.toggle_group(draft, "veg")
    md.set_size(draft, "l")

    analysis = md.stored_analysis(draft)
    assert analysis["food_items"] == _ITEMS  # what the model said
    assert analysis["food_groups"] == ["legumes", "anchor", "grain"]
    assert analysis["food_groups_by_user"] is True
    assert analysis["calories"] == 400.0  # before the portion change

    nutrients = md.stored_nutrients(draft)
    assert nutrients["food_groups"] == ["legumes", "anchor", "grain"]
    assert nutrients["calories"] == 560.0  # what is on screen


def test_a_new_analysis_of_a_corrected_draft_starts_from_its_own_tags():
    draft = _draft()
    md.toggle_group(draft, "veg")
    draft["nutrition"] = {**_NUTRITION, "food_items": [_ITEMS[0]]}
    md.start(
        draft,
        header="Разбор фото:",
        badge="",
        detail_lines=[],
        estimate=True,
        base_grams=350.0,
        chips=True,
        basis="оценка по фото",
    )
    assert draft["groups"] == ["grain"] and draft["groups_by_user"] is False


# --- a favorite -----------------------------------------------------------------


def test_favorite_keeps_groups_and_an_untagged_one_stays_untagged():
    nutrients = fg.stamp(_NUTRITION, ["veg"], by_user=True)
    assert fg.carried(nutrients) == {
        "food_items": _ITEMS,
        "food_groups": ["veg"],
        "food_groups_by_user": True,
    }
    assert fg.carried(_NUTRITION) is None
    assert fg.carried(None) is None
