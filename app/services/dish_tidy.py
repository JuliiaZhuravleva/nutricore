"""🍲 Порядок в блюдах: which of a person's dishes are one dish, and which deserve a ⭐.

``find`` reads the person's rows; the pure ``plan`` decides. The dry run feeds ``plan``
from a file, so what it prints is what a Sunday would do. Nothing here writes or calls
a model: embeddings already stored are compared in the database.

Three kinds of row:
- **same**: the names are equal once a maker's tail, sizes, percents, punctuation,
  a leading «это» and word order are set aside, and the numbers agree. Merged by
  itself before the message goes out, listed ticked, undoable.
- **merge**: likely one dish (word stems, or stored embeddings close). Proposed,
  ticked; unticked when the sizes differ, or when one name adds a word the stored
  embeddings do not vouch for («морковь по-домашнему» / «морковь» is another dish).
- **star**: eaten on at least ``STAR_DAYS`` days of the last ``WINDOW_DAYS``, no ⭐.

Different fat percents never pair, and neither do names told apart by a connector
(«гречка с маслом» / «гречка») or by a qualifier a maker's name keeps in brackets
(«кефир (безлактозный)» / «кефир»).
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Iterable, Optional

from sqlalchemy import select

from app.core.config import settings
from app.crud.crud_dish_tidy import crud_dish_tidy
from app.crud.crud_favorite_meal import crud_favorite_meal
from app.crud.crud_personal_food import AnnQueryError, crud_personal_food
from app.models.favorite_meal import FavoriteMeal
from app.models.meal import Meal
from app.models.meal_item import MealItem
from app.models.record_event import RecordEvent
from app.models.user import User
from app.services.day_card import local_time
from app.services.meal_items import product_dish_name
from app.services.settings_screen import user_zone

logger = logging.getLogger(__name__)

SAME, MERGE, STAR = "same", "merge", "star"
SAME_KCAL = 0.10
LIKELY_KCAL = 0.30
SAME_GRAMS = 0.15
MAX_EXTRA_WORDS = 2
PER_TARGET = 3
SAME_CAP = 10
PROPOSAL_CAP = 10
STAR_CAP = 5
STAR_DAYS = 3
WINDOW_DAYS = 28
DECLINE_DAYS = 90
MAX_EMBEDDING_ROWS = 2000

CONNECTORS = frozenset({"с", "со", "без", "и", "на", "в", "во", "из", "+"})
_UNITS = {
    "г": "г", "гр": "г", "грамм": "г", "грамма": "г", "граммов": "г", "g": "г",
    "кг": "кг", "kg": "кг", "мл": "мл", "ml": "мл", "л": "л", "l": "л",
    "шт": "шт", "штук": "шт", "штуки": "шт", "ккал": "ккал", "kcal": "ккал",
}  # fmt: skip
_SIZE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*("
    + "|".join(sorted(_UNITS, key=len, reverse=True))
    + r")\.?(?![^\W\d_])",
    re.IGNORECASE,
)
_PERCENT = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
_BRACKETS = re.compile(r"\(([^()]*)\)")
_DASH_TAIL = re.compile(r"\s+[—–-]\s+(.+)$")
_WORD = re.compile(r"[^\W_]+|\+")


# --- names ------------------------------------------------------------------------


def _fold(text: str) -> str:
    return text.casefold().replace("ё", "е")


def _number(raw: str) -> str:
    value = float(raw.replace(",", "."))
    return f"{value:g}"


def sizes(name: str) -> frozenset:
    """«сырок (Бренд, 40 г)» → {"40г"}; read from the whole name, tail included."""
    return frozenset(
        _number(value) + _UNITS[unit.casefold()]
        for value, unit in _SIZE.findall(_fold(name))
    )


def percents(name: str) -> frozenset:
    return frozenset(_number(value) + "%" for value in _PERCENT.findall(_fold(name)))


def _words(text: str) -> list:
    text = _PERCENT.sub(" ", _SIZE.sub(" ", _fold(text)))
    return _WORD.findall(text)


@dataclass(frozen=True)
class Name:
    words: tuple  # sorted, without a leading «это», sizes or percents
    marks: frozenset  # the words a kept bracket or dash tail adds: they tell apart
    sizes: frozenset
    percents: frozenset

    @property
    def key(self) -> str:
        return " ".join(self.words)


def read_name(name: str) -> Name:
    short = product_dish_name(name) or name or ""
    marks: set = set()
    for inner in _BRACKETS.findall(short):
        marks.update(_words(inner))
    tail = _DASH_TAIL.search(short)
    if tail:
        marks.update(_words(tail.group(1)))
    words = _words(short)
    if len(words) > 1 and words[0] == "это":
        words = words[1:]
    return Name(
        words=tuple(sorted(words)),
        marks=frozenset(marks),
        sizes=sizes(name or ""),
        percents=percents(name or ""),
    )


_ENDINGS = sorted(
    (
        "ого", "его", "ому", "ему", "ыми", "ими", "ами", "ями",
        "ой", "ей", "ый", "ий", "ая", "яя", "ое", "ее", "ые", "ие", "ую", "юю",
        "ом", "ем", "ам", "ям", "ах", "ях", "ов", "ев", "ью",
        "а", "я", "о", "е", "ы", "и", "у", "ю", "ь", "й",
    ),
    key=len,
    reverse=True,
)  # fmt: skip


def stem(word: str) -> str:
    """The word without a case or gender ending, keeping three letters at least."""
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            return word[: -len(ending)]
    return word


def same_word(one: str, other: str) -> bool:
    """One word in another form: «гречка»/«гречкой», «томатный»/«томатного»; never
    «сыр»/«сырок» or «белый»/«белок»: the rest must be an ending, not a suffix."""
    return one == other or stem(one) == stem(other)


def _extra(shorter: Name, longer: Name) -> Optional[list]:
    """The longer name's words no word of the shorter matches; None when a word of
    the shorter (three letters or more) is not in the longer."""
    left = list(longer.words)
    for word in shorter.words:
        if len(word) < 3:
            continue
        match = next((near for near in left if same_word(word, near)), None)
        if match is None:
            return None
        left.remove(match)
    return [word for word in left if word not in shorter.words]


def _told_apart(one: Name, other: Name) -> bool:
    """A connector or a kept qualifier on one side only, or two fat percents."""
    if one.percents and other.percents and one.percents != other.percents:
        return True
    only = set(one.words) ^ set(other.words)
    if only & CONNECTORS:
        return True
    return bool(only & (one.marks | other.marks))


def _stems_extra(one: Name, other: Name) -> Optional[list]:
    """The words the longer name adds when the stems match, else None."""
    shorter, longer = sorted((one, other), key=lambda n: len(n.words))
    if not any(len(word) >= 3 for word in shorter.words):
        return None
    extra = _extra(shorter, longer)
    return extra if extra is not None and len(extra) <= MAX_EXTRA_WORDS else None


def stems_match(one: Name, other: Name) -> bool:
    return _stems_extra(one, other) is not None


# --- dishes and rows ----------------------------------------------------------------


@dataclass(frozen=True)
class Dish:
    id: int
    name: str
    kcal: Optional[float] = None  # per 100 g
    by_user: bool = False  # the person typed the numbers
    grams: Optional[float] = None  # a portion the person set
    items: int = 0  # live meal items
    days: frozenset = frozenset()  # local days eaten
    starred: bool = False
    star_removed: bool = False


@dataclass(frozen=True)
class Decline:
    fingerprint: Optional[str]  # None: the pair stays apart whatever changes (by hand)
    at: datetime


@dataclass
class Row:
    kind: str
    source_id: int
    target_id: Optional[int]
    ticked: bool
    key: str
    fingerprint: str
    names: tuple  # (source, target) for a merge, (dish,) for a ⭐
    days: int = 0


@dataclass
class Plan:
    rows: list = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.rows


def pair_key(one: int, other: int) -> str:
    low, high = sorted((one, other))
    return f"m:{low}:{high}"


def star_key(dish_id: int) -> str:
    return f"s:{dish_id}"


def _fingerprint(*parts) -> str:
    raw = "|".join("" if part is None else str(part) for part in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def pair_fingerprint(one: Dish, other: Dish) -> str:
    low, high = sorted((one, other), key=lambda d: d.id)
    return _fingerprint(
        read_name(low.name).key, low.kcal, read_name(high.name).key, high.kcal
    )


def star_fingerprint(dish: Dish) -> str:
    return _fingerprint(read_name(dish.name).key, dish.kcal)


def _near(one: Optional[float], other: Optional[float], share: float) -> bool:
    if not one or not other:
        return True  # one side has no number: nothing to disagree with
    return abs(one - other) / min(one, other) <= share


def _tier(
    one: Dish, other: Dish, distance: Optional[float], threshold: float
) -> Optional[tuple]:
    """(kind, ticked) of a pair, or None."""
    a, b = read_name(one.name), read_name(other.name)
    if _told_apart(a, b):
        return None
    size_conflict = bool(a.sizes and b.sizes and a.sizes != b.sizes)
    equal = a.key == b.key and bool(a.key)
    if (
        equal
        and not size_conflict
        and a.percents == b.percents
        and _near(one.kcal, other.kcal, SAME_KCAL)
        and _near(one.grams, other.grams, SAME_GRAMS)
    ):
        return SAME, True
    extra = _stems_extra(a, b)
    close = distance is not None and distance <= threshold
    if (equal or extra is not None or close) and _near(
        one.kcal, other.kcal, LIKELY_KCAL
    ):
        # An added word often makes another dish: ticked only when embeddings agree.
        adds_a_word = not equal and bool(extra) and not close
        return MERGE, not size_conflict and not adds_a_word
    return None


def _target_order(dish: Dish) -> tuple:
    return (not dish.by_user, -dish.items, len(read_name(dish.name).key), dish.id)


def _declined(key: str, fingerprint: str, declines: dict, now: datetime) -> bool:
    decline = declines.get(key)
    if decline is None:
        return False
    at = decline.at if decline.at.tzinfo else decline.at.replace(tzinfo=UTC)
    if now - at > timedelta(days=DECLINE_DAYS):
        return False
    return decline.fingerprint is None or decline.fingerprint == fingerprint


def _groups(ids: Iterable[int], pairs: dict) -> list:
    """Union-find over the pairs: lists of ids that hang together."""
    parent = {i: i for i in ids}

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for one, other in pairs:
        parent[root(one)] = root(other)
    groups: dict = {}
    for i in parent:
        groups.setdefault(root(i), []).append(i)
    return [members for members in groups.values() if len(members) > 1]


def _rows_of(kind: str, pairs: dict, dishes: dict, cap: int) -> list:
    """One row per source → target, at most PER_TARGET per target; only a dish that
    pairs with the target itself goes into it."""
    involved = {i for pair in pairs for i in pair}
    rows = []
    for members in _groups(involved, pairs):
        ordered = sorted((dishes[i] for i in members), key=_target_order)
        target = ordered[0]
        sources = [
            dish
            for dish in ordered[1:]
            if (min(dish.id, target.id), max(dish.id, target.id)) in pairs
        ][:PER_TARGET]
        for source in sources:
            ticked = pairs[(min(source.id, target.id), max(source.id, target.id))]
            rows.append(
                Row(
                    kind=kind,
                    source_id=source.id,
                    target_id=target.id,
                    ticked=ticked,
                    key=pair_key(source.id, target.id),
                    fingerprint=pair_fingerprint(source, target),
                    names=(source.name, target.name),
                    days=source.items,
                )
            )
    rows.sort(key=lambda row: (-(dishes[row.source_id].items), row.source_id))
    return rows[:cap]


def plan(
    dishes: list,
    close_pairs: dict,
    declines: dict,
    now: datetime,
    *,
    today: Optional[date] = None,
    threshold: Optional[float] = None,
) -> Plan:
    """What a tidy-up would do to these live dishes. ``close_pairs`` maps (low id,
    high id) to the embedding distance; ``declines`` maps a pair or ⭐ key to a
    ``Decline``. ``today`` is the person's local date (the ⭐ window ends on it)."""
    threshold = settings.SAVED_FOOD_SIM_THRESHOLD if threshold is None else threshold
    today = today or now.date()
    by_id = {dish.id: dish for dish in dishes}
    same: dict = {}
    likely: dict = {}
    ids = sorted(by_id)
    for i, one_id in enumerate(ids):
        for other_id in ids[i + 1 :]:
            one, other = by_id[one_id], by_id[other_id]
            tier = _tier(one, other, close_pairs.get((one_id, other_id)), threshold)
            if tier is None:
                continue
            kind, ticked = tier
            key = pair_key(one_id, other_id)
            if _declined(key, pair_fingerprint(one, other), declines, now):
                continue
            (same if kind == SAME else likely)[(one_id, other_id)] = ticked
    same_rows = _rows_of(SAME, same, by_id, SAME_CAP)

    # The proposals see the dishes as the merges leave them.
    gone = {row.source_id for row in same_rows}
    into: dict = {}
    for row in same_rows:
        into.setdefault(row.target_id, []).append(by_id[row.source_id])
    after = {}
    for dish_id, dish in by_id.items():
        if dish_id in gone:
            continue
        merged = into.get(dish_id, [])
        after[dish_id] = Dish(
            id=dish.id,
            name=dish.name,
            kcal=dish.kcal,
            by_user=dish.by_user,
            grams=dish.grams,
            items=dish.items + sum(d.items for d in merged),
            days=dish.days.union(*(d.days for d in merged)),
            starred=dish.starred or any(d.starred for d in merged),
            star_removed=dish.star_removed,
        )
    likely = {
        pair: ticked
        for pair, ticked in likely.items()
        if pair[0] in after and pair[1] in after and pair not in same
    }
    merge_rows = _rows_of(MERGE, likely, after, PROPOSAL_CAP)

    proposed_sources = {row.source_id for row in merge_rows}
    first = today - timedelta(days=WINDOW_DAYS - 1)
    star_rows = []
    for dish in after.values():
        if dish.starred or dish.star_removed or dish.id in proposed_sources:
            continue
        count = sum(1 for day in dish.days if first <= day <= today)
        if count < STAR_DAYS:
            continue
        key, fingerprint = star_key(dish.id), star_fingerprint(dish)
        if _declined(key, fingerprint, declines, now):
            continue
        star_rows.append(
            Row(
                kind=STAR,
                source_id=dish.id,
                target_id=None,
                ticked=True,
                key=key,
                fingerprint=fingerprint,
                names=(dish.name,),
                days=count,
            )
        )
    star_rows.sort(key=lambda row: (-row.days, row.source_id))
    room = max(PROPOSAL_CAP - len(merge_rows), 0)
    return Plan(same_rows + merge_rows + star_rows[: min(STAR_CAP, room)])


