"""The re-tag run (G5-R17): price first, a hard stop, never over a user's groups."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.meal import Meal
from app.models.user import User
from app.services import food_groups_retag as retag
from app.services import usage_meter

_REPLY = json.dumps({"food_items": [{"name": "тофу", "groups": ["legumes", "anchor"]}]})


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with sessionmaker(bind=engine)() as session:
        session.add(User(id=1, telegram_id=42))
        session.commit()
        yield session
    engine.dispose()


def _meal(db, **kw):
    meal = Meal(user_id=1, description="тофу", nutrients={"foods": ["тофу"]}, **kw)
    db.add(meal)
    db.commit()
    return meal


def _service(usd=0.001, reply=_REPLY):
    calls = []

    async def tag(foods):
        calls.append(foods)
        meter = usage_meter._current.get()
        if meter is not None:  # only the pricing call runs under a meter
            meter.usd += usd
        return reply() if callable(reply) else reply

    service = AsyncMock()
    service.tag_food_groups = tag
    service.calls = calls
    return service


def _run(db, service, **kw):
    return asyncio.run(retag.run(db, service, **kw))


def test_without_apply_it_prices_one_call_and_writes_nothing(db):
    meals = [_meal(db) for _ in range(5)]
    service = _service()

    report = _run(db, service, apply=False)

    assert len(service.calls) == 1
    assert report.meals == 5 and report.total_usd == pytest.approx(0.005)
    assert all(meal.ai_analysis is None for meal in meals)


def test_over_the_limit_it_stops_after_the_price_with_the_sum(db):
    meals = [_meal(db) for _ in range(5)]
    service = _service(usd=0.05)

    report = _run(db, service, apply=True)

    assert len(service.calls) == 1 and report.tagged == 0
    assert report.stopped == "over the limit"
    assert report.total_usd == pytest.approx(0.25)
    assert all(meal.ai_analysis is None for meal in meals)


def test_an_unknown_price_is_a_stop_not_a_go(db):
    _meal(db)
    service = _service()

    async def unpriced(foods):
        usage_meter._current.get().unpriced.append("some-model")
        return _REPLY

    service.tag_food_groups = unpriced
    report = _run(db, service, apply=True)

    assert report.stopped == "no known price for the model" and report.tagged == 0


def test_apply_tags_both_stores_and_keeps_the_rest_of_the_analysis(db):
    meal = _meal(db, ai_analysis={"calories": 100})

    report = _run(db, _service(), apply=True)

    db.refresh(meal)
    assert report.tagged == 1
    assert meal.ai_analysis["calories"] == 100
    assert meal.ai_analysis["food_groups"] == ["legumes", "anchor"]
    assert meal.nutrients["food_groups"] == ["legumes", "anchor"]
    assert meal.nutrients["foods"] == ["тофу"]


def test_a_meal_with_groups_is_left_alone_even_an_empty_set(db):
    _meal(db, ai_analysis={"food_groups": [], "food_groups_by_user": True})
    service = _service()

    report = _run(db, service, apply=True)

    assert report.meals == 0 and service.calls == []


def test_one_bad_reply_does_not_lose_the_rest_and_a_second_run_finishes(db):
    meals = [_meal(db) for _ in range(3)]
    replies = iter([_REPLY, "not json", _REPLY])

    report = _run(db, _service(reply=lambda: next(replies)), apply=True)

    assert (report.tagged, report.failed) == (2, 1)
    assert meals[1].ai_analysis is None
    report = _run(db, _service(), apply=True)
    assert report.tagged == 1 and report.meals == 1
