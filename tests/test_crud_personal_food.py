"""Unit tests for CRUDPersonalFood (B1 — personal food DB, ADR-0003).

Coverage:
  - upsert(): create new food + idempotent re-upsert (counter increment)
  - upsert(): case-insensitive dedup key (lower(canonical_name))
  - upsert(): selective field update on re-upsert (None fields not overwritten)
  - upsert(): barcode persisted on create + updated on re-upsert
  - get_by_barcode(): hit / miss / user scope isolation
  - add_embedding(): create embedding row
  - get_embeddings_for_food(): returns all embeddings for a food

find_similar() is NOT tested here — it uses the pgvector <=> operator which is
Postgres-only.  B6 tests SavedFoodRAGStrategy by mocking crud_personal_food.find_similar.
The real ANN / threshold check is a manual post-deploy verification (ADR-0003 §4d).

All tests run on the in-memory SQLite DB from conftest.py (Base.metadata.create_all).
The Vector(1536) column uses the fallback UserDefinedType which SQLite accepts as TEXT.
"""

from __future__ import annotations

import pytest

from app.crud.crud_personal_food import crud_personal_food
from app.crud.crud_user import crud_user
from app.schemas.user import UserCreate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user(db, telegram_id: int):
    """Create a minimal user for FK requirements."""
    return crud_user.create(db, obj_in=UserCreate(telegram_id=telegram_id))


def _upsert_basic(db, user_id: int, name: str = "Греческий йогурт", **kwargs):
    """Convenience wrapper around crud_personal_food.upsert()."""
    return crud_personal_food.upsert(
        db,
        user_id=user_id,
        canonical_name=name,
        per_100g_calories=kwargs.get("per_100g_calories", 97.0),
        per_100g_proteins=kwargs.get("per_100g_proteins", 9.0),
        per_100g_fats=kwargs.get("per_100g_fats", 5.0),
        per_100g_carbs=kwargs.get("per_100g_carbs", 3.8),
        resolution_source=kwargs.get("resolution_source", "vision"),
        barcode=kwargs.get("barcode"),
        brand=kwargs.get("brand"),
        meal_id=kwargs.get("meal_id"),
    )


# ---------------------------------------------------------------------------
# upsert() — create path
# ---------------------------------------------------------------------------


def test_upsert_creates_new_food(db_session):
    """upsert() inserts a new PersonalFood row when name is first seen."""
    user = _make_user(db_session, telegram_id=10_001)

    food = _upsert_basic(db_session, user.id, name="Куриная грудка")

    assert food.id is not None
    assert food.canonical_name == "Куриная грудка"
    assert food.user_id == user.id
    assert food.times_used == 1
    assert food.last_used_at is not None
    assert food.per_100g_calories is not None


def test_upsert_sets_provenance(db_session):
    """Provenance fields (resolution_source, meal_id) are persisted on create."""
    user = _make_user(db_session, telegram_id=10_002)

    food = crud_personal_food.upsert(
        db_session,
        user_id=user.id,
        canonical_name="Овсянка",
        resolution_source="barcode_off",
        meal_id=None,
    )

    assert food.resolution_source == "barcode_off"


def test_upsert_stores_barcode(db_session):
    """barcode is persisted when provided on create."""
    user = _make_user(db_session, telegram_id=10_003)

    food = _upsert_basic(db_session, user.id, name="Молоко БЗМЖ", barcode="4607195501226")

    assert food.barcode == "4607195501226"


# ---------------------------------------------------------------------------
# upsert() — idempotent re-upsert path
# ---------------------------------------------------------------------------


def test_upsert_increments_times_used(db_session):
    """Second upsert for the same food increments times_used."""
    user = _make_user(db_session, telegram_id=10_010)

    _upsert_basic(db_session, user.id, name="Творог 5%")
    food2 = _upsert_basic(db_session, user.id, name="Творог 5%")

    assert food2.times_used == 2


