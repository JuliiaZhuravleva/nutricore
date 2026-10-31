"""Food groups on a meal (stage 5, slice B): the tags, the draft, what is saved."""

import json
import logging

import pytest

from app.services import food_groups as fg
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


@pytest.mark.parametrize("nutrients", [None, [], 5, {"foods": ["тофу"]}])
def test_carried_is_none_for_a_favorite_without_tags(nutrients):
    assert fg.carried(nutrients) is None


def test_carried_survives_a_broken_stored_value(caplog):
    assert fg.carried({"food_groups": 5}) is None
    assert "not a list" in caplog.text


def test_an_analysis_without_tags_says_so_in_the_log(caplog):
    reply = {
        "foods": ["x"],
        "calories": 1,
        "protein": 1,
        "fats": 1,
        "carbs": 1,
        "portion": "1",
    }
    with caplog.at_level(logging.WARNING):
        parse_nutrition(dict(reply))
    assert "no food_items" in caplog.text


def test_omega_is_plant_sources_only():
    assert "plant omega-3 sources only" in _FOOD_GROUPS_RULE
    assert "fish" not in _FOOD_GROUPS_RULE.split('"omega"')[1].split("\n")[0]
