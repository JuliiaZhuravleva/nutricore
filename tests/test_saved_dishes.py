"""A typed or spoken meal looks in the person's own dishes first (2026-11-10).

Through the real application at the wire, on real rows, for the typed meal; the model
and the two pgvector seams (``has_embeddings``, ``nearest``) are the fakes. No network,
no paid call.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.crud.crud_personal_food import crud_personal_food
from app.models import Meal, MealItem
from app.models.personal_food import PersonalFood
from app.services import capture, meal_card, saved_dishes
from app.services import telegram as tg
from app.services.openai_service import OpenAIService
from tests.test_capture_router import (  # noqa: F401 (fixtures)
    OWNER,
    Session,
    db,
    owner,
    send,
    text,
)
from tests.test_openai_service import _create_kwargs, _service_with_mock_client

SAVED = "Запеканка творожная"


class Model:
    """The model: what it was asked, and the analysis it gives back."""

    def __init__(self, answer: dict):
        self.answer = answer
        self.asked = []
        self.embedded = []

    def model_for(self, task):
        return "gpt-test"

    async def embed_text(self, words, *, input_ref=None):
        self.embedded.append((words, input_ref))
        return [0.0] * 3

    async def analyze_food_entry(self, words, *, spoken=False, saved=None):
        self.asked.append({"words": words, "spoken": spoken, "saved": saved})
        return json.dumps(self.answer, ensure_ascii=False)


def _answer(*items, calories=170.0, grams=40.0) -> dict:
    """The model's reply: ``items`` are (name, saved number or None)."""
    return {
        "foods": [name for name, _ in items],
        "food_items": [
            {"name": name, "groups": ["calcium"], "saved": number}
            for name, number in items
        ],
        "calories": calories,
        "protein": 6.0,
        "fats": 9.0,
        "carbs": 17.0,
        "portion": f"{grams:.0f} г",
        "portion_grams": grams,
    }


@pytest.fixture
def dish(db, owner):
    row = PersonalFood(
        user_id=owner.id,
        canonical_name=SAVED,
        per_100g_calories=400,
        per_100g_proteins=8,
        per_100g_fats=25,
        per_100g_carbs=37,
        usual_grams=45.0,
        times_used=3,
    )
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def near(monkeypatch):
    """The pgvector seams: which dishes are nearest, by id."""
    state = SimpleNamespace(ids=[], embeddings=True)
    monkeypatch.setattr(
        crud_personal_food, "has_embeddings", lambda db, user_id: state.embeddings
    )

    def nearest(db, *, embedding, user_id, limit):
        return [(db.get(PersonalFood, i), 0.485) for i in state.ids][:limit]

    monkeypatch.setattr(crud_personal_food, "nearest", nearest)
    return state


@pytest.fixture
def model(monkeypatch):
    """The model behind the real ``_analyze_and_parse``; the call log is not kept."""
    box = SimpleNamespace(model=None)

    def answering(answer):
        box.model = Model(answer)
        monkeypatch.setattr(tg.telegram_service, "openai_service", box.model)
        return box.model

    async def no_log(coro, *, parse, **_):
        return parse(await coro)

    monkeypatch.setattr(tg, "analyze_and_log", no_log)
    return answering


def _meal(db) -> Meal:
    db.expire_all()
    return db.query(Meal).one()


def _items(db, meal) -> list:
    return list(
        db.scalars(
            select(MealItem)
            .where(MealItem.meal_id == meal.id, MealItem.removed_at.is_(None))
            .order_by(MealItem.position)
        )
    )


def _dishes(db) -> int:
    db.expire_all()
    return db.scalar(select(func.count(PersonalFood.id)))


# --- the typed meal, at the wire ---------------------------------------------------


def test_a_typed_known_dish_is_that_dish_with_its_numbers(db, dish, near, model):
    near.ids = [dish.id]
    asked = model(_answer(("запеканка творожная с изюмом", 1)))

    wire = send(text("съела творожную запеканку с изюмом"))

    meal = _meal(db)
    (item,) = _items(db, meal)
    assert item.personal_food_id == dish.id
    assert item.name == SAVED
    assert _dishes(db) == 1  # no twin in the base
    assert meal.resolution_source == "saved_rag"
    assert meal.resolution_signals["saved_match_source"] == "saved_text"
    # The dish's 400 kcal / 100 g at the 40 g eaten, not the model's 170.
    assert float(meal.calories) == pytest.approx(160.0)
    assert item.numbers_source == "dish"
    assert SAVED in wire.edits()[-1]["text"]
    assert meal_card._source_line(meal) == "<i>⭐ Из твоей базы</i>"
    (call,) = asked.asked
    assert call["saved"] == [{"n": 1, "name": SAVED, "serving_g": 45}]
    # The typed words name the embedding's log row; they are already in the text's.
    assert asked.embedded == [
        (
            "съела творожную запеканку с изюмом",
            "съела творожную запеканку с изюмом",
        )
    ]


