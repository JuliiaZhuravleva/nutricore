"""A meal's dishes: written at save, changed one at a time (stage 5, slice N).

The rules of the sum live here (docs/stages/05-gentle-review-screens.md, 3.1):

- ``items``: totals are the sum of the live items' known numbers.
- ``plate``: totals are ``plate_totals`` plus the known numbers of items added later.
  A line read from the plate is ``plate_share``: it has no numbers, they are inside the
  plate's, and nothing here splits them. A line without numbers added later is ``none``
  and can still get its own, so the two must not look alike or the plate counts twice.
- ``none``: nothing was read, totals stay NULL.
- A meal from before items keeps its totals until its first ➕, which moves them to
  ``plate_totals`` as they are.

Every public function is one transaction, so nothing here calls ``upsert`` or
``crud_meal.create``: both commit by themselves. A change also rewrites ``food_items``,
``food_groups``, ``nutrients["foods"]`` and ``description``, as new dicts because a
plain JSON column does not notice a change in place, or the day card and the numbers
disagree.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crud.crud_action_event import crud_action_event
from app.crud.crud_favorite_meal import crud_favorite_meal
from app.crud.crud_meal import crud_meal
from app.crud.crud_meal_item import NAME_MAX, crud_meal_item
from app.crud.crud_personal_food import crud_personal_food
from app.crud.crud_record_event import crud_record_event
from app.models.action_event import SAVED
from app.models.favorite_meal import FavoriteMeal
from app.models.meal import Meal
from app.models.meal_item import MealItem
from app.models.meal_message import MealMessage
from app.models.meal_source import MealSource
from app.models.personal_food import PersonalFood
from app.models.record_event import RecordEvent
from app.schemas.meal import MealCreate
from app.services import food_groups
from app.services.product_lookup_service import _parse_portion_grams, has_usable_macros

# What a change did. SAME wrote nothing and no event: the toast is ``SAME_TOAST``.
EXACT_SOURCES = frozenset({"barcode", "label", "user"})
OWED_VIEW = "owed"  # a meal's line whose last word Telegram has not taken yet
LINE_VIEWS = ("pending", "line", OWED_VIEW)  # the rows that are a meal's own line
DONE = "done"
DELETED = "deleted"
SAME = "same"
STALE = "stale"
NOT_APPLICABLE = "not_applicable"
REFUSED = "refused"

SAME_TOAST = "Уже так"
UNDO_REFUSED = "<i>С тех пор запись менялась, поэтому возврат не применён.</i>"

EXACT_NAME_SOURCE = "exact_name"
TAG_DISH = "из твоих блюд"
TAG_DISH_UNVERIFIED = "из твоих блюд, не проверено"
# Numbers on 100 g with no weight anywhere count this much, or the dish's guess if it
# has one, until ⚖️ Порция says more.
DEFAULT_GRAMS = 100.0

FIELDS = ("calories", "proteins", "fats", "carbohydrates")
# The same four numbers as a draft's nutrition, and so as ``nutrients``, calls them.
_NUTRIENT_KEYS = dict(zip(FIELDS, ("calories", "protein", "fats", "carbs")))
_PER_100 = dict(
    zip(
        FIELDS,
        (
            "per_100g_calories",
            "per_100g_proteins",
            "per_100g_fats",
            "per_100g_carbs",
        ),
    )
)
# These name one product, however many foods the photo read showed.
_PRODUCT_SOURCES = frozenset(
    {"saved_rag", "barcode_off", "name_off", "label_ocr", "name_web"}
)
_NUMBERS_SOURCE = {"barcode_off": "barcode", "label_ocr": "label", "saved_rag": "dish"}
_VERIFIED = frozenset({"barcode", "label", "user"})
# How many of a dish's last meals are looked through for its groups.
_GROUPS_LOOKBACK = 5
_ITEM_FIELDS = (
    "personal_food_id",
    "position",
    "name",
    "amount_text",
    "grams",
    "base_grams",
    "base_calories",
    "base_proteins",
    "base_fats",
    "base_carbohydrates",
    "factor",
    "numbers_source",
    "by_user",
    "replaced_by_item_id",
)
_MEAL_FIELDS = (*FIELDS, "numbers_basis", "plate_totals", "description")
_MEAL_JSON = ("nutrients", "ai_analysis")
_TIME_FIELDS = ("time_precision", "time_source", "time_phrase")


@dataclass
class Portion:
    """What one serving of a dish holds: floats, or no numbers at all."""

    numbers: Optional[dict] = None
    grams: Optional[float] = None
    assumed: bool = False  # nobody gave these grams: a default, and it says so


@dataclass
class Change:
    status: str
    meal: Optional[Meal] = None
    event: Optional[RecordEvent] = None
    items: list = field(default_factory=list)  # what the change made
    numbers_changed: bool = True
    meals: list = field(default_factory=list)  # the meals an album split made
    gone: list = field(default_factory=list)  # message ids of lines taken back


# --- names and dishes -------------------------------------------------------


def _clean_name(name) -> str:
    return " ".join(str(name).split())[:NAME_MAX]


def _foods(nutrients) -> list:
    foods = nutrients.get("foods") if isinstance(nutrients, dict) else None
    if not isinstance(foods, list):
        return []
    return [name for name in map(_clean_name, foods) if name]


def _names(meal_in: MealCreate) -> list:
    """One name per dish in the meal; a resolved product is one dish."""
    signals = meal_in.resolution_signals or {}
    if meal_in.resolution_source in _PRODUCT_SOURCES:
        product = signals.get("saved_food_name") or signals.get("product_name")
        if product and _clean_name(product):
            return [_clean_name(product)]
    foods = _foods(meal_in.nutrients)
    if foods:
        return foods
    description = _clean_name(meal_in.description or "")
    return [description] if description else []


def holds_one_dish(nutrition: dict, signals: Optional[dict]) -> bool:
    """Whether a meal's numbers belong to one name. A plate's do not (H6)."""
    signals = signals or {}
    if signals.get("saved_food_name") or signals.get("product_name"):
        return True
    return len(_foods(nutrition)) <= 1


def _dish_for(
    db: Session,
    user_id: int,
    name: str,
    *,
    barcode: Optional[str] = None,
    source: Optional[str] = None,
) -> PersonalFood:
    """The dish with this barcode or name; a new one, without numbers, if none is."""
    dish = None
    if barcode:
        dish = crud_personal_food.get_by_barcode(db, barcode=barcode, user_id=user_id)
    if dish is None:
        dish = crud_personal_food.find_by_name(db, user_id, name)
        if dish is not None and barcode and not dish.barcode:
            dish.barcode = barcode
    if dish is None:
        dish = crud_personal_food.add_dish(
            db, user_id=user_id, name=name, barcode=barcode, resolution_source=source
        )
        dish.times_used = 0
    return dish


def _is_plate_row(db: Session, dish: PersonalFood) -> bool:
    """A row whose numbers came from a whole plate under one food's name (H6).

    Nothing writes these any more, but the base still holds some. Such a row points
    at a meal of several foods; a row a person or a label vouches for is never one.
    """
    if dish.numbers_by_user or dish.numbers_quality in _VERIFIED or not dish.meal_id:
        return False
    source = db.get(Meal, dish.meal_id)
    if source is None or source.numbers_basis == "items":
        return False
    return len(_foods(source.nutrients)) > 1


def _used(db: Session, dish: PersonalFood, meal: Meal, *, one_dish: bool) -> None:
    """Counted once for this meal: the learning task sees the same ``meal_id``.

    ``meal_id`` also says where the dish's numbers came from, so a plate never takes
    it, and a plate row keeps it until new numbers replace the old. That row can then
    count twice for one meal, which is the cheaper of the two mistakes.
    """
    dish.times_used = (dish.times_used or 0) + 1
    dish.last_used_at = datetime.now(UTC)
    dish.hidden_at = None  # eating it again brings it back
    if one_dish and not _is_plate_row(db, dish):
        dish.meal_id = meal.id


def _per_100(dish: PersonalFood) -> Optional[dict]:
    if dish.numbers_basis != "per_100" or dish.per_100g_calories is None:
        return None
    return {
        # Dishes keep Numeric: Decimal * float raises TypeError.
        key: None if getattr(dish, column) is None else float(getattr(dish, column))
        for key, column in _PER_100.items()
    }


def numbers_checked(dish: PersonalFood, grams: Optional[float]) -> bool:
    """Whether a portion of this dish is what its checked numbers say. They are per
    100 g, so a portion nobody weighed is not checked (item 86). 100 g with no usual
    portion may be the default: «не проверено» of a real one is the safe mistake."""
    checked = dish.numbers_by_user or dish.numbers_quality in _VERIFIED
    if grams in (DEFAULT_GRAMS, dish.suggested_grams) and not dish.usual_grams:
        return False
    return bool(checked and grams)


def _at_default_weight(db: Session, dish: PersonalFood, portion: Portion) -> Portion:
    """No weight anywhere. Numbers somebody wrote count DEFAULT_GRAMS; an estimate keeps
    the plate it came from, which says more than an assumed weight, if there is one."""
    per_100 = _per_100(dish)
    if portion.grams or per_100 is None or _is_plate_row(db, dish):
        return portion
    written = dish.numbers_by_user or dish.numbers_quality in _VERIFIED
    if portion.numbers and not written:
        return portion
    grams = dish.suggested_grams or DEFAULT_GRAMS
    return Portion(_scaled(per_100, grams / 100.0), grams, assumed=True)


def _scaled(numbers: dict, factor: float) -> dict:
    return {
        key: None if value is None else round(value * factor, 2)
        for key, value in numbers.items()
    }


def numbers_exact(meal: Meal, items: list) -> bool:
    """Only a barcode, a label or typed numbers go without ≈ (2.6), every dish of them.
    A share of a plate is no dish of its own, and a dish with no numbers says nothing.
    """
    counted = [
        item
        for item in items
        if item.numbers_source != "plate_share" and _has_numbers(item)
    ]
    return (
        bool(counted)
        and meal.numbers_basis == "items"
        and all(item.numbers_source in EXACT_SOURCES for item in counted)
    )


def _has_numbers(item: MealItem) -> bool:
    return any(getattr(item, f"base_{key}") is not None for key in FIELDS)


def _shown(item: MealItem) -> dict:
    """Base × factor: ½ after ×2 is half of the base, not a quarter of it."""
    factor = item.factor if item.factor is not None else 1
    return {
        key: (
            None
            if getattr(item, f"base_{key}") is None
            else getattr(item, f"base_{key}") * factor
        )
        for key in FIELDS
    }


def _item_grams(item: MealItem) -> Optional[float]:
    if item.grams:
        return item.grams
    if item.base_grams:
        return item.base_grams * (item.factor if item.factor is not None else 1)
    return None


def weighed_grams(item: MealItem) -> Optional[float]:
    """The item's weight, if anybody gave one: a default is no weight of the dish."""
    if item.base_grams_assumed and not item.grams:
        return None
    return _item_grams(item)


