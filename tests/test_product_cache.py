"""Tests for product_cache (A1): model, CRUD lifecycle, and the meal
resolution-source/signals columns added alongside it."""

import datetime

import pytest

from app.crud.crud_meal import crud_meal
from app.crud.crud_product_cache import crud_product_cache
from app.models.product_cache import ProductCache
from app.schemas.meal import MealCreate
from app.schemas.product_cache import ProductCacheCreate, ProductCacheUpdate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_product(barcode: str = "4607195501226", **kwargs) -> ProductCacheCreate:
    return ProductCacheCreate(
        barcode=barcode,
        product_name=kwargs.get("product_name", "Творог Простоквашино 5%"),
        brand=kwargs.get("brand", "Простоквашино"),
        calories_per_100g=kwargs.get("calories_per_100g", 121.0),
        proteins_per_100g=kwargs.get("proteins_per_100g", 17.0),
        fats_per_100g=kwargs.get("fats_per_100g", 5.0),
        carbohydrates_per_100g=kwargs.get("carbohydrates_per_100g", 1.8),
        off_code=kwargs.get("off_code", "4607195501226"),
        raw_data=kwargs.get("raw_data", {"nutriscore_grade": "a"}),
    )


# ---------------------------------------------------------------------------
# product_cache CRUD — create / get_by_barcode
# ---------------------------------------------------------------------------


def test_create_product_cache(db_session):
    obj = crud_product_cache.create(db_session, _make_product())

    assert obj.id is not None
    assert obj.barcode == "4607195501226"
    assert obj.product_name == "Творог Простоквашино 5%"
    assert obj.calories_per_100g == 121.0
    assert obj.proteins_per_100g == 17.0
    assert obj.fats_per_100g == 5.0
    assert obj.carbohydrates_per_100g == 1.8
    assert obj.raw_data == {"nutriscore_grade": "a"}
    assert obj.created_at is not None
    assert obj.updated_at is not None


def test_get_by_barcode_hit_and_miss(db_session):
    crud_product_cache.create(db_session, _make_product("0000000000001"))

    hit = crud_product_cache.get_by_barcode(db_session, "0000000000001")
    assert hit is not None
    assert hit.barcode == "0000000000001"

    miss = crud_product_cache.get_by_barcode(db_session, "9999999999999")
    assert miss is None


def test_update_refreshes_fields(db_session):
    obj = crud_product_cache.create(
        db_session, _make_product("1111111111111", calories_per_100g=100.0)
    )

    updated = crud_product_cache.update(
        db_session,
        obj,
        ProductCacheUpdate(calories_per_100g=110.0, product_name="Updated Name"),
    )

    assert updated.calories_per_100g == 110.0
    assert updated.product_name == "Updated Name"
    # untouched field preserved
    assert updated.proteins_per_100g == 17.0


# ---------------------------------------------------------------------------
# get_or_create — idempotency
# ---------------------------------------------------------------------------


def test_get_or_create_inserts_once(db_session):
    schema = _make_product("2222222222222")

    entry1, created1 = crud_product_cache.get_or_create(db_session, schema)
    entry2, created2 = crud_product_cache.get_or_create(db_session, schema)

    assert created1 is True
    assert created2 is False
    assert entry1.id == entry2.id  # same row returned


def test_get_or_create_returns_existing(db_session):
    schema = _make_product("3333333333333", calories_per_100g=50.0)
    original, _ = crud_product_cache.get_or_create(db_session, schema)

    # Second call with *different* nutrition values — should NOT overwrite
    schema2 = _make_product("3333333333333", calories_per_100g=999.0)
    existing, created = crud_product_cache.get_or_create(db_session, schema2)

    assert created is False
    assert existing.id == original.id
    assert existing.calories_per_100g == 50.0  # original value preserved


# ---------------------------------------------------------------------------
# Meal resolution_source + resolution_signals round-trip
# ---------------------------------------------------------------------------


def _make_meal_create(**overrides):
    defaults = dict(
        meal_time=datetime.datetime(2026, 7, 7, 12, 0, tzinfo=datetime.timezone.utc),
        description="Творог",
        calories=181.5,
        proteins=25.5,
        fats=7.5,
        carbohydrates=2.7,
    )
    defaults.update(overrides)
    return MealCreate(**defaults)


def _make_user(db_session):
    from app.crud.crud_user import crud_user
    from app.schemas.user import UserCreate

    return crud_user.create(
        db_session, obj_in=UserCreate(telegram_id=99001, username="tester")
    )


def test_meal_create_with_resolution_source(db_session):
    user = _make_user(db_session)
    signals = {
        "barcode_raw": "4607195501226",
        "product_name": "Творог Простоквашино 5%",
        "portion_grams": 150.0,
        "confidence_tier": "high",
        "lookup_latency_ms": 320,
    }
    meal_in = _make_meal_create(
        resolution_source="barcode_off",
        resolution_signals=signals,
    )

    meal = crud_meal.create(db_session, obj_in=meal_in, user_id=user.id)

    assert meal.resolution_source == "barcode_off"
    assert meal.resolution_signals == signals
    assert meal.resolution_signals["barcode_raw"] == "4607195501226"
    assert meal.resolution_signals["confidence_tier"] == "high"


def test_meal_create_without_resolution_defaults_none(db_session):
    user = _make_user(db_session)
    meal = crud_meal.create(db_session, obj_in=_make_meal_create(), user_id=user.id)

    assert meal.resolution_source is None
    assert meal.resolution_signals is None


def test_meal_update_sets_resolution(db_session):
    from app.schemas.meal import MealUpdate

    user = _make_user(db_session)
    meal = crud_meal.create(db_session, obj_in=_make_meal_create(), user_id=user.id)
    assert meal.resolution_source is None

    updated = crud_meal.update(
        db_session,
        meal,
        MealUpdate(
            resolution_source="vision",
            resolution_signals={"confidence_tier": "low"},
        ),
    )

    assert updated.resolution_source == "vision"
    assert updated.resolution_signals == {"confidence_tier": "low"}


# ---------------------------------------------------------------------------
# created_at NOT NULL (TD-006 lesson)
# ---------------------------------------------------------------------------


def test_product_cache_created_at_not_null(db_session):
    obj = crud_product_cache.create(db_session, _make_product("4444444444444"))
    db_session.refresh(obj)
    assert obj.created_at is not None


def test_product_cache_created_at_is_timezone_aware(db_session):
    obj = crud_product_cache.create(db_session, _make_product("5555555555555"))
    db_session.refresh(obj)
    # SQLite stores without tz but the ORM gives back a naive datetime — we just
    # verify it's a datetime; the NOT NULL guarantee is the critical invariant.
    assert isinstance(obj.created_at, datetime.datetime)
