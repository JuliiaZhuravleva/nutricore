"""Dish changes without a tap: merge, ⭐, rename, hide, and one undo for a group of them.

``dishes`` calls these for a tap; the tidy-up (``dish_tidy``) calls them for a batch.
Nothing here commits: every function flushes, and the caller commits once, so a batch
is all or nothing. Nothing here knows Telegram or calls a model.

A change refused because the record moved on raises ``Refused`` with the dish's name;
the caller says so. Each change writes a ``RecordEvent``; one ``group_id`` ties the
changes of one batch, and ``undo_group`` takes them back newest first.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import select

from app.crud.crud_favorite_meal import crud_favorite_meal
from app.crud.crud_personal_food import crud_personal_food
from app.crud.crud_record_event import crud_record_event
from app.models.favorite_meal import FavoriteMeal
from app.models.meal_item import MealItem
from app.models.personal_food import PersonalFood
from app.models.record_event import RecordEvent
from app.models.user import User
from app.services import meal_items

# The kinds ``undo`` takes back. The rest stay with the screens that wrote them.
GROUP_KINDS = ("food_merge", "food_star", "food_unstar", "rename", "food_hide")


class Refused(Exception):
    """The record moved on since the change it would take back or repeat, or the change
    cannot be made; ``reason`` says which when the caller words it."""

    def __init__(self, name: str, reason: Optional[str] = None):
        super().__init__(name)
        self.name = name
        self.reason = reason


class NameTaken(Refused):
    """A rename onto a name another row holds: ``holder`` is the live dish it leads to
    (the dish itself when the name already finds it)."""

    def __init__(self, name: str, holder: PersonalFood):
        super().__init__(name, reason="taken")
        self.holder = holder


@dataclass
class GroupUndo:
    undone: int = 0
    refused_names: list = field(default_factory=list)
    already: bool = False  # nothing left to take back: a second tap


def new_group() -> str:
    return str(uuid.uuid4())


def _stamp(moment: Optional[datetime]) -> float:
    if moment is None:
        return 0.0
    if moment.tzinfo is None:  # SQLite gives it back naive
        moment = moment.replace(tzinfo=UTC)
    return moment.timestamp()


def lock_user(db, user: User) -> None:
    """The person's row, locked until the commit: two writers of one person's dishes
    take turns. The rows must be read: an unread result neither locks on every dialect
    nor refreshes ``user`` (populate_existing works as rows are fetched)."""
    db.scalars(
        select(User)
        .where(User.id == user.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()


def link_stars(db, user_id: int) -> None:
    """An old ⭐ row gets its dish link, so a check by dish sees it. Flushes only."""
    for fav in crud_favorite_meal.list_active(db, user_id):
        crud_favorite_meal.dish_of(db, fav)


def _active_stars(db, dish: PersonalFood) -> list:
    return list(
        db.scalars(
            select(FavoriteMeal).where(
                FavoriteMeal.personal_food_id == dish.id,
                FavoriteMeal.removed_at.is_(None),
            )
        )
    )


def _is_live(dish: PersonalFood) -> bool:
    return dish.merged_into_id is None and dish.hidden_at is None


# --- merge --------------------------------------------------------------------


def merge(
    db,
    user: User,
    source: PersonalFood,
    target: PersonalFood,
    *,
    now: Optional[datetime] = None,
    group_id: Optional[str] = None,
    undoes: Optional[int] = None,
) -> RecordEvent:
    """The source's live items and ⭐ go to the target. Their names, numbers and
    ``original_food_id`` stay: the past is what it was.

    The caller holds ``lock_user``; both rows are read again under it. A source no
    longer live, or one that would go into itself, is refused."""
    now = now or datetime.now(UTC)
    db.refresh(source)
    db.refresh(target)
    target = crud_personal_food.live_of(db, target)
    if not _is_live(source) or target.id == source.id or target.user_id != user.id:
        raise Refused(source.canonical_name)
    items = list(
        db.scalars(select(MealItem).where(MealItem.personal_food_id == source.id))
    )
    for item in items:
        item.personal_food_id = target.id
    moved_stars, removed_stars = [], []
    keeps_own = crud_favorite_meal.active_of_dish(db, target.id) is not None
    for fav in _active_stars(db, source):
        if keeps_own:
            fav.removed_at = now
            removed_stars.append(fav.id)
        else:
            fav.personal_food_id = target.id
            moved_stars.append(fav.id)
    target_hidden = target.hidden_at
    target.hidden_at = None
    source.merged_into_id = target.id
    return crud_record_event.add(
        db,
        user_id=user.id,
        kind="food_merge",
        food_id=source.id,
        before={
            "merged_into": None,
            "target_hidden": _stamp(target_hidden) if target_hidden else None,
            "target_used": target.times_used,
        },
        after={
            "merged_into": target.id,
            "items": [item.id for item in items],
            "stars_moved": moved_stars,
            "stars_removed": removed_stars,
        },
        undoes_event_id=undoes,
        group_id=group_id,
    )


def unmerge(
    db,
    user: User,
    source: PersonalFood,
    event: RecordEvent,
    *,
    group_id: Optional[str] = None,
) -> RecordEvent:
    """Only what this merge moved: what was eaten under the target since stays there."""
    after = event.after or {}
    target = db.get(PersonalFood, after.get("merged_into") or 0)
    if source.merged_into_id != after.get("merged_into") or target is None:
        raise Refused(source.canonical_name)
    live = crud_personal_food.live_of(db, target)
    moved = after.get("items") or []
    if moved:
        items = db.scalars(select(MealItem).where(MealItem.id.in_(moved)))
        for item in items:
            if item.personal_food_id == live.id:
                item.personal_food_id = source.id
    for fav_id in after.get("stars_moved") or []:
        fav = db.get(FavoriteMeal, fav_id)
        if fav is not None and fav.personal_food_id == live.id:
            fav.personal_food_id = source.id
    for fav_id in after.get("stars_removed") or []:
        fav = db.get(FavoriteMeal, fav_id)
        if fav is not None:
            fav.removed_at = None
    before = event.before or {}
    hidden = before.get("target_hidden")
    # A dish eaten or starred since the merge was asked for again, so it stays.
    in_use = (
        before.get("target_used") != target.times_used
        or crud_favorite_meal.active_of_dish(db, target.id) is not None
    )
    if hidden and target.hidden_at is None and not in_use:
        target.hidden_at = datetime.fromtimestamp(hidden, UTC)
    source.merged_into_id = None
    return crud_record_event.add(
        db,
        user_id=user.id,
        kind="food_merge",
        food_id=source.id,
        before={"merged_into": target.id},
        after={"merged_into": None},
        undoes_event_id=event.id,
        group_id=group_id,
    )


def merge_again(
    db,
    user: User,
    source: PersonalFood,
    event: RecordEvent,
    *,
    now: Optional[datetime] = None,
    group_id: Optional[str] = None,
) -> tuple:
    """The undo of an undo: (the new event, the dish it went into)."""
    target = db.get(PersonalFood, (event.before or {}).get("merged_into") or 0)
    if not _is_live(source) or target is None:
        raise Refused(source.canonical_name)
    live = crud_personal_food.live_of(db, target)
    if live.id == source.id:
        raise Refused(source.canonical_name)
    new = merge(db, user, source, live, now=now, group_id=group_id, undoes=event.id)
    return new, live


# --- ⭐ -------------------------------------------------------------------------


def create_star(db, user_id: int, dish: PersonalFood, *, commit: bool):
    """The ⭐ row for a dish, its numbers as its portion has them. No event: the tap
    in 🍲 Блюда writes none, ``star`` adds its own."""
    portion = meal_items.dish_portion(db, dish)
    numbers = portion.numbers or {key: None for key in meal_items.FIELDS}
    return crud_favorite_meal.create_from_numbers(
        db,
        user_id=user_id,
        name=dish.canonical_name,
        numbers=numbers,
        nutrients={
            "foods": [dish.canonical_name],
            "calories": numbers["calories"],
            "protein": numbers["proteins"],
            "fats": numbers["fats"],
            "carbs": numbers["carbohydrates"],
        },
        source_meal_id=dish.meal_id,
        usual_grams=None if portion.assumed else portion.grams,
        commit=commit,
    )


def star(
    db,
    user: User,
    dish: PersonalFood,
    *,
    group_id: Optional[str] = None,
) -> Optional[RecordEvent]:
    """A ⭐ at the portion the dish is usually eaten at, as the ⭐ tap in 🍲 Блюда makes
    it. None when it has one already. Its event lets a group undo take it back."""
    db.refresh(dish)
    if not _is_live(dish) or dish.user_id != user.id:
        raise Refused(dish.canonical_name)
    link_stars(db, user.id)
    if crud_favorite_meal.active_of_dish(db, dish.id) is not None:
        return None
    fav = create_star(db, user.id, dish, commit=False)
    return crud_record_event.add(
        db,
        user_id=user.id,
        kind="food_star",
        food_id=dish.id,
        before={"favourite": None},
        after={"favourite": fav.id},
        group_id=group_id,
    )


def _take_star_back(
    db, user: User, dish: PersonalFood, event: RecordEvent, group_id: Optional[str]
) -> RecordEvent:
    """A ⭐ taken off since stays off: the undo only says so."""
    fav_id = (event.after or {}).get("favourite")
    fav = db.get(FavoriteMeal, fav_id or 0)
    if fav is not None and fav.removed_at is None:
        fav.removed_at = datetime.now(UTC)
    return crud_record_event.add(
        db,
        user_id=user.id,
        kind="food_star",
        food_id=dish.id,
        before=event.after,
        after={"favourite": None},
        undoes_event_id=event.id,
        group_id=group_id,
    )


def unstar(
    db,
    user: User,
    dish: PersonalFood,
    *,
    now: Optional[datetime] = None,
    group_id: Optional[str] = None,
) -> Optional[RecordEvent]:
    """The dish's ⭐ taken off. None when it has none."""
    db.refresh(dish)
    if dish.user_id != user.id or dish.merged_into_id is not None:
        raise Refused(dish.canonical_name)
    link_stars(db, user.id)
    fav = crud_favorite_meal.active_of_dish(db, dish.id)
    if fav is None:
        return None
    fav.removed_at = now or datetime.now(UTC)
    return crud_record_event.add(
        db,
        user_id=user.id,
        kind="food_unstar",
        food_id=dish.id,
        before={"favourite": fav.id},
        after={"favourite": None},
        group_id=group_id,
    )