def _no_weight_read(source: Optional[str], signals: dict, grams) -> bool:
    """A product's numbers with no weight say "100г", meaning «per 100 g». That is a
    label's basis and not a portion, unless somebody said it. A star logged at a default
    says so itself."""
    if signals.get("portion_assumed"):
        return True
    if signals.get("portion_grams"):
        return False
    return source in _PRODUCT_SOURCES and grams == DEFAULT_GRAMS


def _last_item(db: Session, dish_id: int) -> Optional[MealItem]:
    """The dish's last portion that has numbers of its own, in a meal still kept."""
    stmt = (
        select(MealItem)
        .join(Meal, Meal.id == MealItem.meal_id)
        .where(
            MealItem.personal_food_id == dish_id,
            MealItem.removed_at.is_(None),
            MealItem.numbers_source.notin_(("none", "plate_share")),
            Meal.deleted_at.is_(None),
            Meal.is_draft.is_(False),
        )
        .order_by(MealItem.id.desc())
    )
    return next((item for item in db.scalars(stmt) if _has_numbers(item)), None)


def _meal_totals(meal: Meal) -> dict:
    return {key: getattr(meal, key) for key in FIELDS}


def _source_grams(meal: Meal) -> Optional[float]:
    signals = (
        meal.resolution_signals if isinstance(meal.resolution_signals, dict) else {}
    )
    nutrients = meal.nutrients if isinstance(meal.nutrients, dict) else {}
    return signals.get("portion_grams") or _parse_portion_grams(nutrients)


def _source_weight(meal: Meal) -> Optional[float]:
    """The weight a meal's read says, if it is a weight."""
    grams = _source_grams(meal)
    signals = (
        meal.resolution_signals if isinstance(meal.resolution_signals, dict) else {}
    )
    return None if _no_weight_read(meal.resolution_source, signals, grams) else grams


def wants_suggestion(db: Session, dish: PersonalFood) -> bool:
    """Whether a guess at its portion would be used: numbers on 100 g, no guess yet, and
    no weight anywhere. A plate row, or an estimate with its plate, has no default."""
    if (
        _per_100(dish) is None
        or dish.suggested_grams
        or dish.usual_grams_by_user
        or dish.merged_into_id
        or dish.hidden_at
    ):
        return False
    return dish_portion(db, dish).assumed


def save_suggestion(
    db: Session, dish_id: int, user_id: int, name: str, grams: float, unit: str
) -> bool:
    """A guess onto the dish it was asked for. False, and nothing written, if the dish
    is not this person's, or was renamed, weighed or merged while the model was asked:
    what the person did outranks a late answer."""
    dish = db.scalars(
        select(PersonalFood)
        .where(PersonalFood.id == dish_id, PersonalFood.user_id == user_id)
        .with_for_update()
    ).first()
    if (
        dish is None
        or dish.canonical_name != name
        or dish.suggested_grams
        or dish.usual_grams_by_user
        or dish.merged_into_id
    ):
        db.rollback()
        return False
    dish.suggested_grams = grams
    dish.suggested_unit = unit
    db.commit()
    return True


def dish_portion(db: Session, dish: PersonalFood) -> Portion:
    """A serving of the dish: its own, else the portion its ⭐ holds. No model.

    A favourite's name is a known name, and a known name costs no read, so a dish with
    no numbers of its own would write an empty meal where the favourite knows the
    answer. A removed favourite lends nothing: what a person threw away serves no
    portion.
    """
    own = _own_portion(db, dish)
    has_own = own.numbers is not None and any(
        value is not None for value in own.numbers.values()
    )
    # Typed numbers with no weight of their own may still find one on the ⭐.
    if has_own and not (dish.numbers_by_user and own.grams is None):
        return _at_default_weight(db, dish, own)
    fav = crud_favorite_meal.active_of_dish(db, dish.id)
    if fav is None or all(getattr(fav, key) is None for key in FIELDS):
        return _at_default_weight(db, dish, own)
    return star_portion(db, dish, fav)


def _at_typed_grams(dish: PersonalFood, portion: Portion) -> Portion:
    """A typed usual portion, with numbers scaled by it. Numbers with no weight stay as
    they are: grams alone cannot say what they were for."""
    typed = dish.usual_grams if dish.usual_grams_by_user else None
    if not typed:
        return portion
    if portion.grams and portion.numbers:
        return Portion(_scaled(portion.numbers, typed / portion.grams), typed)
    return Portion(portion.numbers, typed)


def star_portion(db: Session, dish: PersonalFood, fav: FavoriteMeal) -> Portion:
    """What a ⭐ logs: the portion it holds, until a person corrects its dish.

    One correction lives in one place (US-O4), so the favourite's copy stays and typed
    numbers go over it, at the portion's grams. A ⭐ made at a default holds no weight,
    so a typed portion cannot scale its copy, and the dish's own numbers answer as on
    the dish's card. No weight anywhere: as any dish.
    """
    held = Portion({key: getattr(fav, key) for key in FIELDS}, fav.usual_grams)
    portion = _at_typed_grams(dish, held)
    per_100 = _per_100(dish)
    if dish.numbers_by_user and per_100:
        grams = portion.grams or _own_portion(db, dish).grams
        if grams:
            return Portion(_scaled(per_100, grams / 100.0), grams)
    if per_100 and portion.grams and not held.grams and not _is_plate_row(db, dish):
        return Portion(_scaled(per_100, portion.grams / 100.0), portion.grams)
    return _at_default_weight(db, dish, portion)


def star_meal_fields(db: Session, dish: PersonalFood | None, fav: FavoriteMeal) -> dict:
    """A ⭐'s fields of ``MealCreate``. Its own row stays as it is. No dish yet (a row
    an old container wrote): nobody corrected anything, so the copy serves."""
    portion = (
        star_portion(db, dish, fav)
        if dish is not None
        else Portion({key: getattr(fav, key) for key in FIELDS}, fav.usual_grams)
    )
    numbers = portion.numbers or {}
    nutrients = (
        dict(fav.nutrients) if isinstance(fav.nutrients, dict) else fav.nutrients
    )
    if isinstance(nutrients, dict) and any(
        numbers.get(key) != getattr(fav, key) for key in FIELDS
    ):
        nutrients.update({_NUTRIENT_KEYS[key]: numbers.get(key) for key in FIELDS})
    tags = food_groups.carried(fav.nutrients)
    if dish is not None and dish.food_groups is not None:
        tags = _word_tags(dish.canonical_name, dish.food_groups)
        nutrients = {**(nutrients or {}), **tags}
    return {
        **{key: numbers.get(key) for key in FIELDS},
        "nutrients": nutrients,
        "ai_analysis": tags,
        "grams": portion.grams,
        "assumed": portion.assumed,
    }


def _word_tags(name: str, groups: list) -> dict:
    """A meal's tags from a dish's word. Not the meal's own choice: an edit of the meal
    still counts what its items carry."""
    chosen = food_groups.ordered(groups)
    return {
        "food_items": [{"name": name, "groups": chosen}],
        "food_groups": chosen,
        "food_groups_by_user": False,
    }