def test_a_plate_links_the_known_dish_and_keeps_the_models_numbers(
    db, dish, near, model
):
    near.ids = [dish.id]
    model(_answer(("творожная запеканка", 1), ("чай", None), calories=175.0))

    send(text("запеканка и чай"))

    meal = _meal(db)
    first, second = _items(db, meal)
    assert (first.name, first.personal_food_id) == (SAVED, dish.id)
    assert second.name == "чай" and second.personal_food_id != dish.id
    assert meal.resolution_source is None
    assert float(meal.calories) == pytest.approx(175.0)
    assert _dishes(db) == 2  # чай is new, the запеканка is not


def test_a_dish_the_model_did_not_pick_is_a_new_one_as_before(db, dish, near, model):
    near.ids = [dish.id]
    model(_answer(("творог", None)))

    send(text("творог"))

    meal = _meal(db)
    (item,) = _items(db, meal)
    assert item.name == "творог" and item.personal_food_id != dish.id
    assert meal.resolution_source is None
    assert _dishes(db) == 2


def test_a_number_that_was_never_sent_names_nothing(db, dish, near, model):
    near.ids = [dish.id]
    model(_answer(("запеканка", 5)))

    send(text("запеканка"))

    (item,) = _items(db, _meal(db))
    assert item.name == "запеканка" and item.personal_food_id != dish.id


def test_no_dishes_to_find_costs_no_embedding_and_offers_nothing(db, dish, near, model):
    near.embeddings = False
    asked = model(_answer(("запеканка", None)))

    send(text("запеканка"))

    assert asked.embedded == []
    assert asked.asked[0]["saved"] is None


def test_a_failed_lookup_still_reads_the_meal(
    db, dish, near, model, monkeypatch, caplog
):
    def broken(db, *, embedding, user_id, limit):
        raise RuntimeError("pgvector gone")

    monkeypatch.setattr(crud_personal_food, "nearest", broken)
    asked = model(_answer(("запеканка", None)))

    send(text("запеканка"))

    assert asked.asked[0]["saved"] is None
    assert _items(db, _meal(db))[0].name == "запеканка"
    assert "lookup failed at nearest" in caplog.text


def test_a_failed_embedding_logs_no_words(db, dish, near, model, caplog):
    asked = model(_answer(("запеканка", None)))

    async def broken(words, *, input_ref=None):
        raise RuntimeError(f"bad input: {words}")

    asked.embed_text = broken

    send(text("тайное слово"))

    assert "lookup failed at embed" in caplog.text
    assert "тайное слово" not in caplog.text
    assert _items(db, _meal(db))[0].name == "запеканка"


def test_a_broken_dish_lookup_after_the_read_keeps_the_meal(
    db, dish, near, model, monkeypatch, caplog
):
    near.ids = [dish.id]
    model(_answer(("запеканка", 1)))

    def broken(*_, **__):
        raise RuntimeError("no macros today")

    monkeypatch.setattr(saved_dishes, "_has_usable_macros", broken)

    send(text("запеканка"))

    meal = _meal(db)
    (item,) = _items(db, meal)
    assert item.personal_food_id == dish.id
    assert meal.resolution_source is None
    assert float(meal.calories) == pytest.approx(170.0)
    assert "could not take the dish's numbers" in caplog.text


def test_every_lookup_says_how_many_it_found(db, dish, near, model, caplog):
    caplog.set_level("INFO", logger="app.services.saved_dishes")
    near.ids = [dish.id]
    model(_answer(("творог", None)))

    send(text("творог"))

    assert "1 candidates for user" in caplog.text


def test_a_dish_without_numbers_links_but_keeps_the_models_numbers(
    db, dish, near, model
):
    dish.per_100g_calories = None
    db.commit()
    near.ids = [dish.id]
    model(_answer(("запеканка", 1)))

    send(text("запеканка"))

    meal = _meal(db)
    (item,) = _items(db, meal)
    assert item.personal_food_id == dish.id
    assert meal.resolution_source is None
    assert float(meal.calories) == pytest.approx(170.0)