# --- reading the person's rows --------------------------------------------------------


def _eaten(db, user: User, zone, first_day: date) -> tuple:
    """(dish id → live items, dish id → local days eaten since ``first_day``)."""
    rows = db.execute(
        select(MealItem.personal_food_id, Meal.meal_time)
        .join(Meal, Meal.id == MealItem.meal_id)
        .where(
            Meal.user_id == user.id,
            Meal.deleted_at.is_(None),
            Meal.is_draft.is_(False),
            MealItem.removed_at.is_(None),
            MealItem.personal_food_id.is_not(None),
        )
    )
    counts: dict = {}
    days: dict = {}
    for food_id, moment in rows:
        counts[food_id] = counts.get(food_id, 0) + 1
        if moment is None:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)  # SQLite drops it
        day = local_time(moment, zone).date()
        if day >= first_day:
            days.setdefault(food_id, set()).add(day)
    return counts, days


def _stars(db, user: User) -> tuple:
    """(dishes with an active ⭐, dishes with a removed one). Reads only."""
    active = set()
    for fav in crud_favorite_meal.list_active(db, user.id):
        dish = crud_favorite_meal.dish_if_any(db, fav)
        if dish is not None:
            active.add(dish.id)
    # A ⭐ the tidy-up's own ↩️ took back is a decline (90 days, by its row), not a
    # ⭐ the person removed: that one is never offered again.
    taken_back = {
        (event.before or {}).get("favourite")
        for event in db.scalars(
            select(RecordEvent).where(
                RecordEvent.user_id == user.id,
                RecordEvent.kind == "food_star",
                RecordEvent.undoes_event_id.is_not(None),
            )
        )
    }
    removed = {
        food_id
        for fav_id, food_id in db.execute(
            select(FavoriteMeal.id, FavoriteMeal.personal_food_id).where(
                FavoriteMeal.user_id == user.id,
                FavoriteMeal.removed_at.is_not(None),
                FavoriteMeal.personal_food_id.is_not(None),
            )
        )
        if fav_id not in taken_back
    }
    return active, removed