def _own_portion(db: Session, dish: PersonalFood) -> Portion:
    """Its usual portion, else its last one.

    A plate row serves no numbers (H6). A dish corrected in the base reaches future
    meals, so known grams count at the dish's numbers of today, and a recorded
    portion's own numbers are the answer only when the dish has none.
    """
    if _is_plate_row(db, dish):
        return Portion()
    last = _last_item(db, dish.id)
    source = db.get(Meal, dish.meal_id) if dish.meal_id else None
    if source is not None and source.deleted_at is not None:
        source = None  # what a person threw away serves no portion
    source_has_numbers = source is not None and any(
        value is not None for value in _meal_totals(source).values()
    )
    grams = dish.usual_grams or (last and weighed_grams(last))
    if not grams and source_has_numbers:
        grams = _source_weight(source)
    per_100 = _per_100(dish)
    if per_100 and grams:
        return Portion(_scaled(per_100, grams / 100.0), grams)
    if per_100 and last is not None and last.base_grams_assumed:
        return Portion()  # numbers and no weight: the default's to say
    if last is not None:
        # No numbers on 100 g left to count from: its numbers serve, and a default
        # they were written at stays a default.
        assumed = bool(last.base_grams_assumed and not last.grams)
        return _at_typed_grams(dish, Portion(_shown(last), _item_grams(last), assumed))
    if source_has_numbers:
        weighed = _source_grams(source) if dish.usual_grams_by_user else grams
        return _at_typed_grams(dish, Portion(_meal_totals(source), weighed))
    return Portion()


def _with_numbers(portion: Portion) -> dict:
    """An item's columns for a portion copied from a dish."""
    if portion.numbers is None:
        return {"numbers_source": "none"}
    return {
        **{f"base_{key}": value for key, value in portion.numbers.items()},
        "base_grams": portion.grams,
        "base_grams_assumed": portion.assumed,
        "numbers_source": "dish",
    }


# --- at save ----------------------------------------------------------------


def _rolled_back(db: Session, write: Callable):
    try:
        return write()
    except Exception:
        db.rollback()
        raise


def save_meal(
    db: Session,
    meal_in: MealCreate,
    user_id: int,
    *,
    names: Optional[list] = None,
    numbers_source: Optional[str] = None,
    grams_by_user: bool = False,
) -> Meal:
    """The meal and its items, in one transaction.

    Several foods are a plate: one item per name and no numbers on any of them. One
    food's plate is its dish, so it gets the numbers and the basis ``items``.
    """

    def write() -> Meal:
        meal = crud_meal.build(meal_in, user_id)
        _write_reading(
            db,
            meal,
            meal_in,
            user_id,
            names=names,
            numbers_source=numbers_source,
            grams_by_user=grams_by_user,
        )
        crud_action_event.add_for_user(db, user_id=user_id, kind=SAVED, label="dish")
        db.commit()
        db.refresh(meal)
        return meal

    return _rolled_back(db, write)


def _write_reading(
    db: Session,
    meal: Meal,
    meal_in: MealCreate,
    user_id: int,
    *,
    names: Optional[list] = None,
    numbers_source: Optional[str] = None,
    grams_by_user: bool = False,
    source_id: Optional[int] = None,
    shared: bool = False,
    first: int = 0,
) -> None:
    """The meal's basis and its items, from what was read. Commits nothing.

    ``shared``: the numbers read hold more than ``names``, so even a single name is a
    line of the plate and not the plate itself. ``first``: the position they start at.
    """
    wanted = names if names is not None else _names(meal_in)
    totals = _meal_totals(meal)
    read = any(value is not None for value in totals.values())
    one_dish = len(wanted) == 1 and not shared
    if not read:
        meal.numbers_basis = "none"
    elif one_dish:
        meal.numbers_basis = "items"
    else:
        meal.numbers_basis = "plate"
        meal.plate_totals = totals
    db.add(meal)
    db.flush()

    signals = meal_in.resolution_signals or {}
    barcode = (signals.get("barcode_raw") or None) if one_dish else None
    for position, name in enumerate(wanted, first):
        dish = _dish_for(
            db, user_id, name, barcode=barcode, source=meal_in.resolution_source
        )
        _used(db, dish, meal, one_dish=one_dish)
        columns = {"numbers_source": "none"}
        if read and one_dish:
            grams = signals.get("portion_grams") or _parse_portion_grams(
                meal_in.nutrients or {}
            )
            columns = {
                **{f"base_{key}": value for key, value in totals.items()},
                "base_grams": grams,
                "base_grams_assumed": not grams_by_user
                and _no_weight_read(meal_in.resolution_source, signals, grams),
                "grams": grams if grams_by_user else None,
                "by_user": grams_by_user,
                "numbers_source": numbers_source
                or _NUMBERS_SOURCE.get(meal_in.resolution_source, "model"),
            }
        elif read:
            columns = {"numbers_source": "plate_share"}
        crud_meal_item.add(
            db,
            meal_id=meal.id,
            name=name,
            personal_food_id=dish.id,
            position=position,
            source_id=source_id,
            **columns,
        )


# What a reading fills on a meal that is already in the diary.
_READ_FIELDS = (
    "description",
    "calories",
    "proteins",
    "fats",
    "carbohydrates",
    "nutrients",
    "ai_analysis",
    "resolution_source",
    "resolution_signals",
)


def names_read(meal_in: MealCreate) -> list:
    """The dishes a reading found: the person's own words do not count as one."""
    return _names(meal_in.model_copy(update={"description": None}))


def apply_read(
    db: Session,
    meal: Meal,
    rev: int,
    meal_in: MealCreate,
    *,
    source_id: Optional[int] = None,
    replacing: bool = False,
) -> bool:
    """A reading onto the meal born before it. False, and nothing written, if the
    meal has changed since: what the person did outranks a late answer.

    No event: the person changed nothing, and there is no earlier state to return to.
    ``replacing``: a re-read, over the items of the read before. That one has a state
    to return to, so it writes a ``reread`` event.
    """
    if (meal.rev or 0) != rev or meal.deleted_at is not None:
        return False

    def write() -> bool:
        old = crud_meal_item.live(db, meal.id) if replacing else []
        before = _meal_state(meal, old)
        for item in old:
            item.removed_at = datetime.now(UTC)
        if replacing:
            meal.plate_totals = None
        for key in _READ_FIELDS:
            setattr(meal, key, getattr(meal_in, key))
        _write_reading(db, meal, meal_in, meal.user_id, source_id=source_id)
        meal.reading_status = "read"
        meal.rev = rev + 1
        if replacing:
            db.flush()
            crud_record_event.add(
                db,
                user_id=meal.user_id,
                kind="reread",
                meal_id=meal.id,
                before=before,
                after=_meal_state(meal, [*old, *crud_meal_item.live(db, meal.id)]),
            )
        db.commit()
        db.refresh(meal)
        return True

    return _rolled_back(db, write)


def _taken_out(db: Session, meal: Meal, live: list) -> set:
    """The names the person took out or replaced: a reading does not bring them back."""
    names = set()
    for event in db.scalars(
        select(RecordEvent).where(
            RecordEvent.meal_id == meal.id,
            RecordEvent.kind.in_(("remove", "replace")),
        )
    ):
        for key, state in ((event.after or {}).get("items") or {}).items():
            item = db.get(MealItem, int(key))
            if state.get("removed") and item is not None and item.removed_at:
                names.add(item.name.casefold())
    return names - {item.name.casefold() for item in live}


def _plate_after_read(
    meal: Meal, kept_named: list, parts_plate: dict
) -> Optional[dict]:
    """What the reading holds beyond the lines the person kept, plus what the meals
    joined here brought. The kept line counts once, at the person's own number."""
    read = (meal.plate_totals or {}) if meal.numbers_basis == "plate" else {}
    plate = {}
    for key in FIELDS:
        value = read.get(key)
        if value is not None:
            taken = sum(getattr(i, f"base_{key}") or 0 for i in kept_named)
            value = max(round(value - taken, 2), 0)
        extra = parts_plate.get(key)
        plate[key] = (
            None if value is None and extra is None else (value or 0) + (extra or 0)
        )
    return plate if any(value is not None for value in plate.values()) else None