def _star_back(
    db, user: User, dish: PersonalFood, event: RecordEvent, group_id: Optional[str]
) -> RecordEvent:
    """The ⭐ comes back unless the dish got another one since, or is gone."""
    if not _is_live(dish):
        raise Refused(dish.canonical_name)
    fav = db.get(FavoriteMeal, (event.before or {}).get("favourite") or 0)
    if fav is not None and crud_favorite_meal.active_of_dish(db, dish.id) is None:
        fav.removed_at = None
    return crud_record_event.add(
        db,
        user_id=user.id,
        kind="food_unstar",
        food_id=dish.id,
        before=event.after,
        after=event.before,
        undoes_event_id=event.id,
        group_id=group_id,
    )


# --- rename ---------------------------------------------------------------------


def names_state(dish: PersonalFood) -> dict:
    return {"name": dish.canonical_name, "aliases": list(dish.aliases or [])}


def rename(
    db,
    user: User,
    dish: PersonalFood,
    new_name: str,
    *,
    group_id: Optional[str] = None,
) -> Optional[RecordEvent]:
    """The old name stays a name that finds the dish. None when the name is the dish's
    own already; ``NameTaken`` when another row holds it."""
    db.refresh(dish)
    if not _is_live(dish) or dish.user_id != user.id:
        raise Refused(dish.canonical_name)
    name = meal_items._clean_name(new_name)
    if not name:
        raise Refused(dish.canonical_name, reason="empty")
    if name == dish.canonical_name:
        return None
    holder = crud_personal_food.holder_of_name(db, user.id, name, without=dish.id)
    if holder is not None:
        raise NameTaken(dish.canonical_name, crud_personal_food.live_of(db, holder))
    before = names_state(dish)
    crud_personal_food.rename(db, dish, name)
    return crud_record_event.add(
        db,
        user_id=user.id,
        kind="rename",
        food_id=dish.id,
        before=before,
        after=names_state(dish),
        group_id=group_id,
    )