def test_a_name_that_matches_without_the_mark_gets_no_dish_numbers(
    db, dish, near, model
):
    # «кофе чёрный» against a saved latte: the model says it is not that one.
    near.ids = [dish.id]
    model(_answer((SAVED, None)))

    send(text("другая запеканка"))

    meal = _meal(db)
    assert meal.resolution_source is None
    assert float(meal.calories) == pytest.approx(170.0)


def test_an_item_worded_apart_from_its_food_still_takes_the_dish(db, dish, near, model):
    near.ids = [dish.id]
    answer = _answer(("запеканка", 1))
    answer["foods"] = ["творожная запеканка"]
    model(answer)

    send(text("запеканка"))

    meal = _meal(db)
    (item,) = _items(db, meal)
    assert item.personal_food_id == dish.id
    assert meal.resolution_source == "saved_rag"
    assert _dishes(db) == 1


def test_a_default_weight_is_not_offered_as_the_usual_serving(db, dish, near, model):
    dish.usual_grams = None
    db.commit()
    near.ids = [dish.id]
    asked = model(_answer(("творог", None)))

    send(text("творог"))

    assert asked.asked[0]["saved"] == [{"n": 1, "name": SAVED, "serving_g": None}]


# --- a voice note ------------------------------------------------------------------


def test_spoken_words_go_in_no_log_row(db, dish, near, model, monkeypatch, Session):
    near.ids = [dish.id]
    asked = model(_answer(("запеканка", 1)))

    async def words(bot, job):
        return "творожная запеканка"

    monkeypatch.setattr(tg, "_voice_words", words)
    job = SimpleNamespace(kind=capture.VOICE, file_id="voice-file", telegram_id=OWNER)

    nutrition, found = asyncio.run(tg._reading(None, job))

    assert asked.embedded == [("творожная запеканка", "voice-file")]
    assert asked.asked[0]["spoken"] is True
    assert nutrition["foods"] == [SAVED]
    assert found.source == "saved_rag"
    assert found.signals["saved_food_id"] == dish.id


# --- the pieces --------------------------------------------------------------------


def test_the_model_sees_the_dishes_in_the_user_message_only():
    service = _service_with_mock_client()
    offered = [{"n": 1, "name": SAVED, "serving_g": 45}]

    asyncio.run(service.analyze_food_entry("запеканка", saved=offered))

    system, user = _create_kwargs(service)["messages"]
    assert SAVED not in system["content"]
    assert "Saved dishes" in system["content"]
    assert SAVED in user["content"] and '"serving_g": 45' in user["content"]


def test_without_dishes_the_prompt_is_as_before():
    service = _service_with_mock_client()

    asyncio.run(service.analyze_food_entry("запеканка"))

    system, user = _create_kwargs(service)["messages"]
    assert "Saved dishes" not in system["content"]
    assert "Saved dishes" not in user["content"]


def test_embedding_log_names_the_given_reference(monkeypatch):
    logged = []
    monkeypatch.setattr(
        "app.services.openai_service.record_ai_call", lambda **kw: logged.append(kw)
    )
    service = OpenAIService()

    async def create(**_):
        return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1])], usage=None)

    service.client.embeddings.create = create

    asyncio.run(service.embed_text("сказанные слова", input_ref="voice-file"))

    assert logged[0]["input_ref"] == "voice-file"


@pytest.mark.parametrize(
    "number, renamed",
    [
        (1, True),
        ("1", True),
        (1.0, True),
        (2, False),
        (True, False),
        (None, False),
        ("x", False),
        (1.5, False),
    ],
)
def test_only_a_number_that_was_sent_renames(number, renamed):
    found = (saved_dishes.Candidate(7, SAVED, 0.4, 45.0),)
    data = {
        "foods": ["запеканка"],
        "food_items": [{"name": "запеканка", "groups": [], "saved": number}],
    }

    saved_dishes._take_saved(data, found)

    expected = SAVED if renamed else "запеканка"
    assert data["foods"] == [expected]
    assert data["food_items"] == [{"name": expected, "groups": []}]


# --- review 2026-11-10: what the model says back never fails a read ---------------


@pytest.mark.parametrize("number", ["²", "①", "1" * 5000, " 1 "])
def test_a_number_int_cannot_read_is_dropped_not_raised(number):
    found = (saved_dishes.Candidate(7, SAVED, 0.4, 45.0),)
    data = {
        "foods": ["запеканка"],
        "food_items": [{"name": "запеканка", "saved": number}],
    }

    saved_dishes._take_saved(data, found)

    expected = [7] if number == " 1 " else []
    assert data[saved_dishes.MARKED] == expected