def reread(
    db: Session,
    meal: Meal,
    rev: int,
    meal_in: MealCreate,
    *,
    source_id: Optional[int] = None,
    quiet: bool = False,
) -> Optional[Change]:
    """🔁 Прочитать заново: only what this meal's own read gave goes. What the person
    typed stays, a dish they took out stays out, and a meal joined here is not this
    reading's business. None, with nothing written, if the meal moved since the tap.

    ``quiet``: the first read of a meal something was joined to while it was out. The
    person changed nothing and there is no read before it to return to: no event, like
    ``apply_read``.
    """
    if (meal.rev or 0) != rev or meal.deleted_at is not None:
        return None

    def write() -> Change:
        live = crud_meal_item.live(db, meal.id)
        before = _meal_state(meal, live)
        tags = _tagged(meal)
        known = _groups_by_item(meal, live)
        chosen = (
            food_groups.ordered(tags.get("food_groups") or [])
            if tags.get("food_groups_by_user")
            else None
        )
        kept = [i for i in live if i.by_user or i.origin_meal_id != meal.id]
        going = [i for i in live if i not in kept]
        now = datetime.now(UTC)
        for item in going:
            item.removed_at = now
        joined = parts(db, meal)
        parts_plate = _summed(
            [
                {key: (p.meal.plate_totals or {}).get(key) for key in FIELDS}
                for p in joined
                if p.meal.numbers_basis == "plate"
            ]
        )
        has_kept_names = {i.name.casefold(): i for i in kept}
        out = _taken_out(db, meal, live)
        names = _names(meal_in.model_copy(update={"description": None})) or _names(
            meal_in
        )
        wanted = [
            name
            for name in names
            if name.casefold() not in has_kept_names and name.casefold() not in out
        ]
        matched = [
            has_kept_names[name.casefold()]
            for name in names
            if name.casefold() in has_kept_names
        ]
        whole = not kept and not joined and len(wanted) == len(names)
        meal.plate_totals = None
        for key in _READ_FIELDS:
            setattr(meal, key, getattr(meal_in, key))
        first = max((i.position for i in kept), default=-1) + 1
        _write_reading(
            db,
            meal,
            meal_in,
            meal.user_id,
            names=wanted,
            source_id=source_id,
            shared=not whole,
            first=first,
        )
        db.flush()
        now_live = crud_meal_item.live(db, meal.id)
        made = [item for item in now_live if item not in live]
        if not whole:
            plate = _plate_after_read(meal, matched, parts_plate) if wanted else None
            if not wanted and any(v is not None for v in parts_plate.values()):
                plate = parts_plate
            meal.plate_totals = plate
            if plate:
                meal.numbers_basis = "plate"
            else:
                meal.numbers_basis = (
                    "items" if any(_has_numbers(i) for i in now_live) else "none"
                )
            rows = _tag_rows(meal)
            found = {
                **known,
                **{i.id: rows.get(i.name.casefold(), []) for i in made},
            }
            _recompute(meal, now_live)
            _rewrite_fields(db, meal, now_live, found)
        if chosen is not None:
            meal.ai_analysis = food_groups.stamp(
                meal.ai_analysis or {}, chosen, by_user=True
            )
            meal.nutrients = food_groups.stamp(
                meal.nutrients or {}, chosen, by_user=True
            )
        meal.reading_status = "read"
        meal.rev = rev + 1
        meal.updated_at = now
        db.flush()
        if quiet:
            db.commit()
            db.refresh(meal)
            return Change(DONE, meal, None, made)
        event = crud_record_event.add(
            db,
            user_id=meal.user_id,
            kind="reread",
            meal_id=meal.id,
            source_id=source_id,
            before=before,
            after=_meal_state(meal, [*live, *made]),
        )
        db.commit()
        db.refresh(meal)
        return Change(DONE, meal, event, made)

    return _rolled_back(db, write)


def only_dish_name(db: Session, meal: Meal) -> Optional[str]:
    """The name of the dish a one-item meal stands on: what the base calls it."""
    items = crud_meal_item.live(db, meal.id)
    if len(items) != 1 or items[0].personal_food_id is None:
        return None
    dish = db.get(PersonalFood, items[0].personal_food_id)
    return dish.canonical_name if dish else None


# --- the free exact-name path (US-N1) ---------------------------------------


def exact_match(db: Session, user_id: int, text: Optional[str]) -> list:
    """The dishes the text names exactly: all of it, or every comma-separated part."""
    wanted = (text or "").strip()
    if not wanted:
        return []
    known = crud_personal_food.names_of(db, user_id)
    whole = known.get(wanted.lower())
    parts = [part.strip().lower() for part in wanted.split(",")]
    if whole is not None:
        dishes = [whole]
    elif len(parts) >= 2 and all(part in known for part in parts):
        dishes = [known[part] for part in parts]
    else:
        return []
    return [] if any(_reads_as_new(db, dish) for dish in dishes) else dishes


def _reads_as_new(db: Session, dish: PersonalFood) -> bool:
    """A removed ⭐ was all that knew the numbers. Removing it did not ask to stop
    counting, so the words go to the model like any food, and the dish gets its own."""
    return dish_portion(db, dish).numbers is None and crud_favorite_meal.has_removed(
        db, dish.id
    )


def _groups_of_dish(db: Session, dish_id: int, name: str) -> Optional[list]:
    """The person's word on the dish, else what the last meal tagging it carried."""
    dish = db.get(PersonalFood, dish_id)
    if dish is not None and dish.food_groups is not None:
        return food_groups.ordered(dish.food_groups)
    stmt = (
        select(MealItem)
        .join(Meal, Meal.id == MealItem.meal_id)
        .where(
            MealItem.personal_food_id == dish_id,
            Meal.deleted_at.is_(None),
            Meal.is_draft.is_(False),
        )
        .order_by(MealItem.id.desc())
        .limit(_GROUPS_LOOKBACK)
    )
    for item in db.scalars(stmt):
        meal = db.get(Meal, item.meal_id)
        rows = _tag_rows(meal) if meal else {}
        for key in (item.name, name):
            if key.casefold() in rows:
                return rows[key.casefold()]
    return None


def save_exact(
    db: Session,
    user_id: int,
    dishes: list,
    *,
    meal_time: datetime,
    meal_type: Optional[str] = None,
    draft: bool = False,
) -> Meal:
    """The meal the named dishes make, with no model call and final at once."""

    def write() -> Meal:
        one_dish = len(dishes) == 1
        portions = [dish_portion(db, dish) for dish in dishes]
        names = [_clean_name(dish.canonical_name) for dish in dishes]
        known = [portion.numbers for portion in portions if portion.numbers]
        totals = _summed(known)
        unverified = any(
            portion.numbers and not numbers_checked(dish, portion.grams)
            for dish, portion in zip(dishes, portions)
        )
        groups = [
            _groups_of_dish(db, dish.id, name) for dish, name in zip(dishes, names)
        ]
        tags = {}
        if any(found is not None for found in groups):
            rows = [
                {"name": name, "groups": found or []}
                for name, found in zip(names, groups)
            ]
            tags = {
                "food_items": rows,
                "food_groups": food_groups.from_items(rows),
                "food_groups_by_user": False,
            }
        meal = crud_meal.build(
            MealCreate(
                description=", ".join(names),
                meal_time=meal_time,
                meal_type=meal_type,
                **totals,
                nutrients={
                    "foods": names,
                    **{_NUTRIENT_KEYS[key]: totals[key] for key in FIELDS},
                    **tags,
                },
                ai_analysis=tags or None,
                resolution_source=EXACT_NAME_SOURCE,
                resolution_signals={
                    "dish_ids": [dish.id for dish in dishes],
                    "numbers_unverified": unverified,
                },
                numbers_basis="items" if known else "none",
            ),
            user_id,
        )
        meal.is_draft = draft
        db.add(meal)
        db.flush()
        for position, (dish, name, portion) in enumerate(zip(dishes, names, portions)):
            _used(db, dish, meal, one_dish=one_dish)
            crud_meal_item.add(
                db,
                meal_id=meal.id,
                name=name,
                personal_food_id=dish.id,
                position=position,
                **_with_numbers(portion),
            )
        crud_action_event.add_for_user(db, user_id=user_id, kind=SAVED, label="dish")
        db.commit()
        db.refresh(meal)
        return meal

    return _rolled_back(db, write)


def numbers_tag(meal: Meal) -> Optional[str]:
    """The source tag of a meal written from the person's dishes."""
    if meal.resolution_source != EXACT_NAME_SOURCE or meal.calories is None:
        return None
    signals = (
        meal.resolution_signals if isinstance(meal.resolution_signals, dict) else {}
    )
    return TAG_DISH_UNVERIFIED if signals.get("numbers_unverified") else TAG_DISH


# --- the sum and the JSON fields --------------------------------------------


def _summed(numbers: list) -> dict:
    """Known values add up; a field nobody knows stays None, never 0."""
    totals = {}
    for key in FIELDS:
        known = [row[key] for row in numbers if row[key] is not None]
        totals[key] = round(sum(known), 2) if known else None
    return totals


def _recompute(meal: Meal, items: list) -> None:
    numbers = [_shown(item) for item in items if _has_numbers(item)]
    if meal.numbers_basis == "none":
        if not numbers:
            return
        meal.numbers_basis = "items"
    added = _summed(numbers)
    plate = meal.plate_totals if meal.numbers_basis == "plate" else None
    for key in FIELDS:
        if meal.numbers_basis == "items":
            setattr(meal, key, added[key])
            continue
        kept = (plate or {}).get(key)
        if added[key] is None:
            setattr(meal, key, kept)
        else:
            setattr(meal, key, round((kept or 0) + added[key], 2))


def _tagged(meal: Meal) -> dict:
    for source in (meal.ai_analysis, meal.nutrients):
        if isinstance(source, dict) and "food_groups" in source:
            return source
    return {}


def _tag_rows(meal: Meal) -> dict:
    rows = _tagged(meal).get("food_items")
    return {
        row["name"].casefold(): list(row.get("groups") or [])
        for row in (rows if isinstance(rows, list) else [])
        if isinstance(row, dict) and isinstance(row.get("name"), str)
    }


def _groups_by_item(meal: Meal, items: list) -> dict:
    """Each item's groups in the tags as they stand.

    An item named otherwise than the tags (a favourite, a legacy meal's own name)
    takes what the tags hold under names no item has, so a group is not lost.
    """
    rows = _tag_rows(meal)
    names = {item.name.casefold() for item in items}
    spare = [
        group for name, groups in rows.items() if name not in names for group in groups
    ]
    if not rows:
        confirmed = _tagged(meal).get("food_groups")
        spare = list(confirmed) if isinstance(confirmed, list) else []
    return {
        item.id: rows.get(item.name.casefold(), food_groups.ordered(spare))
        for item in items
    }