def undo_rename(
    db,
    user: User,
    dish: PersonalFood,
    event: RecordEvent,
    *,
    group_id: Optional[str] = None,
) -> RecordEvent:
    """Refused when the names changed since, or another row took the old name."""
    before, after = event.before or {}, event.after or {}
    holder = crud_personal_food.holder_of_name(
        db, user.id, before.get("name") or "", without=dish.id
    )
    if names_state(dish) != after or holder is not None:
        raise Refused(dish.canonical_name)
    crud_personal_food.forget_suggestion(dish, dish.canonical_name, before["name"])
    dish.canonical_name = before["name"]
    dish.aliases = list(before.get("aliases") or [])
    return crud_record_event.add(
        db,
        user_id=user.id,
        kind="rename",
        food_id=dish.id,
        before=after,
        after=before,
        undoes_event_id=event.id,
        group_id=group_id,
    )


# --- out of the lists -------------------------------------------------------------


def hide(
    db,
    user: User,
    dish: PersonalFood,
    *,
    now: Optional[datetime] = None,
    group_id: Optional[str] = None,
    undoes: Optional[int] = None,
) -> Optional[RecordEvent]:
    """Out of 🍲 Блюда and the pickers, its ⭐ off; the past stays. None when hidden
    already."""
    now = now or datetime.now(UTC)
    db.refresh(dish)
    if dish.merged_into_id is not None or dish.user_id != user.id:
        raise Refused(dish.canonical_name)
    if dish.hidden_at is not None:
        return None
    link_stars(db, user.id)
    stars = _active_stars(db, dish)
    for fav in stars:
        fav.removed_at = now
    dish.hidden_at = now
    ids = [fav.id for fav in stars]
    return crud_record_event.add(
        db,
        user_id=user.id,
        kind="food_hide",
        food_id=dish.id,
        before={"hidden_at": None, "favourites": ids},
        after={"hidden_at": _stamp(now), "favourites": ids},
        undoes_event_id=undoes,
        group_id=group_id,
    )