def hand_unmerges(db, user: User, now: datetime) -> dict:
    """Pairs a person took apart in 🍲 Блюда in the last ``DECLINE_DAYS``: an undo of
    a merge with no group. Not merged or proposed again, whatever changes."""
    since = now - timedelta(days=DECLINE_DAYS)
    found = {}
    for event in db.scalars(
        select(RecordEvent).where(
            RecordEvent.user_id == user.id,
            RecordEvent.kind == "food_merge",
            RecordEvent.undoes_event_id.is_not(None),
            RecordEvent.group_id.is_(None),
            RecordEvent.created_at >= since,
        )
    ):
        target = (event.before or {}).get("merged_into")
        if target and event.food_id:
            found[pair_key(event.food_id, target)] = Decline(None, event.created_at)
    return found


def close_pairs(db, user: User, dish_ids: set) -> dict:
    """Stored embeddings near each other, among live dishes. Never a model call; a
    failed query leaves the stems alone to judge, and says so at ERROR."""
    try:
        found = crud_personal_food.close_pairs(
            db, user.id, settings.SAVED_FOOD_SIM_THRESHOLD, MAX_EMBEDDING_ROWS
        )
    except AnnQueryError:
        logger.error(
            "dish tidy: close pairs failed for user_id=%s, stems only", user.id
        )
        return {}
    return {pair: d for pair, d in found.items() if set(pair) <= dish_ids}


