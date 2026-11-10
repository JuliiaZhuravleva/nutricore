"""A meal is called by its dishes, not by the words it was sent with (2026-11-10)."""

from types import SimpleNamespace

import pytest

from app.services import stats_screen
from app.services.favorites import meal_title
from app.services.meal_items import product_dish_name


def _meal(description, foods=None):
    nutrients = {"foods": foods} if foods is not None else {}
    return SimpleNamespace(description=description, nutrients=nutrients)


def test_the_dishes_name_the_meal_not_the_words():
    meal = _meal(
        "на обед был суп и кусок хлеба",
        ["гороховый суп", "ржаной хлеб"],
    )
    assert meal_title(meal) == "гороховый суп, ржаной хлеб"


def test_the_words_name_a_meal_no_dish_was_read_from():
    assert meal_title(_meal("перекус яблоком")) == "перекус яблоком"
    assert meal_title(_meal("перекус яблоком", [])) == "перекус яблоком"
    assert meal_title(_meal(None, [" ", ""])) == "Блюдо"


def test_a_long_dish_list_is_cut_on_screens():
    meal = _meal("x", ["рис", "куриное филе", "брокколи", "морковь", "кунжут"])
    name = stats_screen._meal_name(meal)
    assert name.startswith("рис, куриное филе")
    assert name.endswith("…") and len(name) <= stats_screen.NAME_MAX


@pytest.mark.parametrize(
    "label, dish",
    [
        ("Oreo печенье (Mondelez, 95 g)", "Oreo печенье"),
        ("Кефир 2,5% — Молочный завод", "Кефир 2,5%"),
        ("Хлеб — Бренд (500 г)", "Хлеб"),
        ("запечённая индейка (грудка, USDA)", "запечённая индейка"),
        ("Coca-Cola Zero", "Coca-Cola Zero"),
        ("Greek Yogurt", "Greek Yogurt"),
        # A name that is nothing but a tail keeps it: an empty dish name is worse.
        ("(82 g)", "(82 g)"),
        # What tells one product from another is not a package.
        ("Кефир (безлактозный)", "Кефир (безлактозный)"),
        ("Йогурт (клубника)", "Йогурт (клубника)"),
        ("Борщ — домашний", "Борщ — домашний"),
        (None, ""),
    ],
)
def test_a_product_name_loses_its_package_tail(label, dish):
    assert product_dish_name(label) == dish