def _rewrite_fields(db: Session, meal: Meal, items: list, known: dict) -> None:
    """``known`` holds the groups of the items that were here before the change."""
    prior = _tagged(meal)
    groups = {}
    for item in items:
        found = known.get(item.id)
        if found is None and item.personal_food_id:
            found = _groups_of_dish(db, item.personal_food_id, item.name)
        groups[item.id] = found or []
    names = [item.name for item in items]
    # A portion's words describe the plate that was: the totals below replace it.
    nutrients = {
        key: value
        for key, value in (
            meal.nutrients if isinstance(meal.nutrients, dict) else {}
        ).items()
        if key not in ("portion", "portion_grams")
    }
    nutrients["foods"] = names
    for key, nutrient_key in _NUTRIENT_KEYS.items():
        nutrients[nutrient_key] = getattr(meal, key)
    if prior or any(groups.values()):
        rows = [{"name": item.name, "groups": groups[item.id]} for item in items]
        by_user = bool(prior.get("food_groups_by_user"))
        tags = {
            "food_items": rows,
            "food_groups": (
                food_groups.ordered(prior.get("food_groups") or [])
                if by_user
                else food_groups.from_items(rows)
            ),
            "food_groups_by_user": by_user,
        }
        nutrients.update(tags)
        analysis = meal.ai_analysis if isinstance(meal.ai_analysis, dict) else {}
        meal.ai_analysis = {**analysis, **tags}
    meal.nutrients = nutrients
    meal.description = ", ".join(names)


# --- what a change writes, and its event ------------------------------------


def _item_state(item: MealItem) -> dict:
    return {
        **{key: getattr(item, key) for key in _ITEM_FIELDS},
        "base_grams_assumed": item.base_grams_assumed,
        "removed": item.removed_at is not None,
    }


def _meal_state(meal: Meal, items: list) -> dict:
    return {
        "meal": {
            **{key: getattr(meal, key) for key in (*_MEAL_FIELDS, *_MEAL_JSON)},
            "deleted": meal.deleted_at is not None,
        },
        "items": {str(item.id): _item_state(item) for item in items},
    }


def _changed(
    db: Session,
    meal: Meal,
    kind: str,
    mutate: Callable[[list], list],
    *,
    food_id: Optional[int] = None,
    numbers_changed: bool = True,
    groups: Optional[dict] = None,
    note: Optional[Callable[[], dict]] = None,
) -> Change:
    """Apply ``mutate`` and everything that follows from it, in one transaction.

    ``mutate`` gets the live items and returns the items it made. ``groups``: of the
    items that came from elsewhere with their own tags. ``note``: what else the event
    has to remember, called once the change is flushed.
    """

    def write() -> Change:
        live = crud_meal_item.live(db, meal.id)
        before = _meal_state(meal, live)
        known = _groups_by_item(meal, live)
        made = mutate(live)
        db.flush()
        now_live = crud_meal_item.live(db, meal.id)
        for item in made:
            # A legacy meal's own line: the old tags stay under the old name.
            if not live and item.numbers_source == "plate_share":
                known.update(_groups_by_item(meal, [item]))
        known.update(groups or {})
        _recompute(meal, now_live)
        _rewrite_fields(db, meal, now_live, known)
        meal.rev = (meal.rev or 0) + 1
        meal.updated_at = datetime.now(UTC)
        db.flush()
        event = crud_record_event.add(
            db,
            user_id=meal.user_id,
            kind=kind,
            meal_id=meal.id,
            food_id=food_id,
            before=before,
            after={**_meal_state(meal, [*live, *made]), **(note() if note else {})},
        )
        db.commit()
        return Change(DONE, meal, event, made, numbers_changed)

    return _rolled_back(db, write)


def _own_meal(db: Session, user_id: int, meal_id: int) -> Optional[Meal]:
    meal = crud_meal.get(db, meal_id)
    return meal if meal is not None and meal.user_id == user_id else None


def _own_item(db: Session, user_id: int, item_id: int):
    """The live item and its live meal, if this user's; else ``(None, None)``."""
    item = crud_meal_item.get(db, item_id)
    if item is None or item.removed_at is not None:
        return None, None
    meal = _own_meal(db, user_id, item.meal_id)
    return (item, meal) if meal is not None else (None, None)


_BASE_FIELDS = (*(f"base_{key}" for key in FIELDS), "base_grams")


def _recorded(db: Session, item: MealItem) -> Optional[dict]:
    """What a typed weight's per-100 g numbers took the place of, while they stand.

    Those numbers are the dish's, not the record's, and ½, ×1, ×2 count from the record.
    None when no weight did that, or when nothing was recorded to come back to.
    """
    events = db.scalars(
        select(RecordEvent)
        .where(RecordEvent.meal_id == item.meal_id, RecordEvent.kind == "grams")
        .order_by(RecordEvent.id.desc())
    )
    key = str(item.id)
    for event in events:
        before = ((event.before or {}).get("items") or {}).get(key)
        after = ((event.after or {}).get("items") or {}).get(key)
        if not before or not after:
            continue
        if all(before[name] == after[name] for name in _BASE_FIELDS):
            continue
        holds = all(after[name] == getattr(item, name) for name in _BASE_FIELDS)
        known = any(before[f"base_{name}"] is not None for name in FIELDS)
        return before if holds and known else None
    return None


def set_quantity(db: Session, user_id: int, item_id: int, factor: float) -> Change:
    """½, ×1, ×2 of what was recorded. ×1 also drops a typed weight: it is the base."""
    item, meal = _own_item(db, user_id, item_id)
    if item is None:
        return Change(STALE)
    if not _has_numbers(item):
        return Change(NOT_APPLICABLE, meal)  # nothing to scale, as on a plate's line
    if factor <= 0:
        raise ValueError(f"factor must be positive: {factor}")
    if item.factor == factor and item.grams is None:
        return Change(SAME, meal)
    recorded = _recorded(db, item)

    def mutate(_live: list) -> list:
        if recorded is not None:
            for name in (*_BASE_FIELDS, "numbers_source"):
                setattr(item, name, recorded[name])
        item.factor = factor
        item.grams = None
        item.by_user = True
        return []

    return _changed(db, meal, "qty", mutate, food_id=item.personal_food_id)


def set_grams(db: Session, user_id: int, item_id: int, grams: float) -> Change:
    """A typed weight: scales from the base, else takes the dish's per-100 g numbers.

    With neither the grams are stored and the numbers stay. A plate's line has its
    numbers inside the plate's, so it stays too.
    """
    item, meal = _own_item(db, user_id, item_id)
    if item is None:
        return Change(STALE)
    if grams <= 0:
        raise ValueError(f"grams must be positive: {grams}")
    if item.grams == grams:
        return Change(SAME, meal)
    dish = (
        db.get(PersonalFood, item.personal_food_id) if item.personal_food_id else None
    )
    per_100 = None
    if dish is not None and not _is_plate_row(db, dish):
        per_100 = _per_100(dish)
    in_plate = item.numbers_source == "plate_share"
    scales = bool(item.base_grams) and _has_numbers(item) and not in_plate
    from_dish = not scales and per_100 is not None and not in_plate

    def mutate(_live: list) -> list:
        item.grams = grams
        item.by_user = True
        if scales:
            item.factor = grams / item.base_grams
        elif from_dish:
            for key, value in per_100.items():
                setattr(item, f"base_{key}", value)
            item.base_grams = 100.0
            item.factor = grams / 100.0
            item.numbers_source = "dish"
        return []

    return _changed(
        db,
        meal,
        "grams",
        mutate,
        food_id=item.personal_food_id,
        numbers_changed=scales or from_dish,
    )


def _own_dish(
    db: Session, user_id: int, food_id: Optional[int], name: Optional[str]
) -> Optional[PersonalFood]:
    if food_id is not None:
        dish = db.get(PersonalFood, food_id)
        return dish if dish is not None and dish.user_id == user_id else None
    cleaned = _clean_name(name or "")
    return _dish_for(db, user_id, cleaned) if cleaned else None


def _open_legacy(db: Session, meal: Meal) -> list:
    """A meal from before items, at its first ➕: its totals move to the plate as they
    are, and one line stands for what it was.

    The line joins a dish of exactly that name and makes none: its name is a whole
    meal's description, which does not belong in the base.
    """
    totals = _meal_totals(meal)
    if any(value is not None for value in totals.values()):
        meal.plate_totals = totals
        meal.numbers_basis = "plate"
    description = _clean_name(meal.description or "")
    if not description:
        return []
    dish = crud_personal_food.find_by_name(db, meal.user_id, description)
    return [
        crud_meal_item.add(
            db,
            meal_id=meal.id,
            name=description,
            personal_food_id=dish.id if dish else None,
            numbers_source="plate_share",
        )
    ]


