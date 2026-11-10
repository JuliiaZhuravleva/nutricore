"""A typed or spoken meal looks in the person's own dishes first (2026-11-10).

A photo finds a saved dish by an embedding threshold (``SavedFoodRAGStrategy``). Typed
words cannot: a dish typed in everyday words sat 0.49 from the same dish saved under
its package name, against a threshold of 0.15, while unrelated dishes sat from 0.57. No
threshold both finds it and keeps them apart.

So the nearest few dishes go into the same analysis call, by name, and the model says
which one, if any, was eaten. Nothing it says back is trusted on its own: a number
counts only if it is one of the dishes sent, and the name written is the dish's own.
Then the meal links to that dish (no twin in the base), and one dish eaten gets the
dish's numbers, as a photo's saved match does.

The lookup costs one embedding and never stops a read: any failure reads without it.
"""

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

from sqlalchemy.orm import Session

from app.crud.crud_personal_food import crud_personal_food
from app.crud.crud_user import crud_user
from app.db.session import SessionLocal
from app.models.personal_food import PersonalFood
from app.services import meal_items
from app.services.model_registry import loads_model_json
from app.services.product_lookup_service import (
    ImageSignals,
    ResolutionResult,
    SavedFoodRAGStrategy,
    _has_usable_macros,
    _parse_portion_grams,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.services.openai_service import OpenAIService

logger = logging.getLogger(__name__)

# How many dishes the model sees: enough for a dish and its twins under other names.
CANDIDATES = 8
MATCH_SOURCE = "saved_text"


@dataclass(frozen=True)
class Candidate:
    dish_id: int
    name: str
    distance: float
    serving_grams: Optional[float]
    assumed: bool = False  # the grams are a default nobody gave: not shown as usual


# Which dishes the model marked, kept on the parsed reply until ``resolution_for``
# takes it off: the mark, not a name that happens to match, says what was eaten.
MARKED = "_saved_marked"


def _key(name: str) -> str:
    return name.strip().casefold()


async def candidates(
    svc: "OpenAIService",
    telegram_id: Optional[int],
    words: Optional[str],
    *,
    input_ref: str,
    sessions: Callable = SessionLocal,
) -> tuple:
    """The person's dishes nearest the words, nearest first; () if none or on any
    failure. ``input_ref`` is what the embedding's log row names: never spoken words.
    ``sessions``: the caller's session factory, so its database is the one asked.
    Every lookup leaves one log line, so "nothing near" and "never looked" differ."""
    if telegram_id is None or not (words or "").strip():
        return ()
    stage = "user"
    try:
        with sessions() as db:
            user = crud_user.get_by_telegram_id(db, telegram_id=telegram_id)
            if user is None:
                logger.info("saved dishes: no lookup for %s (no_user)", telegram_id)
                return ()
            stage = "has_embeddings"
            if not crud_personal_food.has_embeddings(db, user.id):
                logger.info(
                    "saved dishes: no lookup for user %s (no_embeddings)", user.id
                )
                return ()
            user_id = user.id
        stage = "embed"
        embedding = await svc.embed_text(words, input_ref=input_ref)
        stage = "nearest"
        with sessions() as db:
            near = crud_personal_food.nearest(
                db, embedding=embedding, user_id=user_id, limit=CANDIDATES
            )
            found = tuple(
                cand
                for cand in (_candidate(db, dish, dist) for dish, dist in near)
                if cand is not None
            )
        if not found:
            # The user has embedded live dishes and no threshold applies, so none
            # coming back means the base changed under us or something broke.
            logger.warning("saved dishes: 0 candidates for user %s", user_id)
            return found
        logger.info(
            "saved dishes: %d candidates for user %s (nearest %.3f)",
            len(found),
            user_id,
            found[0].distance,
        )
        return found
    except Exception as exc:
        # The embedding's error can echo the words, so that stage logs no traceback;
        # its row in the AI call log keeps the error text.
        logger.warning(
            "saved dishes: lookup failed at %s for %s, reading without them (%s)",
            stage,
            telegram_id,
            type(exc).__name__,
            exc_info=stage != "embed",
        )
        return ()


def _candidate(db: Session, dish: PersonalFood, distance: float) -> Optional[Candidate]:
    """One dish as the model sees it; None when its serving cannot be worked out, so
    one broken dish costs only itself, not the lookup."""
    try:
        portion = meal_items.dish_portion(db, dish)
    except Exception as exc:
        logger.warning(
            "saved dishes: dish %s left out, its serving failed (%s)",
            dish.id,
            type(exc).__name__,
            exc_info=True,
        )
        return None
    return Candidate(
        dish_id=dish.id,
        name=dish.canonical_name,
        distance=distance,
        serving_grams=portion.grams,
        assumed=portion.assumed,
    )


def prompt_list(found: tuple) -> list:
    """What the model sees of each dish: its number, name and usual serving."""
    return [
        {
            "n": n,
            "name": cand.name,
            # A default weight is nobody's serving: the model estimates instead.
            "serving_g": (
                round(cand.serving_grams)
                if cand.serving_grams and not cand.assumed
                else None
            ),
        }
        for n, cand in enumerate(found, 1)
    ]


def parser(found: tuple, parse: Callable) -> Callable:
    """``parse`` that first writes the dishes the model named under their own names.
    The number goes here, before ``parse`` cleans the items and drops it."""

    def run(raw):
        data = loads_model_json(raw) if isinstance(raw, str) else raw
        if isinstance(data, dict):
            try:
                _take_saved(data, found)
            except Exception as exc:
                # An optional field never fails a paid read: read without the marks.
                logger.warning(
                    "saved dishes: could not read the model's dish marks (%s)",
                    type(exc).__name__,
                    exc_info=True,
                )
                data[MARKED] = []
        return parse(data)

    return run


def _number(value: object) -> Optional[int]:
    """The model's ``saved`` as a whole number: 2, "2" and 2.0 are 2; else None.
    Only ASCII digits, and a few of them: "²".isdigit() is true and int() refuses it."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        value = value.strip()
        if value.isascii() and value.isdigit() and len(value) <= 3:
            return int(value)
    return None


def _take_saved(data: dict, found: tuple) -> None:
    """Pop each item's ``saved`` number; an item that names a dish sent takes the
    dish's name, and the dish's id goes under ``MARKED``. Anything else is warned
    about and dropped: the model's number counts only if it is one we sent."""
    by_number = dict(enumerate(found, 1))
    renamed, marked, at = {}, [], {}
    items = data.get("food_items")
    for index, entry in enumerate(items if isinstance(items, list) else []):
        if not isinstance(entry, dict):
            continue
        raw = entry.pop("saved", None)
        if raw is None:
            continue
        number = _number(raw)
        if number is None:
            logger.warning(
                "saved dishes: the model's dish number is not a number (%s)",
                type(raw).__name__,
            )
            continue
        cand = by_number.get(number)
        if cand is None:
            logger.warning(
                "saved dishes: the model named dish %s of %d sent", number, len(found)
            )
            continue
        name = entry.get("name")
        if not isinstance(name, str):
            logger.warning(
                "saved dishes: dish %s named on an item with no name", number
            )
            continue
        renamed[_key(name)] = cand.name
        at[index] = cand.name
        marked.append(cand.dish_id)
        entry["name"] = cand.name
    data[MARKED] = marked
    foods = data.get("foods")
    if isinstance(foods, str):
        foods = [foods]
    if not renamed or not isinstance(foods, list):
        return
    # One item per food, in order (the food groups rule): the item's place names
    # its food even when the two are worded apart, or the food makes a twin dish.
    same_order = isinstance(items, list) and len(items) == len(foods)
    data["foods"] = [
        (
            at[i]
            if same_order and i in at
            else renamed.get(_key(food), food) if isinstance(food, str) else food
        )
        for i, food in enumerate(foods)
    ]


def resolution_for(
    nutrition: dict,
    found: tuple,
    telegram_id: Optional[int],
    *,
    sessions: Callable = SessionLocal,
) -> Optional[ResolutionResult]:
    """One food eaten, and it is one of the dishes sent: the dish's numbers at the
    eaten grams. A plate keeps the model's numbers; its names still link to dishes.
    Never raises: the read is paid for by now, and the model's numbers still stand."""
    marked = nutrition.pop(MARKED, None)
    if marked is None:
        if found:
            # Dishes were sent but the reply never passed ``parser``: a wiring bug.
            logger.warning(
                "saved dishes: %d dishes sent, the reply was not checked for marks",
                len(found),
            )
        marked = []
    try:
        return _resolution(nutrition, found, marked, telegram_id, sessions)
    except Exception as exc:
        logger.warning(
            "saved dishes: could not take the dish's numbers, keeping the model's (%s)",
            type(exc).__name__,
            exc_info=True,
        )
        return None


def _resolution(
    nutrition: dict,
    found: tuple,
    marked: list,
    telegram_id: Optional[int],
    sessions: Callable,
) -> Optional[ResolutionResult]:
    if not found:
        return None
    foods = nutrition.get("foods") or []
    # Every read with dishes sent says what the model did with them, so a model that
    # never marks one is told apart from a lookup that never ran.
    logger.info(
        "saved dishes: %d of %d sent marked, %d foods eaten",
        len(marked),
        len(found),
        len(foods),
    )
    if len(foods) != 1 or len(marked) != 1:
        return None
    cand = next((c for c in found if c.dish_id == marked[0]), None)
    if cand is None:
        logger.warning("saved dishes: marked dish %s was not sent", marked[0])
        return None
    with sessions() as db:
        dish = db.get(PersonalFood, cand.dish_id)
        if dish is None or dish.hidden_at is not None or dish.merged_into_id:
            logger.info(
                "saved dishes: dish %s is gone since the lookup, keeping the model's "
                "numbers",
                cand.dish_id,
            )
            return None
        if not _has_usable_macros(dish):
            logger.info(
                "saved dishes: dish %s has no numbers, keeping the model's", dish.id
            )
            return None
        signals = ImageSignals(
            image_data_url="",
            barcode=None,
            vision_result=nutrition,
            portion_grams=_parse_portion_grams(nutrition),
            telegram_id=telegram_id,
        )
        result = SavedFoodRAGStrategy.build_result(
            pf=dish,
            signals_input=signals,
            distance=cand.distance,
            match_source=MATCH_SOURCE,
            query_text=cand.name,
        )
        dish_id = dish.id
    result.signals["strategy_tried"] = [MATCH_SOURCE]
    result.signals["strategy_chosen"] = MATCH_SOURCE
    result.signals["strategy_attempts"] = [{"strategy": MATCH_SOURCE, "outcome": "hit"}]
    result.signals["caption"] = None
    result.signals["portion_source"] = "text"
    if "food_items" in nutrition:
        result.nutrition["food_items"] = nutrition["food_items"]
    logger.info("saved dishes: a typed meal is dish %s", dish_id)
    return result
