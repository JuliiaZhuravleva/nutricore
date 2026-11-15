"""🍲 Порядок в блюдах: what a batch does to the dishes (service logic, no Telegram).

``prepare`` makes a batch, ``roll_back`` takes an unsent one apart, ``apply`` and
``undo`` do what the person's ✅ and ↩️ mean. They take a mask (bit ``idx`` set: that
row ticked) and return plain data, so the message screen and the widget can call the
same code. Nothing here calls a model or builds a view.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from app.crud.crud_dish_tidy import OPEN, crud_dish_tidy
from app.models.dish_tidy import DishTidyBatch, DishTidyItem
from app.models.personal_food import PersonalFood
from app.models.record_event import RecordEvent
from app.models.user import User
from app.services import dish_ops, dish_tidy

logger = logging.getLogger(__name__)

SWITCH = "dish_tidy"  # User.settings: False, no Sunday batch; on unless turned off
OPEN_DAYS = 7  # the proposals hold this long

_ORDER = {dish_tidy.SAME: 0, dish_tidy.MERGE: 1, dish_tidy.STAR: 2}


@dataclass
class ApplyResult:
    already: bool = False  # the batch was not open any more: nothing was done
    applied: list = field(default_factory=list)  # item idx done now
    declined: list = field(default_factory=list)  # item idx left unticked
    failed: list = field(default_factory=list)  # item idx whose dishes moved on
    changed: bool = False  # something was merged, starred or taken apart
    kept: bool = False  # some row of the batch stands applied


@dataclass
class UndoResult:
    already: bool = False  # the batch was taken back (or closed) before
    undone: int = 0
    not_back: list = field(default_factory=list)  # names that could not be taken back


def tidy_on(user: Optional[User]) -> bool:
    return (user.settings or {}).get(SWITCH) is not False if user else True


def ticked(mask: int, item: DishTidyItem) -> bool:
    return bool(mask >> item.idx & 1)


def prepare(db, user: User, batch: DishTidyBatch, now: datetime) -> bool:
    """The finder's rows into the batch, the near-identical merged under the batch's
    group; one commit. False when there is nothing to show (the batch is ``empty``).

    The finder reads before the lock: a failed embeddings query rolls back, which
    would let the lock go. ``merge`` re-reads each dish under the lock and refuses
    one that moved on meanwhile."""
    found = dish_tidy.find(db, user, now)
    dish_ops.lock_user(db, user)
    dish_ops.link_stars(db, user.id)  # an old ⭐ row moves with its merged dish
    idx = 0
    for row in found.rows:
        event_id, state = None, "proposed"
        if row.kind == dish_tidy.SAME:
            source = db.get(PersonalFood, row.source_id)
            target = db.get(PersonalFood, row.target_id)
            try:
                event = dish_ops.merge(
                    db, user, source, target, now=now, group_id=batch.group_id
                )
            except dish_ops.Refused:
                continue
            event_id, state = event.id, "applied"
        db.add(
            DishTidyItem(
                batch_id=batch.id,
                idx=idx,
                kind=row.kind,
                source_id=row.source_id,
                target_id=row.target_id,
                key=row.key,
                label=list(row.names),
                fingerprint=row.fingerprint,
                ticked=row.ticked,
                days=row.days,
                event_id=event_id,
                state=state,
            )
        )
        idx += 1
    batch.state = "ready" if idx else "empty"
    batch.expires_at = now + timedelta(days=OPEN_DAYS)
    db.commit()
    return bool(idx)


def roll_back(db, user: User, batch: DishTidyBatch, state: str) -> None:
    """The message never reached the person: the merges come apart again, and its rows
    count as never shown (not as declines). One commit.

    A merge that would not come apart (its dishes changed since) stays ``applied``,
    and the batch stays open as ``maybe``, blocked or not (a person may unblock):
    🍲 Блюда → «🍲 Порядок» shows what stayed once, with «↩️ Отменить»
    (``left_over``), so no merge is left where nobody can see it."""
    dish_ops.lock_user(db, user)
    result = dish_ops.undo_group(db, user, batch.group_id)
    for item in crud_dish_tidy.items(db, batch.id):
        event = db.get(RecordEvent, item.event_id) if item.event_id else None
        if event is None or dish_ops.is_undone(db, event):
            item.state = "rolled_back"
    if result.refused_names:
        logger.error(
            "dish tidy: batch %s not fully rolled back: %s left",
            batch.id,
            len(result.refused_names),
        )
        state = "maybe"
    batch.state = state
    db.commit()


def left_over(db, batch: DishTidyBatch) -> bool:
    """An open batch whose message never arrived and whose merges would not all come
    apart: what stayed merged waits to be shown, and never expires unseen."""
    return batch.state in OPEN and any(
        item.state == "rolled_back" for item in crud_dish_tidy.items(db, batch.id)
    )


def _dish(row: PersonalFood) -> dish_tidy.Dish:
    return dish_tidy.Dish(
        id=row.id, name=row.canonical_name, kcal=dish_tidy._float(row.per_100g_calories)
    )


def _still_as_shown(db, item: DishTidyItem) -> bool:
    """The row's dishes are still live and named and counted as in the message."""
    source = db.get(PersonalFood, item.source_id or 0)
    if source is None or source.hidden_at is not None or source.merged_into_id:
        return False
    if item.kind == dish_tidy.STAR:
        return dish_tidy.star_fingerprint(_dish(source)) == item.fingerprint
    target = db.get(PersonalFood, item.target_id or 0)
    if target is None or target.hidden_at is not None or target.merged_into_id:
        return False
    return dish_tidy.pair_fingerprint(_dish(source), _dish(target)) == item.fingerprint