def add_item(
    db: Session,
    user_id: int,
    meal_id: int,
    *,
    food_id: Optional[int] = None,
    name: Optional[str] = None,
) -> Change:
    """A dish added to a meal, at its usual or last portion. No model, free.

    An unknown name becomes a new dish without numbers.
    """
    meal = _own_meal(db, user_id, meal_id)
    if meal is None:
        return Change(STALE)
    dish = _own_dish(db, user_id, food_id, name)
    if dish is None:
        db.rollback()
        return Change(STALE, meal)
    portion = dish_portion(db, dish)
    label = _clean_name(dish.canonical_name)
    made = {}

    def mutate(live: list) -> list:
        lines = [] if live else _open_legacy(db, meal)
        present = [*live, *lines]
        _used(db, dish, meal, one_dish=False)
        item = crud_meal_item.add(
            db,
            meal_id=meal.id,
            name=label,
            personal_food_id=dish.id,
            position=max((i.position for i in present), default=-1) + 1,
            by_user=True,
            **_with_numbers(portion),
        )
        made["item"] = item
        return [*lines, item]

    change = _changed(db, meal, "add", mutate, food_id=dish.id)
    change.items = [made["item"]]
    return change


def _plate_of(into: Meal, other: Meal) -> dict:
    """Two plates' totals: known values add up, a field nobody knows stays None."""
    if into.numbers_basis == "plate":
        mine = into.plate_totals or {}
    else:
        # Items count on top of a plate, so a meal on items brings none. Totals with
        # no basis at all are this meal's whole plate.
        mine = _meal_totals(into) if into.numbers_basis == "none" else {}
    theirs = other.plate_totals or {}
    return _summed([{key: side.get(key) for key in FIELDS} for side in (mine, theirs)])


def join(
    db: Session,
    user_id: int,
    into_id: int,
    from_id: int,
    *,
    source_id: Optional[int] = None,
) -> Change:
    """A meal into another. Its items, sources and messages move, and its row stays
    with its own time, merged and out of every reader, so it can be separated again.
    """
    into = _own_meal(db, user_id, into_id)
    other = _own_meal(db, user_id, from_id)
    if into is None or other is None or into.id == other.id:
        return Change(STALE)
    moving = crud_meal_item.live(db, other.id)
    tags = _groups_by_item(other, moving)
    remembered = {}

    def mutate(live: list) -> list:
        lines = [] if live else _open_legacy(db, into)
        first = max((i.position for i in [*live, *lines]), default=-1) + 1
        for offset, item in enumerate(moving):
            item.meal_id = into.id
            item.position = first + offset
            if item.source_id is None:
                item.source_id = source_id
        if other.numbers_basis == "plate":
            into.plate_totals = _plate_of(into, other)
            into.numbers_basis = "plate"
        other.merged_into_id = into.id
        other.deleted_at = datetime.now(UTC)
        other.rev = (other.rev or 0) + 1
        other.updated_at = datetime.now(UTC)
        # A meal joined to this one before now answers to the survivor: flat, so every
        # component of a meal is one step from it.
        for earlier in db.scalars(select(Meal).where(Meal.merged_into_id == other.id)):
            earlier.merged_into_id = into.id
        sources = []
        for source in db.scalars(
            select(MealSource).where(MealSource.meal_id == other.id)
        ):
            source.meal_id = into.id
            sources.append(source.id)
        messages, was_line = [], []
        for row in db.scalars(
            select(MealMessage).where(MealMessage.meal_id == other.id)
        ):
            row.meal_id = into.id
            messages.append(row.id)
            if row.view in LINE_VIEWS:
                was_line.append(row.id)
                row.view = "other"  # a line of what it was, not of this meal
        remembered.update(
            {
                "from": other.id,
                "sources": sources,
                "messages": messages,
                "lines": was_line,
                "items": {
                    str(i.id): _untouched(i)
                    for i in moving
                    if i.origin_meal_id == other.id
                },
            }
        )
        return [*lines, *moving]

    change = _changed(
        db,
        into,
        "join",
        mutate,
        groups=tags,
        note=lambda: {"join": remembered},
    )
    if change.status == DONE:
        change.items = moving
    return change


def _untouched(item: MealItem) -> list:
    """What says a dish is as it was when it was joined."""
    return [item.name, item.factor, item.grams]


@dataclass
class Part:
    """A meal joined into another: what comes back with ➖ Отделить."""

    meal: Meal
    sources: list  # as they came, first to last; the edits of them stay out
    messages: list  # ids of its ``meal_messages`` rows
    lines: list  # those of them that were the meal's own line
    items: dict  # what its dishes were when it was joined


def parts(db: Session, meal: Meal) -> list:
    """The meals joined into this one that have a way back, first joined first.

    A meal that was itself a survivor moved its parts' sources along with its own, so
    a part claims only what no earlier join claimed. Merged rows with no join behind
    them (an album taken back together) are not parts.
    """
    merged = {
        row.id: row
        for row in db.scalars(select(Meal).where(Meal.merged_into_id == meal.id))
    }
    if not merged:
        return []
    latest = {}
    for event in db.scalars(
        select(RecordEvent)
        .where(RecordEvent.user_id == meal.user_id, RecordEvent.kind == "join")
        .order_by(RecordEvent.id)
    ):
        note = (event.after or {}).get("join")
        if note and note["from"] in merged:
            latest[note["from"]] = (event.id, note)
    found, sources_seen, messages_seen = [], set(), set()
    for _event_id, note in sorted(latest.values(), key=lambda pair: pair[0]):
        ids = [i for i in note["sources"] if i not in sources_seen]
        sources_seen.update(ids)
        rows = [db.get(MealSource, i) for i in ids]
        sources = [
            row
            for row in rows
            if row is not None and row.meal_id == meal.id and not row.supersedes_id
        ]
        messages = [i for i in note["messages"] if i not in messages_seen]
        messages_seen.update(messages)
        if sources:
            found.append(
                Part(
                    merged[note["from"]],
                    sources,
                    messages,
                    list(note["lines"]),
                    note.get("items") or {},
                )
            )
    return found


def part_of(db: Session, meal: Meal, source_id: int) -> Optional[Part]:
    for part in parts(db, meal):
        if any(source.id == source_id for source in part.sources):
            return part
    return None


def _less(plate: Optional[dict], other: Optional[dict]) -> Optional[dict]:
    """A plate with what another brought taken off it; None when nothing is left."""
    left = {}
    for key in FIELDS:
        mine, theirs = (plate or {}).get(key), (other or {}).get(key)
        left[key] = mine if mine is None else round(mine - (theirs or 0), 2)
    if all(value is None or abs(value) < 0.005 for value in left.values()):
        return None
    return left


def separate(db: Session, user_id: int, survivor: Meal, part: Part) -> Change:
    """➖ Отделить: the part is its own meal again, with its own time and sources. The
    fixes typed while it was joined stay with its dishes."""
    other = part.meal
    moving = [
        item
        for item in crud_meal_item.live(db, survivor.id)
        if item.origin_meal_id == other.id
    ]
    as_it_was = len(moving) == len(part.items) and all(
        part.items.get(str(item.id)) == _untouched(item) for item in moving
    )
    tags = _groups_by_item(survivor, moving)

    def mutate(_live: list) -> list:
        now = datetime.now(UTC)
        for position, item in enumerate(moving):
            item.meal_id = other.id
            item.position = position
        message_ids = set()
        for source in part.sources:
            message_ids.add((source.chat_id, source.message_id))
        for source in db.scalars(
            select(MealSource).where(MealSource.meal_id == survivor.id)
        ):
            if (source.chat_id, source.message_id) in message_ids:
                source.meal_id = other.id
        for row_id in part.messages:
            row = db.get(MealMessage, row_id)
            if row is None or row.meal_id != survivor.id:
                continue
            row.meal_id = other.id
            if row_id in part.lines:
                row.view = "line"
        if other.numbers_basis == "plate" and survivor.numbers_basis == "plate":
            left = _less(survivor.plate_totals, other.plate_totals)
            survivor.plate_totals = left
            if left is None:
                rest = [i for i in _live if i not in moving and _has_numbers(i)]
                survivor.numbers_basis = "items" if rest else "none"
                if not rest:
                    for key in FIELDS:
                        setattr(survivor, key, None)
        other.deleted_at = None
        other.merged_into_id = None
        other.rev = (other.rev or 0) + 1
        other.updated_at = now
        _recompute(other, moving)
        if moving and not as_it_was:
            _rewrite_fields(db, other, moving, tags)
        return []

    change = _changed(db, survivor, "split", mutate, note=lambda: {"joined": other.id})
    if change.status == DONE:
        change.items = moving
        change.meals = [other]
    return change


def _teach_word(db: Session, item: MealItem, key: str, on: bool) -> None:
    """A dish with a word hears this one group too, and the rest of its word stands.
    Without a word it has no need: it reads its last tagged meal, and this is that
    meal."""
    dish = db.get(PersonalFood, item.personal_food_id or 0)
    if dish is None or dish.food_groups is None:
        return
    held = set(dish.food_groups)
    dish.food_groups = food_groups.ordered(held | {key} if on else held - {key})