def declines(db, user: User, now: datetime) -> dict:
    """Every pair and ⭐ the person turned down lately: rows unticked or returned in
    a batch, and merges taken apart by hand in 🍲 Блюда."""
    since = now - timedelta(days=DECLINE_DAYS)
    found = {
        key: Decline(fingerprint, at)
        for key, (fingerprint, at) in crud_dish_tidy.declines(
            db, user.id, since
        ).items()
    }
    found.update(hand_unmerges(db, user, now))
    return found


def _float(value) -> Optional[float]:
    # Numeric reads back as Decimal: a float keeps the fingerprint the same either way.
    return None if value is None else round(float(value), 2)


def find(db, user: User, now: datetime) -> Plan:
    """The tidy-up for this person now. Reads only."""
    zone = user_zone(user)
    today = local_time(now, zone).date()
    live = crud_personal_food.list_live(db, user.id)
    counts, days = _eaten(db, user, zone, today - timedelta(days=WINDOW_DAYS - 1))
    active, removed = _stars(db, user)
    dishes = [
        Dish(
            id=dish.id,
            name=dish.canonical_name,
            kcal=_float(dish.per_100g_calories),
            by_user=bool(dish.numbers_by_user),
            grams=dish.usual_grams if dish.usual_grams_by_user else None,
            items=counts.get(dish.id, 0),
            days=frozenset(days.get(dish.id, ())),
            starred=dish.id in active,
            star_removed=dish.id in removed,
        )
        for dish in live
    ]
    near = (
        close_pairs(db, user, {dish.id for dish in dishes}) if len(dishes) > 1 else {}
    )
    return plan(dishes, near, declines(db, user, now), now, today=today)