def apply(
    db, user: User, batch: DishTidyBatch, mask: int, now: datetime
) -> ApplyResult:
    """The ticked rows become the dishes; an unticked row is declined (an unticked
    near-identical pair comes apart again). One commit. ``already`` when the batch is
    not open under the lock: another tap got here first."""
    dish_ops.lock_user(db, user)
    db.refresh(batch)
    if batch.state not in OPEN:
        return ApplyResult(already=True)
    dish_ops.link_stars(db, user.id)
    result = ApplyResult()
    items = crud_dish_tidy.items(db, batch.id)
    for item in sorted(items, key=lambda i: (_ORDER[i.kind], i.idx)):
        is_ticked = ticked(mask, item)
        item.decided_at = now
        if item.kind == dish_tidy.SAME:
            if is_ticked or item.state != "applied":
                continue
            event = db.get(RecordEvent, item.event_id or 0)
            try:
                if event is None or dish_ops.is_undone(db, event):
                    raise dish_ops.Refused((item.label or ["?"])[0])
                dish_ops.undo(db, user, event, group_id=batch.group_id)
            except dish_ops.Refused:
                item.state = "stale"
                result.failed.append(item.idx)
                continue
            item.state = "declined"
            result.declined.append(item.idx)
            result.changed = True
            continue
        if not is_ticked:
            item.state = "declined"
            result.declined.append(item.idx)
            continue
        if not _still_as_shown(db, item):
            item.state = "stale"
            result.failed.append(item.idx)
            continue
        source = db.get(PersonalFood, item.source_id)
        try:
            if item.kind == dish_tidy.MERGE:
                target = db.get(PersonalFood, item.target_id)
                event = dish_ops.merge(
                    db, user, source, target, now=now, group_id=batch.group_id
                )
            else:
                event = dish_ops.star(db, user, source, group_id=batch.group_id)
        except dish_ops.Refused:
            item.state = "stale"
            result.failed.append(item.idx)
            continue
        item.state = "applied"
        item.event_id = event.id if event is not None else None
        result.applied.append(item.idx)
        result.changed = True
    batch.state = "applied"
    db.commit()
    result.kept = any(item.state == "applied" for item in items)
    return result


def undo(db, user: User, batch: DishTidyBatch, now: datetime) -> UndoResult:
    """Every change of the batch taken back, as far as the dishes allow. One commit.
    ``already`` (and nothing written) when the batch was closed before."""
    dish_ops.lock_user(db, user)
    db.refresh(batch)
    if batch.state in ("undone", "blocked", "failed"):
        return UndoResult(already=True)
    items = [
        item
        for item in crud_dish_tidy.items(db, batch.id)
        if item.state == "applied" and item.event_id is not None
    ]
    result = dish_ops.undo_group(db, user, batch.group_id)
    if result.already:
        db.rollback()
        return UndoResult(already=True)
    for item in items:
        event = db.get(RecordEvent, item.event_id)
        if event is not None and dish_ops.is_undone(db, event):
            item.state = "undone"
            item.decided_at = now
    batch.state = "undone"
    db.commit()
    return UndoResult(undone=result.undone, not_back=list(result.refused_names))
