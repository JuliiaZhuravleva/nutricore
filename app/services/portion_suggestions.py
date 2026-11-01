"""A guess at a portion, for a dish with numbers on 100 g and no weight anywhere.

One small call per dish, asked once, behind the screen that was already sent. It can
fail in any way and the dish stays at 100 g: nobody waits for it and nobody hears of it.
Everything it answers is checked, and only a number and a unit are kept, never its text.

``run`` does the dishes that have no guess yet: it prices them with no call, asks only on
``apply``, stops at a count and at a sum, and writes each guess on the row it was asked
about, whoever's that is. Logs ids and sums only, never a name: a dish is a person's.
"""

import asyncio
import logging
import math
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select

from app.models.personal_food import PersonalFood
from app.services import meal_items, usage_meter
from app.services.meal_card import GRAMS_MAX, GRAMS_MIN
from app.services.model_registry import loads_model_json

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 20
LIMIT_USD = 0.20
LIMIT_DISHES = 500
UNITS = ("g", "ml")
# One call, on the safe side of what one was measured to use: a short system prompt, a
# name, a small JSON object.
_TOKENS_IN = 200
_TOKENS_OUT = 30


def parse(raw) -> Optional[tuple]:
    """(grams, unit) from a model's answer, or None. 1 ml counts 1 g."""
    if not isinstance(raw, str):
        return None
    try:
        answer = loads_model_json(raw)
    except ValueError:
        return None
    if not isinstance(answer, dict):
        return None
    amount, unit = answer.get("amount"), answer.get("unit")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        return None
    if not math.isfinite(amount) or not GRAMS_MIN <= amount <= GRAMS_MAX:
        return None
    if not isinstance(unit, str) or unit not in UNITS:
        return None
    return float(amount), unit


async def _guess(service, name: str) -> Optional[tuple]:
    raw = await asyncio.wait_for(service.suggest_portion(name), TIMEOUT_SECONDS)
    return parse(raw)


async def ask(session_factory, service, dish_id: int) -> bool:
    """Ask about one dish and keep a good answer. Never raises. Holds no session while
    the model thinks: a slow answer must not hold a database connection."""
    try:
        with session_factory() as db:
            dish = db.get(PersonalFood, dish_id)
            if dish is None or not meal_items.wants_suggestion(db, dish):
                return False
            name, user_id = dish.canonical_name, dish.user_id
        with usage_meter.metering() as meter:
            guess = await _guess(service, name)
        logger.info("Portion suggestion: dish %s, $%.5f", dish_id, meter.usd)
        if guess is None:
            logger.warning("Portion suggestion refused for dish %s", dish_id)
            return False
        with session_factory() as db:
            return meal_items.save_suggestion(db, dish_id, user_id, name, *guess)
    except Exception as e:
        logger.warning(
            "Portion suggestion failed for dish %s: %s", dish_id, type(e).__name__
        )
        return False


@dataclass
class Report:
    dishes: int = 0  # dishes waiting for a guess
    capped_to: int = 0  # how many of them this run would ask about
    estimated_usd: Optional[float] = None
    spent_usd: float = 0.0
    written: int = 0
    failed: int = 0
    skipped: int = 0  # changed while the answer was awaited; a later run picks it up
    stopped: Optional[str] = None
    unpriced: list = field(default_factory=list)


def pending(db) -> list:
    """The dishes that would use a guess, oldest first."""
    rows = db.scalars(
        select(PersonalFood)
        .where(
            PersonalFood.suggested_grams.is_(None),
            PersonalFood.usual_grams_by_user.is_(False),
            PersonalFood.merged_into_id.is_(None),
            PersonalFood.hidden_at.is_(None),
        )
        .order_by(PersonalFood.id)
    ).all()
    return [dish for dish in rows if meal_items.wants_suggestion(db, dish)]


def _per_call_usd(service) -> Optional[float]:
    prices = usage_meter.PRICES.get(service.model_for("text").split(":", 1)[-1])
    if prices is None:
        return None
    fresh_in, _cached_in, out = prices
    return (_TOKENS_IN * fresh_in + _TOKENS_OUT * out) / 1_000_000


async def run(
    session_factory,
    service,
    *,
    apply: bool = False,
    limit: int = LIMIT_DISHES,
    limit_usd: float = LIMIT_USD,
) -> Report:
    report = Report()
    with session_factory() as db:
        rows = pending(db)
        report.dishes = len(rows)
        wanted = [(d.id, d.user_id, d.canonical_name) for d in rows[:limit]]
    report.capped_to = len(wanted)
    per_call = _per_call_usd(service)
    if per_call is not None:
        report.estimated_usd = per_call * len(wanted)
    if not apply or not wanted:
        return report
    if report.estimated_usd is None:
        report.stopped = "no known price for this model"
        return report
    if report.estimated_usd > limit_usd:
        report.stopped = f"over the limit of ${limit_usd:.2f}"
        return report
    with usage_meter.metering() as meter:
        for dish_id, user_id, name in wanted:
            if meter.usd >= limit_usd and limit_usd > 0:
                report.stopped = f"spent the limit of ${limit_usd:.2f}"
                break
            try:
                guess = await _guess(service, name)
            except Exception as e:
                logger.warning(
                    "Portion suggestion failed for dish %s: %s",
                    dish_id,
                    type(e).__name__,
                )
                report.failed += 1
                continue
            if guess is None:
                report.failed += 1
                continue
            with session_factory() as db:
                saved = meal_items.save_suggestion(db, dish_id, user_id, name, *guess)
            report.written += saved
            report.skipped += not saved
        report.spent_usd = meter.usd
        report.unpriced = list(meter.unpriced)
    return report


def run_sync(session_factory, service, **kwargs) -> Report:
    return asyncio.run(run(session_factory, service, **kwargs))