def unhide(
    db,
    user: User,
    dish: PersonalFood,
    event: RecordEvent,
    *,
    group_id: Optional[str] = None,
    mark: bool = False,
) -> Optional[RecordEvent]:
    """The dish and its ⭐ back. A dish brought back by eating it has nothing to undo:
    None, or with ``mark`` an event that says the hide is taken back. Refused when it
    was hidden again since."""
    after = event.after or {}
    ids = after.get("favourites") or []
    if dish.hidden_at is None:
        if not mark:
            return None
    elif abs(_stamp(dish.hidden_at) - after["hidden_at"]) > 0.001:
        raise Refused(dish.canonical_name)
    else:
        dish.hidden_at = None
        if crud_favorite_meal.active_of_dish(db, dish.id) is None:
            for fav_id in ids:  # one made since is the dish's one star
                fav = db.get(FavoriteMeal, fav_id)
                if fav is not None:
                    fav.removed_at = None
    return crud_record_event.add(
        db,
        user_id=user.id,
        kind="food_hide",
        food_id=dish.id,
        before=after,
        after={"hidden_at": None, "favourites": ids},
        undoes_event_id=event.id,
        group_id=group_id,
    )


# --- undo -----------------------------------------------------------------------


def is_undone(db, event: RecordEvent) -> bool:
    return (
        db.scalars(
            select(RecordEvent.id).where(RecordEvent.undoes_event_id == event.id)
        ).first()
        is not None
    )


def undo(
    db, user: User, event: RecordEvent, *, group_id: Optional[str] = None
) -> RecordEvent:
    """The undo of one change of ``GROUP_KINDS``; ``Refused`` when the record moved
    on."""
    if event.kind not in GROUP_KINDS or event.user_id != user.id:
        raise ValueError(f"not undoable here: {event.kind}")
    dish = db.get(PersonalFood, event.food_id or 0)
    if dish is None:
        raise Refused("?")
    if event.kind == "food_star":
        return _take_star_back(db, user, dish, event, group_id)
    if event.kind == "food_unstar":
        return _star_back(db, user, dish, event, group_id)
    if event.kind == "rename":
        return undo_rename(db, user, dish, event, group_id=group_id)
    if event.kind == "food_hide":
        return unhide(db, user, dish, event, group_id=group_id, mark=True)
    if (event.after or {}).get("merged_into") is not None:
        return unmerge(db, user, dish, event, group_id=group_id)
    return merge_again(db, user, dish, event, group_id=group_id)[0]


def group_changes(db, group_id: str) -> bool:
    """Whether any change was written under the group."""
    return (
        db.scalars(
            select(RecordEvent.id).where(RecordEvent.group_id == group_id)
        ).first()
        is not None
    )


def undo_group(db, user: User, group_id: str) -> GroupUndo:
    """Every change of the group not taken back yet, newest first, each through its own
    undo: a ⭐ on a merged dish goes before the merge. The undos share one new group."""
    events = list(
        db.scalars(
            select(RecordEvent)
            .where(
                RecordEvent.user_id == user.id,
                RecordEvent.group_id == group_id,
                RecordEvent.undoes_event_id.is_(None),
            )
            .order_by(RecordEvent.id.desc())
        )
    )
    result = GroupUndo()
    undo_group_id = new_group()
    pending = [event for event in events if not is_undone(db, event)]
    if not pending:
        result.already = True
        return result
    for event in pending:
        try:
            undo(db, user, event, group_id=undo_group_id)
        except Refused as refused:
            result.refused_names.append(refused.name)
        else:
            result.undone += 1
    return result
