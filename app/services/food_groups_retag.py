"""Tag meals logged before food groups existed: one cheap call per item (G5-R17).

The price is measured, not assumed: one real call on the first item, times the
number of items. Above the limit (or with no known price) nothing further is
called and nothing is written; the caller gets the sum to ask the owner about.
The run meters every call and stops if what it spent reaches the limit.
Favorites are tagged too, or every meal logged from an old one stays untagged.
A row that already has groups is never touched, whoever set them. A reply takes
seconds: a row that moved meanwhile is left for the next run, or it would write foods
that are no longer there over the person's change (3.1).
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select

from app.models.favorite_meal import FavoriteMeal
from app.models.meal import Meal
from app.services import food_groups, usage_meter
from app.services.product_lookup_service import parse_food_items

logger = logging.getLogger(__name__)

LIMIT_USD = 0.20


@dataclass
class Report:
    meals: int = 0  # meals and favorites waiting for tags
    per_call_usd: float | None = None
    estimated_usd: float | None = None
    spent_usd: float = 0.0
    tagged: int = 0
    failed: int = 0
    skipped: int = 0  # changed while the reply was awaited; a later run picks it up
    stopped: str | None = None
    unpriced: list = field(default_factory=list)


def _foods(row) -> list:
    nutrients = row.nutrients if isinstance(row.nutrients, dict) else {}
    foods = [str(food) for food in nutrients.get("foods") or [] if str(food).strip()]
    fallback = row.name if isinstance(row, FavoriteMeal) else row.description
    return foods or ([fallback] if fallback and fallback.strip() else [])


def pending(db) -> list:
    """Live favorites and meals without confirmed groups that have something to tag.
    A meal with nothing read names no food, and a call about it pays for nothing."""
    favorites = db.execute(
        select(FavoriteMeal)
        .where(FavoriteMeal.removed_at.is_(None))
        .order_by(FavoriteMeal.id)
    ).scalars()
    meals = db.execute(
        select(Meal).where(Meal.deleted_at.is_(None)).order_by(Meal.id)
    ).scalars()
    rows = [
        favorite
        for favorite in favorites
        if "food_groups" not in (favorite.nutrients or {}) and _foods(favorite)
    ]
    rows += [
        meal
        for meal in meals
        if "food_groups" not in (meal.ai_analysis or {}) and _foods(meal)
    ]
    return rows


def _rev(row):
    return row.rev if isinstance(row, Meal) else None


def _moved(db, row, rev) -> bool:
    """Whether the row is still what the reply answered about, from the database."""
    db.refresh(row)
    if isinstance(row, FavoriteMeal):
        return row.removed_at is not None or "food_groups" in (row.nutrients or {})
    return (
        row.rev != rev
        or row.deleted_at is not None
        or "food_groups" in (row.ai_analysis or {})
    )


def _write(row, items: list) -> None:
    tags = {
        "food_items": items,
        "food_groups": food_groups.from_items(items),
        "food_groups_by_user": False,
    }
    # New dicts: a plain JSON column does not notice a change in place.
    row.nutrients = {**(row.nutrients or {}), **tags}
    if isinstance(row, Meal):
        row.ai_analysis = {**(row.ai_analysis or {}), **tags}
        # A new rev and time, as any change: a pull by ``updated_since`` wants the tags.
        row.rev = (row.rev or 0) + 1
        row.updated_at = datetime.now(UTC)


async def _tag(service, row) -> list:
    items = parse_food_items(await service.tag_food_groups(_foods(row)))
    if not items:
        # Stored as «no groups», it would never be asked about again.
        raise ValueError("food_items reply named no foods")
    return items


async def run(db, service, *, apply: bool, limit_usd: float = LIMIT_USD) -> Report:
    """Price on the first row; with ``apply`` and under the limit, tag them all."""
    rows = pending(db)
    report = Report(meals=len(rows))
    if not rows:
        return report
    # Read now: after a commit, reading the id of a row deleted meanwhile raises.
    labels = [f"{type(row).__name__} {row.id}" for row in rows]

    with usage_meter.metering() as meter:
        first_items = None
        first_rev = _rev(rows[0])
        try:
            first_items = await _tag(service, rows[0])
        except Exception:
            logger.exception("retag: %s failed", labels[0])
            report.failed = 1
        report.spent_usd = meter.usd
        report.unpriced = list(meter.unpriced)
        if meter.unpriced:
            report.stopped = "no known price for the model"
            return report
        if not meter.usd:
            report.stopped = "the first call gave no price"
            return report
        report.per_call_usd = meter.usd
        report.estimated_usd = meter.usd * len(rows)
        if report.estimated_usd >= limit_usd:
            report.stopped = "over the limit"
            return report
        if not apply:
            return report

        for index, (label, row) in enumerate(zip(labels, rows)):
            if index == 0 and first_items is None:
                continue
            if meter.usd >= limit_usd:
                report.stopped = "spent the limit"
                break
            try:
                rev = first_rev if index == 0 else _rev(row)
                items = first_items if index == 0 else await _tag(service, row)
                if _moved(db, row, rev):
                    db.rollback()
                    report.skipped += 1
                    continue
                _write(row, items)
                db.commit()
                report.tagged += 1
            except Exception:
                # One row's bad reply must not lose the rest; it stays untagged
                # and a second run picks it up.
                db.rollback()
                logger.exception("retag: %s failed", label)
                report.failed += 1
        report.spent_usd = meter.usd
        report.unpriced = list(meter.unpriced)
    return report


def run_sync(db, service, *, apply: bool, limit_usd: float = LIMIT_USD) -> Report:
    return asyncio.run(run(db, service, apply=apply, limit_usd=limit_usd))