def test_a_superscript_mark_still_saves_the_meal(db, dish, near, model, caplog):
    near.ids = [dish.id]
    model(_answer(("запеканка", "²")))

    send(text("запеканка"))

    (item,) = _items(db, _meal(db))
    assert item.name == "запеканка" and item.personal_food_id != dish.id
    assert "dish number is not a number" in caplog.text


def test_marks_that_cannot_be_read_leave_the_read_standing(
    db, dish, near, model, monkeypatch, caplog
):
    near.ids = [dish.id]
    model(_answer(("запеканка", 1)))

    def broken(*_):
        raise RuntimeError("marks")

    monkeypatch.setattr(saved_dishes, "_take_saved", broken)

    send(text("запеканка"))

    meal = _meal(db)
    assert float(meal.calories) == pytest.approx(170.0)
    assert meal.resolution_source is None
    assert "could not read the model's dish marks" in caplog.text


def test_one_dish_whose_serving_fails_leaves_out_only_itself(
    db, dish, owner, near, model, monkeypatch, caplog
):
    other = PersonalFood(user_id=owner.id, canonical_name="Сломанное блюдо")
    db.add(other)
    db.commit()
    near.ids = [other.id, dish.id]
    real = saved_dishes.meal_items.dish_portion

    def portion(session, row):
        if row.id == other.id:
            raise RuntimeError("bad favourite")
        return real(session, row)

    monkeypatch.setattr(saved_dishes.meal_items, "dish_portion", portion)
    asked = model(_answer(("запеканка", 1)))

    send(text("запеканка"))

    assert asked.asked[0]["saved"] == [{"n": 1, "name": SAVED, "serving_g": 45}]
    assert f"dish {other.id} left out" in caplog.text
    assert "lookup failed" not in caplog.text


def test_dishes_the_model_left_unmarked_leave_a_line(db, dish, near, model, caplog):
    caplog.set_level("INFO", logger="app.services.saved_dishes")
    near.ids = [dish.id]
    model(_answer(("творог", None)))

    send(text("творог"))

    assert "0 of 1 sent marked, 1 foods eaten" in caplog.text


def test_dishes_sent_but_never_checked_for_marks_is_a_warning(caplog):
    found = (saved_dishes.Candidate(7, SAVED, 0.4, 45.0),)

    assert saved_dishes.resolution_for({"foods": ["x"]}, found, None) is None
    assert "the reply was not checked for marks" in caplog.text


def test_no_candidates_after_an_embedding_is_a_warning(db, dish, near, model, caplog):
    near.ids = []
    model(_answer(("творог", None)))

    send(text("творог"))

    assert "0 candidates for user" in caplog.text
    assert [r.levelname for r in caplog.records if "0 candidates" in r.message] == [
        "WARNING"
    ]


def test_a_typed_dish_says_its_own_strategy(db, dish, near, model):
    near.ids = [dish.id]
    model(_answer(("запеканка", 1)))

    send(text("запеканка"))

    signals = _meal(db).resolution_signals
    assert signals["strategy_chosen"] == signals["strategy_tried"][0] == "saved_text"


def test_hidden_dishes_alone_cost_no_embedding(db, dish, owner):
    from app.models.personal_food import PersonalFoodEmbedding

    db.add(
        PersonalFoodEmbedding(
            personal_food_id=dish.id, text_embedded=SAVED, embedding=[0.25] * 1536
        )
    )
    db.commit()
    assert crud_personal_food.has_embeddings(db, owner.id)  # the known positive

    dish.hidden_at = func.now()
    db.commit()
    assert not crud_personal_food.has_embeddings(db, owner.id)


def test_a_plate_whose_foods_word_the_dish_apart_still_links_it(db, dish, near, model):
    near.ids = [dish.id]
    answer = _answer((SAVED, 1), ("чай", None), calories=175.0)
    answer["foods"] = ["запеканка с изюмом", "чай"]
    model(answer)

    send(text("запеканка и чай"))

    first = _items(db, _meal(db))[0]
    assert (first.name, first.personal_food_id) == (SAVED, dish.id)
    assert _dishes(db) == 2  # чай is new, the dish is not


def test_one_dish_without_numbers_worded_apart_still_links(db, owner, near, model):
    bare = PersonalFood(user_id=owner.id, canonical_name=SAVED)
    db.add(bare)
    db.commit()
    near.ids = [bare.id]
    answer = _answer((SAVED, 1))
    answer["foods"] = ["запеканка с изюмом"]
    model(answer)

    send(text("запеканка"))

    (item,) = _items(db, _meal(db))
    assert item.personal_food_id == bare.id
    assert _dishes(db) == 1
