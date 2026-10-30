"""Tag meals logged before food groups existed: one cheap call per meal (G5-R17).

The price is measured, not assumed: one real call on the first meal, times the
number of meals. Above the limit (or with no known price) nothing further is
called and nothing is written; the caller gets the sum to ask the owner about.
A meal that already has groups is never touched, whoever set them.
"""

import asyncio
import logging
from dataclasses import dataclass, field

from sqlalchemy import select

from app.models.meal import Meal
from app.services import food_groups, usage_meter
from app.services.product_lookup_service import parse_food_items

logger = logging.getLogger(__name__)

LIMIT_USD = 0.20


@dataclass
class Report:
    meals: int = 0
    per_call_usd: float | None = None
    total_usd: float | None = None
    tagged: int = 0
    failed: int = 0
    stopped: str | None = None
    unpriced: list = field(default_factory=list)


def _foods(meal: Meal) -> list:
    nutrients = meal.nutrients if isinstance(meal.nutrients, dict) else {}
    foods = [str(food) for food in nutrients.get("foods") or []]
    return foods or ([meal.description] if meal.description else [])


def pending(db) -> list:
    """Meals without confirmed groups that have something to tag."""
    meals = db.execute(select(Meal).order_by(Meal.id)).scalars().all()
    return [
        meal
        for meal in meals
        if "food_groups" not in (meal.ai_analysis or {}) and _foods(meal)
    ]


def _write(meal: Meal, items: list) -> None:
    tags = {
        "food_items": items,
        "food_groups": food_groups.from_items(items),
        "food_groups_by_user": False,
    }
    # New dicts: a plain JSON column does not notice a change in place.
    meal.ai_analysis = {**(meal.ai_analysis or {}), **tags}
    meal.nutrients = {**(meal.nutrients or {}), **tags}


async def _tag(service, meal: Meal) -> list:
    return parse_food_items(await service.tag_food_groups(_foods(meal)))


async def run(db, service, *, apply: bool, limit_usd: float = LIMIT_USD) -> Report:
    """Price on the first meal; with ``apply`` and under the limit, tag them all."""
    meals = pending(db)
    report = Report(meals=len(meals))
    if not meals:
        return report

    with usage_meter.metering() as meter:
        first_items = await _tag(service, meals[0])
    report.unpriced = list(meter.unpriced)
    if meter.unpriced:
        report.stopped = "no known price for the model"
        return report
    report.per_call_usd = meter.usd
    report.total_usd = meter.usd * len(meals)
    if report.total_usd >= limit_usd:
        report.stopped = "over the limit"
        return report
    if not apply:
        return report

    _write(meals[0], first_items)
    db.commit()
    report.tagged = 1
    for meal in meals[1:]:
        try:
            _write(meal, await _tag(service, meal))
            db.commit()
            report.tagged += 1
        except Exception:
            # One meal's bad reply must not lose the rest; it stays untagged and
            # a second run picks it up.
            db.rollback()
            logger.exception("retag: meal %s failed", meal.id)
            report.failed += 1
    return report


def run_sync(db, service, *, apply: bool, limit_usd: float = LIMIT_USD) -> Report:
    return asyncio.run(run(db, service, apply=apply, limit_usd=limit_usd))
