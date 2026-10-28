"""The confirm-step draft (``app.services.meal_draft``): numbers, portion, buttons.

Pure module — no Telegram I/O, no DB — so these run on plain dicts.
"""

import pytest

from app.services import meal_draft as md

_NUTRITION = {
    "foods": ["самса"],
    "calories": 250.0,
    "protein": 6.0,
    "fats": 12.5,
    "carbs": 30.0,
    "portion": "1 штука (~100г)",
    "portion_grams": 100.0,
}
_SIGNALS = {"portion_grams": 100.0, "portion_source": "vision"}


def _photo_draft(**overrides):
    draft = {"nutrition": dict(_NUTRITION), "resolution_signals": dict(_SIGNALS)}
    kwargs = dict(
        header="Фото:",
        badge="📷 оценка по фото",
        detail_lines=[],
        estimate=True,
        base_grams=100.0,
        chips=True,
        basis="оценка по фото",
    )
    kwargs.update(overrides)
    return md.start(draft, **kwargs)


# --- numbers -------------------------------------------------------------------


def test_num_drops_a_trailing_zero():
    assert md.num(250.0) == "250"
    assert md.num(22.24) == "22.2"
    assert md.num(140.0, 0) == "140"


def test_calories_are_a_range_only_for_an_estimate():
    assert md.calories_text(400, estimate=True) == "≈ 400 ккал (320–480)"
    assert md.calories_text(400.0, estimate=False) == "400 ккал"
    assert md.calories_text(None, estimate=True) == "—"
    assert md.calories_text(0, estimate=True) == "0 ккал"


def test_macros_are_whole_grams_for_an_estimate():
    assert md.macro_text(12.5, estimate=True) == "12г"
    assert md.macro_text(12.25, estimate=False) == "12.2г"
    assert md.macro_text(None, estimate=True) == "—"


# --- typed weight ----------------------------------------------------------------


@pytest.mark.parametrize(
    "text,grams",
    [
        ("150", 150.0),
        ("150 г", 150.0),
        ("150г.", 150.0),
        ("≈ 140 грамм", 140.0),
        ("0,5 кг", 500.0),
        ("200 g", 200.0),
    ],
)
def test_parse_grams_reads_a_weight(text, grams):
    assert md.parse_grams(text, unit_required=False) == grams


@pytest.mark.parametrize(
    "text", ["это самса", "2 штуки", "", None, "0", "9999 г", "150 ml"]
)
def test_parse_grams_refuses_what_is_not_a_weight(text):
    assert md.parse_grams(text, unit_required=False) is None


def test_a_bare_number_counts_only_when_a_weight_was_asked_for():
    # "2" in reply to the draft may mean two pieces; "200 г" is a weight.
    assert md.parse_grams("2", unit_required=True) is None
    assert md.parse_grams("200 г", unit_required=True) == 200.0


# --- portion changes ---------------------------------------------------------------


def test_set_grams_rescales_from_the_base_and_moves_the_gram_basis_everywhere():
    draft = _photo_draft()

    md.set_grams(draft, 140.0)

    n = draft["nutrition"]
    assert (n["calories"], n["protein"], n["fats"], n["carbs"]) == (
        350.0,
        8.4,
        17.5,
        42.0,
    )
    assert n["portion"] == "140г" and n["portion_grams"] == 140.0
    # The personal food DB divides by this: it must match the macros.
    assert draft["resolution_signals"]["portion_grams"] == 140.0
    assert draft["resolution_signals"]["portion_source"] == "user"
    assert draft["resolution_signals"]["portion_grams_estimated"] == 100.0
    # The analysis as it came is kept for ai_analysis.
    assert draft["base_nutrition"]["calories"] == 250.0
    assert "Порция: 140г (твой вес)" in md.text(draft)


def test_size_chips_do_not_drift_when_tapped_back_and_forth():
    draft = _photo_draft()

    for size in ("l", "s", "l", "s", "m"):
        assert md.set_size(draft, size)

    assert draft["nutrition"] == _NUTRITION
    assert draft["resolution_signals"] == _SIGNALS
    assert not draft["grams_by_user"]


def test_a_size_chip_shows_its_grams_and_marks_the_current_one():
    draft = _photo_draft()
    md.set_size(draft, "l")

    chips = [b.text for b in md.markup(draft).inline_keyboard[0]]

    assert chips == ["Маленькая 70г", "Средняя 100г", "✓ Большая 140г"]
    assert "Порция: ≈ 140г (большая)" in md.text(draft)


def test_size_chips_refuse_a_draft_without_an_estimate():
    draft = _photo_draft(chips=False)
    assert not md.set_size(draft, "l")
    assert draft["nutrition"] == _NUTRITION
    # No chips row: the first row is the two corrections.
    first = [b.text for b in md.markup(draft).inline_keyboard[0]]
    assert first == ["✏️ Поправить вес", "✏️ Поправить продукт"]


def test_without_a_gram_basis_only_the_product_can_be_fixed():
    draft = _photo_draft(base_grams=None, chips=False, basis=None)
    assert not md.can_rescale(draft)
    first = [b.text for b in md.markup(draft).inline_keyboard[0]]
    assert first == ["✏️ Поправить продукт"]


# --- the message -----------------------------------------------------------------


def test_portion_line_names_the_models_words_and_the_basis():
    assert "Порция: 1 штука (~100г) (оценка по фото)" in md.text(_photo_draft())
    bare = _photo_draft()
    bare["nutrition"]["portion"] = "120г"
    bare["base_grams"] = 120.0
    assert "Порция: ≈ 120г (оценка по фото)" in md.text(bare)


def test_per_100g_draft_says_so_next_to_the_fix():
    draft = _photo_draft(per_100g=True, chips=False, basis=None)
    assert "Порция: не определена — числа на 100г, поправь вес кнопкой" in md.text(
        draft
    )


def test_the_closing_line_follows_what_the_draft_waits_for():
    draft = _photo_draft()
    assert md.text(draft).endswith("Всё верно? Сохрани или поправь кнопками ниже.")
    draft["awaiting"] = "grams"
    assert md.text(draft).endswith("Напиши вес в граммах, например 150.")
    draft["awaiting"] = "food"
    assert "фото останется" in md.text(draft)
    assert md.text(draft, footer="✅ Сохранено").endswith("✅ Сохранено")


# --- callback data ----------------------------------------------------------------


def test_every_button_carries_the_draft_id_and_fits_telegrams_64_bytes():
    draft = _photo_draft()
    for row in md.markup(draft).inline_keyboard:
        for button in row:
            data = button.callback_data
            assert len(data.encode()) <= 64
            draft_id, verb, _arg = md.parse_callback(data)
            assert draft_id == draft["id"] and verb


@pytest.mark.parametrize(
    "data", [None, "", "fav:1:2", "meal:", "meal:abc", "meal::s", "meal:a:b:c:d"]
)
def test_parse_callback_refuses_malformed_data(data):
    assert md.parse_callback(data) is None


def test_draft_ids_differ_between_drafts():
    assert len({md.new_draft_id() for _ in range(20)}) == 20