def test_upsert_returns_same_row_on_repeat(db_session):
    """Two upserts for the same food return the same DB row (same id)."""
    user = _make_user(db_session, telegram_id=10_011)

    food1 = _upsert_basic(db_session, user.id, name="Банан")
    food2 = _upsert_basic(db_session, user.id, name="Банан")

    assert food1.id == food2.id


def test_upsert_case_insensitive_dedup(db_session):
    """Upsert treats names as equivalent regardless of case (lower() dedup key)."""
    user = _make_user(db_session, telegram_id=10_012)

    food1 = _upsert_basic(db_session, user.id, name="Яблоко")
    # Different capitalisation → should hit the same row
    food2 = _upsert_basic(db_session, user.id, name="яблоко")

    assert food1.id == food2.id
    assert food2.times_used == 2


def test_upsert_preserves_canonical_name_on_reupsert(db_session):
    """Re-upsert does not overwrite canonical_name — the first spelling wins."""
    user = _make_user(db_session, telegram_id=10_013)

    food1 = _upsert_basic(db_session, user.id, name="Гречка")
    food2 = _upsert_basic(db_session, user.id, name="гречка")  # lowercase variant

    # canonical_name set on INSERT is preserved
    assert food2.canonical_name == food1.canonical_name


def test_upsert_updates_macros_on_reupsert(db_session):
    """Re-upsert with new macros overwrites old values (non-None fields)."""
    user = _make_user(db_session, telegram_id=10_014)

    _upsert_basic(db_session, user.id, name="Рис", per_100g_calories=350.0)
    food2 = crud_personal_food.upsert(
        db_session,
        user_id=user.id,
        canonical_name="Рис",
        per_100g_calories=360.0,  # corrected value
    )

    assert float(food2.per_100g_calories) == 360.0


def test_upsert_does_not_overwrite_with_none(db_session):
    """Re-upsert with None macro fields preserves the existing values."""
    user = _make_user(db_session, telegram_id=10_015)

    _upsert_basic(db_session, user.id, name="Гречка", per_100g_calories=343.0)
    food2 = crud_personal_food.upsert(
        db_session,
        user_id=user.id,
        canonical_name="Гречка",
        # per_100g_calories NOT passed → defaults to None
    )

    assert float(food2.per_100g_calories) == 343.0


def test_upsert_updates_barcode_on_reupsert(db_session):
    """barcode is updated on re-upsert when provided."""
    user = _make_user(db_session, telegram_id=10_016)

    _upsert_basic(db_session, user.id, name="Кефир")  # no barcode
    food2 = crud_personal_food.upsert(
        db_session,
        user_id=user.id,
        canonical_name="Кефир",
        barcode="4600000000001",
    )

    assert food2.barcode == "4600000000001"


def test_upsert_is_user_scoped(db_session):
    """Two different users can upsert the same name without collision."""
    user_a = _make_user(db_session, telegram_id=10_020)
    user_b = _make_user(db_session, telegram_id=10_021)

    food_a = _upsert_basic(db_session, user_a.id, name="Хлеб")
    food_b = _upsert_basic(db_session, user_b.id, name="Хлеб")

    assert food_a.id != food_b.id
    assert food_a.times_used == 1
    assert food_b.times_used == 1


# ---------------------------------------------------------------------------
# get_by_barcode()
# ---------------------------------------------------------------------------


def test_get_by_barcode_hit(db_session):
    """get_by_barcode returns the food when barcode matches for that user."""
    user = _make_user(db_session, telegram_id=20_001)

    _upsert_basic(db_session, user.id, name="Молоко", barcode="4607062600027")

    result = crud_personal_food.get_by_barcode(
        db_session, barcode="4607062600027", user_id=user.id
    )
    assert result is not None
    assert result.canonical_name == "Молоко"


def test_get_by_barcode_miss(db_session):
    """get_by_barcode returns None when barcode is not in personal_foods."""
    user = _make_user(db_session, telegram_id=20_002)

    result = crud_personal_food.get_by_barcode(
        db_session, barcode="0000000000000", user_id=user.id
    )
    assert result is None