def set_groups(db: Session, user_id: int, meal_id: int, key: str) -> Change:
    """M14: one group on or off, the person's word from here on. A meal of one dish
    teaches the dish, as the draft's choice always did."""
    meal = _own_meal(db, user_id, meal_id)
    if meal is None:
        return Change(STALE)

    def write() -> Change:
        live = crud_meal_item.live(db, meal.id)
        before = _meal_state(meal, live)
        tags = _tagged(meal)
        chosen = set(tags.get("food_groups") or [])
        chosen ^= {key}
        groups = food_groups.ordered(chosen)
        analysis = dict(meal.ai_analysis) if isinstance(meal.ai_analysis, dict) else {}
        nutrients = dict(meal.nutrients) if isinstance(meal.nutrients, dict) else {}
        rows = analysis.get("food_items") or nutrients.get("food_items") or []
        if len(live) == 1:
            rows = [{"name": live[0].name, "groups": groups}]
            _teach_word(db, live[0], key, key in groups)
        if rows:
            analysis["food_items"] = nutrients["food_items"] = rows
        meal.ai_analysis = food_groups.stamp(analysis, groups, by_user=True)
        meal.nutrients = food_groups.stamp(nutrients, groups, by_user=True)
        meal.rev = (meal.rev or 0) + 1
        meal.updated_at = datetime.now(UTC)
        db.flush()
        event = crud_record_event.add(
            db,
            user_id=user_id,
            kind="groups",
            meal_id=meal.id,
            before=before,
            after=_meal_state(meal, live),
        )
        db.commit()
        return Change(DONE, meal, event)

    return _rolled_back(db, write)


def album_sources(db: Session, meal_id: int) -> list:
    """The photos of the album the meal was born from, in order of arrival. Empty for
    a meal born from anything else."""
    sources = list(
        db.scalars(
            select(MealSource)
            .where(MealSource.meal_id == meal_id, MealSource.supersedes_id.is_(None))
            .order_by(MealSource.id)
        )
    )
    group = sources[0].media_group_id if sources else None
    if not group:
        return []
    return [s for s in sources if s.media_group_id == group and s.kind == "photo"]


def split_album(db: Session, user_id: int, meal_id: int) -> Change:
    """M2b: each photo its own meal, unread, at the album's time. The first stays in
    this row; ``undo`` puts all of it back."""
    meal = _own_meal(db, user_id, meal_id)
    if meal is None:
        return Change(STALE)
    first, *others = album_sources(db, meal_id) or [None]
    if not others:
        return Change(SAME, meal)

    def write() -> Change:
        live = crud_meal_item.live(db, meal.id)
        lines = list(
            db.scalars(
                select(MealMessage).where(
                    MealMessage.meal_id == meal.id,
                    MealMessage.view.in_(LINE_VIEWS),
                )
            )
        )
        before = {
            **_meal_state(meal, live),
            "album": {
                "photos": meal.photos,
                "reading_status": meal.reading_status,
                "resolution_source": meal.resolution_source,
                "resolution_signals": meal.resolution_signals,
                "lines": [row.id for row in lines],
            },
        }
        now = datetime.now(UTC)
        for item in live:
            item.removed_at = now
        for row in lines:
            row.view = "other"  # the line of what it was, not of this meal
        for key in _READ_FIELDS:
            setattr(meal, key, None)
        meal.plate_totals = None
        meal.numbers_basis = "none"
        meal.photos = [first.file_id]
        meal.reading_status = "pending"
        meal.rev = (meal.rev or 0) + 1
        meal.updated_at = now
        made = []
        for source in others:
            new = crud_meal.build(
                MealCreate(
                    meal_time=meal.meal_time,
                    sent_at=source.sent_at,
                    photos=[source.file_id],
                    numbers_basis="none",
                    time_precision=meal.time_precision,
                    time_source=meal.time_source,
                    time_phrase=meal.time_phrase,
                ),
                user_id,
            )
            new.reading_status = "pending"
            new.is_draft = meal.is_draft
            db.add(new)
            db.flush()
            source.meal_id = new.id
            row = db.scalar(
                select(MealMessage).where(
                    MealMessage.chat_id == source.chat_id,
                    MealMessage.message_id == source.message_id,
                )
            )
            if row is not None:
                row.meal_id = new.id
            made.append(new)
        db.flush()
        event = crud_record_event.add(
            db,
            user_id=user_id,
            kind="split",
            meal_id=meal.id,
            before=before,
            after={
                **_meal_state(meal, live),
                "album": {"meals": [new.id for new in made]},
            },
        )
        db.commit()
        return Change(DONE, meal, event, meals=made)

    return _rolled_back(db, write)


def split_holds(db: Session, event: RecordEvent) -> bool:
    """Whether the meals still are what the album's split made of them. A read writes
    no event, so what is read since goes back with the rest; what the person did stays.
    """
    ids = [event.meal_id, *event.after["album"]["meals"]]
    meals = [crud_meal.get_any(db, meal_id) for meal_id in ids]
    if not all(meals) or any(meal.deleted_at is not None for meal in meals):
        return False
    later = db.scalar(
        select(RecordEvent.id)
        .where(RecordEvent.meal_id.in_(ids), RecordEvent.id > event.id)
        .limit(1)
    )
    return later is None


def _undo_album(db: Session, meal: Meal, event: RecordEvent) -> Change:
    if not split_holds(db, event):
        return Change(REFUSED, meal)
    album = event.before["album"]

    def write() -> Change:
        now = datetime.now(UTC)
        gone = []

        def take_back(row: MealMessage) -> None:
            gone.append(row.message_id)
            row.view = "other"

        for item in crud_meal_item.live(db, meal.id):
            if str(item.id) not in event.after["items"]:
                item.removed_at = now  # read since the split
        _put_back(db, meal, event)
        for key in ("photos", "reading_status"):
            setattr(meal, key, album[key])
        meal.resolution_source = album["resolution_source"]
        meal.resolution_signals = album["resolution_signals"]
        meal.rev = (meal.rev or 0) + 1
        meal.updated_at = now
        for row in db.scalars(
            select(MealMessage).where(
                MealMessage.meal_id == meal.id,
                MealMessage.view.in_(LINE_VIEWS),
            )
        ):
            take_back(row)
        for row_id in album["lines"]:
            row = db.get(MealMessage, row_id)
            if row is not None and row.meal_id == meal.id:
                row.view = "line"
        for other_id in event.after["album"]["meals"]:
            other = crud_meal.get_any(db, other_id)
            for source in db.scalars(
                select(MealSource).where(MealSource.meal_id == other.id)
            ):
                source.meal_id = meal.id
            for row in db.scalars(
                select(MealMessage).where(MealMessage.meal_id == other.id)
            ):
                if row.view in LINE_VIEWS:
                    take_back(row)
                row.meal_id = meal.id
            for item in crud_meal_item.live(db, other.id):
                item.removed_at = now
            other.merged_into_id = meal.id
            other.deleted_at = now
            other.rev = (other.rev or 0) + 1
            other.updated_at = now
        db.flush()
        items = [crud_meal_item.get(db, int(key)) for key in event.after["items"]]
        back = crud_record_event.add(
            db,
            user_id=event.user_id,
            kind=event.kind,
            meal_id=meal.id,
            before=event.after,
            after=_meal_state(meal, items),
            undoes_event_id=event.id,
        )
        db.commit()
        return Change(DONE, meal, back, gone=gone)

    return _rolled_back(db, write)


def replace_item(
    db: Session,
    user_id: int,
    item_id: int,
    *,
    food_id: Optional[int] = None,
    name: Optional[str] = None,
) -> Change:
    """Another dish in this line's place: the grams, the words and the quantity stay.

    A plate's line stays a plate's line, so the plate's totals do not move.
    """
    item, meal = _own_item(db, user_id, item_id)
    if item is None:
        return Change(STALE)
    dish = _own_dish(db, user_id, food_id, name)
    if dish is None:
        db.rollback()
        return Change(STALE, meal)
    if item.personal_food_id == dish.id:
        return Change(SAME, meal)
    columns = {"numbers_source": item.numbers_source}
    if item.numbers_source != "plate_share":
        portion = dish_portion(db, dish)
        per_100 = None if _is_plate_row(db, dish) else _per_100(dish)
        if per_100 and item.grams:
            portion = Portion(per_100, 100.0)
        columns = _with_numbers(portion)
    factor = item.factor
    if item.grams and item.numbers_source != "plate_share":
        # The typed weight stays, so it counts from the new dish's own portion.
        base = columns.get("base_grams")
        factor = item.grams / base if base else 1.0

    def mutate(_live: list) -> list:
        new = crud_meal_item.add(
            db,
            meal_id=meal.id,
            name=_clean_name(dish.canonical_name),
            personal_food_id=dish.id,
            position=item.position,
            amount_text=item.amount_text,
            grams=item.grams,
            factor=factor,
            by_user=True,
            **columns,
        )
        # The new line is the old one's: ➖ Отделить takes it where the old would go.
        new.origin_meal_id = item.origin_meal_id
        new.source_id = item.source_id
        item.removed_at = datetime.now(UTC)
        item.replaced_by_item_id = new.id
        _used(db, dish, meal, one_dish=False)
        return [new]

    return _changed(db, meal, "replace", mutate, food_id=dish.id)