def test_get_by_barcode_user_scope(db_session):
    """get_by_barcode does NOT return another user's food."""
    user_a = _make_user(db_session, telegram_id=20_003)
    user_b = _make_user(db_session, telegram_id=20_004)

    _upsert_basic(db_session, user_a.id, name="Сок", barcode="4600001111111")

    # user_b queries for the same barcode — must get None
    result = crud_personal_food.get_by_barcode(
        db_session, barcode="4600001111111", user_id=user_b.id
    )
    assert result is None


def test_get_by_barcode_none_barcode_food_not_returned(db_session):
    """A food with barcode=None is never returned by get_by_barcode."""
    user = _make_user(db_session, telegram_id=20_005)

    _upsert_basic(db_session, user.id, name="Суп", barcode=None)

    result = crud_personal_food.get_by_barcode(
        db_session, barcode="anything", user_id=user.id
    )
    assert result is None


# ---------------------------------------------------------------------------
# add_embedding() + get_embeddings_for_food()
# ---------------------------------------------------------------------------


def test_add_embedding_creates_row(db_session):
    """add_embedding inserts a PersonalFoodEmbedding row."""
    user = _make_user(db_session, telegram_id=30_001)
    food = _upsert_basic(db_session, user.id, name="Авокадо")

    # Use a tiny fake embedding (dimension doesn't matter for SQLite tests)
    fake_embedding = [0.1, 0.2, 0.3]
    emb = crud_personal_food.add_embedding(
        db_session,
        personal_food_id=food.id,
        text_embedded="Авокадо",
        embedding=fake_embedding,
    )

    assert emb.id is not None
    assert emb.personal_food_id == food.id
    assert emb.text_embedded == "Авокадо"


def test_get_embeddings_for_food_empty(db_session):
    """get_embeddings_for_food returns [] when no embeddings exist yet."""
    user = _make_user(db_session, telegram_id=30_002)
    food = _upsert_basic(db_session, user.id, name="Морковь")

    result = crud_personal_food.get_embeddings_for_food(
        db_session, personal_food_id=food.id
    )
    assert result == []


def test_get_embeddings_for_food_multiple(db_session):
    """get_embeddings_for_food returns all embeddings (canonical + aliases)."""
    user = _make_user(db_session, telegram_id=30_003)
    food = _upsert_basic(db_session, user.id, name="Кефир 1%")

    fake = [0.0, 0.5]
    crud_personal_food.add_embedding(
        db_session, personal_food_id=food.id, text_embedded="Кефир 1%", embedding=fake
    )
    crud_personal_food.add_embedding(
        db_session, personal_food_id=food.id, text_embedded="kefir", embedding=fake
    )

    embeddings = crud_personal_food.get_embeddings_for_food(
        db_session, personal_food_id=food.id
    )
    assert len(embeddings) == 2
    texts = {e.text_embedded for e in embeddings}
    assert texts == {"Кефир 1%", "kefir"}


def test_get_embeddings_for_food_scoped_to_food(db_session):
    """get_embeddings_for_food does not return embeddings from another food."""
    user = _make_user(db_session, telegram_id=30_004)
    food_a = _upsert_basic(db_session, user.id, name="Тунец")
    food_b = _upsert_basic(db_session, user.id, name="Лосось")

    crud_personal_food.add_embedding(
        db_session, personal_food_id=food_a.id, text_embedded="Тунец", embedding=[0.1]
    )

    result = crud_personal_food.get_embeddings_for_food(
        db_session, personal_food_id=food_b.id
    )
    assert result == []


# ---------------------------------------------------------------------------
# find_similar() — structural / smoke test only
# ---------------------------------------------------------------------------


def test_find_similar_method_exists():
    """find_similar is accessible as the mockable B6 seam (ADR-0003 §4d)."""
    assert callable(crud_personal_food.find_similar)


def test_find_similar_signature():
    """find_similar accepts the expected keyword arguments."""
    import inspect

    sig = inspect.signature(crud_personal_food.find_similar)
    params = set(sig.parameters)
    assert "db" in params
    assert "embedding" in params
    assert "threshold" in params
    assert "user_id" in params