def fill_numbers(
    db: Session, item_id: int, rev: int, event_id: int, nutrition: dict
) -> bool:
    """The model's numbers for a typed new name, arriving after the answer (N4).

    They apply only while ``rev`` still matches (3.1), so the event that made the item
    is the meal's latest. It learns what it wrote, or its ↩️ would be refused for good.
    """
    item = crud_meal_item.get(db, item_id)
    meal = crud_meal.get(db, item.meal_id) if item else None
    event = crud_record_event.get(db, event_id)
    if (
        meal is None
        or event is None
        or event.meal_id != meal.id
        or item.removed_at is not None
        or item.numbers_source != "none"
        or meal.rev != rev
        or not has_usable_macros(nutrition.get("calories"))
    ):
        return False

    def write() -> bool:
        for key, name in _NUTRIENT_KEYS.items():
            setattr(item, f"base_{key}", nutrition.get(name))
        grams = _parse_portion_grams(nutrition)
        item.base_grams = grams
        item.factor = item.grams / grams if item.grams and grams else 1.0
        item.numbers_source = "model"
        db.flush()
        live = crud_meal_item.live(db, meal.id)
        known = _groups_by_item(meal, live)
        _recompute(meal, live)
        _rewrite_fields(db, meal, live, known)
        meal.rev = rev + 1
        meal.updated_at = datetime.now(UTC)
        db.flush()
        written = [crud_meal_item.get(db, int(key)) for key in event.after["items"]]
        # What else the event remembers (a join's way back) stays.
        event.after = {**event.after, **_meal_state(meal, written)}
        db.commit()
        return True

    return _rolled_back(db, write)


def _soft_delete(db: Session, meal: Meal, *, food_id: Optional[int] = None) -> Change:
    """Items and totals stay, so undo restores exactly."""

    def write() -> Change:
        items = crud_meal_item.live(db, meal.id)
        before = _meal_state(meal, items)
        meal.deleted_at = datetime.now(UTC)
        meal.rev = (meal.rev or 0) + 1
        meal.updated_at = datetime.now(UTC)
        db.flush()
        event = crud_record_event.add(
            db,
            user_id=meal.user_id,
            kind="delete",
            meal_id=meal.id,
            food_id=food_id,
            before=before,
            after=_meal_state(meal, items),
        )
        db.commit()
        return Change(DELETED, meal, event)

    return _rolled_back(db, write)


def delete_meal(db: Session, user_id: int, meal_id: int) -> Change:
    meal = _own_meal(db, user_id, meal_id)
    return Change(STALE) if meal is None else _soft_delete(db, meal)


def remove_item(db: Session, user_id: int, item_id: int) -> Change:
    """A dish out of the meal. The last one takes the meal with it, softly."""
    item, meal = _own_item(db, user_id, item_id)
    if item is None:
        return Change(STALE)
    if len(crud_meal_item.live(db, meal.id)) == 1:
        return _soft_delete(db, meal, food_id=item.personal_food_id)

    def mutate(_live: list) -> list:
        item.removed_at = datetime.now(UTC)
        return []

    return _changed(db, meal, "remove", mutate, food_id=item.personal_food_id)


# --- undo -------------------------------------------------------------------


def _utc(moment: datetime) -> datetime:
    # SQLite hands back naive datetimes; the column is written in UTC.
    return (
        moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)
    )


def _time_state(meal: Meal) -> dict:
    """A time event's own state, apart from ``_meal_state``: a change of the dishes
    does not stop the undo of a time, and a change of the time does not stop theirs."""
    return {
        "time": {
            "meal_time": _utc(meal.meal_time).isoformat(),
            **{key: getattr(meal, key) for key in _TIME_FIELDS},
        }
    }


def _to_the_minute(moment: datetime) -> datetime:
    return _utc(moment).replace(second=0, microsecond=0)


def set_time(
    db: Session, user_id: int, meal_id: int, moment: datetime, precision: str
) -> Change:
    """When the meal was eaten. ``sent_at`` stays: it is when the message came.

    The same minute at the same precision writes nothing: a person reads minutes.
    """
    meal = _own_meal(db, user_id, meal_id)
    if meal is None:
        return Change(STALE)
    if (
        _to_the_minute(meal.meal_time) == _to_the_minute(moment)
        and meal.time_precision == precision
    ):
        return Change(SAME, meal)

    def write() -> Change:
        before = _time_state(meal)
        meal.meal_time = moment.astimezone(UTC)
        meal.time_precision = precision
        meal.time_source = "edited"
        meal.time_phrase = None
        meal.rev = (meal.rev or 0) + 1
        meal.updated_at = datetime.now(UTC)
        db.flush()
        event = crud_record_event.add(
            db,
            user_id=user_id,
            kind="time",
            meal_id=meal.id,
            before=before,
            after=_time_state(meal),
        )
        db.commit()
        return Change(DONE, meal, event)

    return _rolled_back(db, write)


def _undo_kind(kind: str) -> str:
    return {"delete": "restore", "restore": "delete"}.get(kind, kind)


def _put_back(db: Session, meal: Meal, event: RecordEvent) -> None:
    for key in (*_MEAL_FIELDS, *_MEAL_JSON):
        setattr(meal, key, event.before["meal"][key])
    meal.deleted_at = datetime.now(UTC) if event.before["meal"]["deleted"] else None
    now = datetime.now(UTC)
    for item_id, wrote in event.after["items"].items():
        item = crud_meal_item.get(db, int(item_id))
        state = event.before["items"].get(item_id)
        if state is None:  # the change made this item
            item.removed_at = now
            continue
        for key in _ITEM_FIELDS:
            setattr(item, key, state[key])
        # An event from before the column has no word on it, and takes nothing away.
        if "base_grams_assumed" in state:
            item.base_grams_assumed = state["base_grams_assumed"]
        item.personal_food_id = _live_dish_id(db, state["personal_food_id"])
        if state["removed"] != wrote["removed"]:
            item.removed_at = now if state["removed"] else None


def _undo_time(db: Session, meal: Meal, event: RecordEvent) -> Change:
    def write() -> Change:
        state = event.before["time"]
        meal.meal_time = datetime.fromisoformat(state["meal_time"])
        for key in _TIME_FIELDS:
            setattr(meal, key, state[key])
        meal.rev = (meal.rev or 0) + 1
        meal.updated_at = datetime.now(UTC)
        db.flush()
        back = crud_record_event.add(
            db,
            user_id=event.user_id,
            kind=event.kind,
            meal_id=meal.id,
            before=event.after,
            after=_time_state(meal),
            undoes_event_id=event.id,
        )
        db.commit()
        return Change(DONE, meal, back)

    return _rolled_back(db, write)


def _live_dish_id(db: Session, food_id: Optional[int]) -> Optional[int]:
    """Where a merged dish went: an undo neither stops at a merge nor goes back to the
    merged row."""
    dish = db.get(PersonalFood, food_id) if food_id else None
    return crud_personal_food.live_of(db, dish).id if dish is not None else food_id


def _past_merges(db: Session, state: dict) -> dict:
    """A state with its links past any merge. A merge moves an item's link and nothing
    else: the record is what it was, so it does not count as a change since."""
    return {
        **state,
        "items": {
            key: {
                **item,
                "personal_food_id": _live_dish_id(db, item["personal_food_id"]),
            }
            for key, item in state["items"].items()
        },
    }


def _still_holds(db: Session, meal: Meal, event: RecordEvent) -> bool:
    wrote = event.after or {}
    if "time" in wrote:
        return meal.deleted_at is None and _time_state(meal) == wrote
    if set(wrote) != {"meal", "items"}:
        return False
    items = [crud_meal_item.get(db, int(key)) for key in wrote["items"]]
    if not all(items):
        return False
    state = _meal_state(meal, items)
    for key, item in wrote["items"].items():
        if "base_grams_assumed" not in item:  # written before the column
            state["items"][key].pop("base_grams_assumed")
    return _past_merges(db, state) == _past_merges(db, wrote)


_NO_UNDO = ("join", "split")  # ➖ Отделить and 🔗 Объединить are their ways back


def undo(db: Session, user_id: int, event_id: int) -> Change:
    """Back to what the event found. Writes an event, so it can be undone in turn.

    When the fields no longer hold what the event wrote, nothing changes: the screen
    shows ``UNDO_REFUSED``.
    """
    event = crud_record_event.get(db, event_id)
    if event is None or event.user_id != user_id or event.meal_id is None:
        return Change(STALE)
    meal = crud_meal.get_any(db, event.meal_id)
    if meal is None:
        return Change(STALE)
    if "album" in (event.after or {}):
        return _undo_album(db, meal, event)
    if event.kind in _NO_UNDO or not _still_holds(db, meal, event):
        return Change(REFUSED, meal)
    if "time" in event.after:
        return _undo_time(db, meal, event)

    def write() -> Change:
        _put_back(db, meal, event)
        meal.rev = (meal.rev or 0) + 1
        meal.updated_at = datetime.now(UTC)
        db.flush()
        items = [crud_meal_item.get(db, int(key)) for key in event.after["items"]]
        back = crud_record_event.add(
            db,
            user_id=user_id,
            kind=_undo_kind(event.kind),
            meal_id=meal.id,
            food_id=event.food_id,
            before=event.after,
            after=_meal_state(meal, items),
            undoes_event_id=event.id,
        )
        db.commit()
        return Change(DONE, meal, back)

    return _rolled_back(db, write)
