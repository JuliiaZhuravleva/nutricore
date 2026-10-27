"""Unit tests for CRUDPersonalFood (B1 — personal food DB, ADR-0003).

Coverage:
  - upsert(): create new food + idempotent re-upsert (counter increment)
  - upsert(): case-insensitive dedup key (lower(canonical_name))
  - upsert(): selective field update on re-upsert (None fields not overwritten)
  - upsert(): barcode persisted on create + updated on re-upsert
  - get_by_barcode(): hit / miss / user scope isolation
  - add_embedding(): create embedding row
  - get_embeddings_for_food(): returns all embeddings for a food

find_similar()'s ANN *semantics* are not tested here — the pgvector <=> operator is
Postgres-only, and B6 tests SavedFoodRAGStrategy by mocking crud_personal_food.find_similar.
Its *statement* IS tested here, at compile level against the Postgres dialect: the
query shipped broken once (see the find_similar section below), and "we cannot run
the operator" was the reason nobody checked the SQL itself. Only the real distances
and the 0.15 threshold remain a post-deploy verification (ADR-0003 §4d).

All tests run on the in-memory SQLite DB from conftest.py (Base.metadata.create_all).
The Vector(1536) column uses the fallback UserDefinedType which SQLite accepts as TEXT.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.crud.crud_personal_food import AnnQueryError, crud_personal_food
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

    food = _upsert_basic(
        db_session, user.id, name="Молоко БЗМЖ", barcode="4607195501226"
    )

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
# find_similar() — the SHIPPED statement, asserted at compile level
# ---------------------------------------------------------------------------
#
# These replace two structural smoke tests (``assert callable(...)`` and an
# inspect.signature key check) that stayed green on a query psycopg2 refused to
# run. On 2026-09-13 the ANN query was found to be dead in production: text()
# with ":embedding::vector" parses a PHANTOM bind named "embeddin", leaves the
# placeholder literal in the compiled SQL, and Postgres raises
# 'syntax error at or near ":"'. find_similar's broad except turned that into a
# permanent silent "no match", so the personal-food RAG could never hit.
#
# SQLite cannot run pgvector's <=> operator, so the ANN *result* still has to be
# mocked. The *statement* does not: it is asserted here against the Postgres
# dialect, which needs no database at all.


class _CapturingSession:
    """Records the statement find_similar executes; reports a clean miss."""

    def __init__(self):
        self.stmt = None
        self.params = None
        self.rolled_back = False

    def execute(self, stmt, params=None):
        self.stmt, self.params = stmt, params
        return SimpleNamespace(first=lambda: None)

    def rollback(self):  # pragma: no cover - only used by the failure test
        self.rolled_back = True


def test_find_similar_binds_the_embedding_parameter():
    """The ANN query must BIND :embedding, not emit it literally."""
    db = _CapturingSession()

    assert (
        crud_personal_food.find_similar(
            db, embedding=[0.1] * 1536, threshold=0.15, user_id=7
        )
        is None
    )

    assert db.stmt is not None, "find_similar never executed a statement"
    assert isinstance(db.params, dict), (
        "params are no longer a dict — this control would pass vacuously; "
        "update it for the new call shape"
    )
    # 1. No phantom bind name.
    assert set(db.stmt._bindparams) == {"embedding", "user_id"}, (
        f"phantom bind params {sorted(db.stmt._bindparams)} — "
        "':embedding::vector' parses as ':embeddin' plus literal 'g::vector'"
    )
    # 2. The SQL that actually ships to psycopg2.
    sql = str(db.stmt.compile(dialect=postgresql.dialect()))
    assert "%(embedding)s" in sql, f"embedding not bound; compiled SQL: {sql}"
    assert ":embedding" not in sql, (
        f"literal colon placeholder left in compiled SQL — Postgres will raise "
        f'syntax error at or near ":". SQL: {sql}'
    )
    # 3. The value handed over is pgvector's textual form.
    assert db.params["embedding"].startswith("[")
    assert db.params["user_id"] == 7


class _BoomSession:
    """A session whose ANN query always fails."""

    def __init__(self, rollback_raises: bool = False):
        self.rolled_back = False
        self._rollback_raises = rollback_raises

    def execute(self, *a, **k):
        raise RuntimeError('syntax error at or near ":"')

    def rollback(self):
        if self._rollback_raises:
            raise RuntimeError("connection already gone")
        self.rolled_back = True


def test_find_similar_raises_on_sql_failure_instead_of_reporting_a_miss():
    """A BROKEN query must not look like an empty personal food DB.

    Returning None here is what let the ':embedding::vector' defect hide: the
    strategy above recorded 'saved_rag: miss' on every meal, identical to a user
    who has simply saved nothing. The caller still swallows this raise into a
    fall-through, so the pipeline stays non-blocking — but it can now RECORD it.
    """
    db = _BoomSession()
    with caplog_at_error() as records:
        with pytest.raises(AnnQueryError):
            crud_personal_food.find_similar(
                db, embedding=[0.1], threshold=0.15, user_id=7
            )

    assert any(
        "ANN query FAILED" in r.getMessage() for r in records
    ), "a systemic ANN failure was raised with no ERROR log"
    assert any("RuntimeError" in r.getMessage() for r in records), (
        "the exception class is not in the message; a SQL bug and a dropped "
        "connection read identically in the logs"
    )
    assert db.rolled_back, (
        "the aborted transaction was not rolled back — every later query on "
        "this Session would fail too"
    )


def test_find_similar_logs_when_the_rollback_itself_fails(caplog):
    """The recovery failing is exactly what makes the NEXT errors unexplainable.

    Every later strategy in the same pipeline run then fails with an unrelated
    looking 'transaction is aborted', and nothing points back here.
    """
    with caplog.at_level(logging.WARNING, logger="app.crud.crud_personal_food"):
        with pytest.raises(AnnQueryError):
            crud_personal_food.find_similar(
                _BoomSession(rollback_raises=True),
                embedding=[0.1],
                threshold=0.15,
                user_id=7,
            )

    assert any(
        "rollback" in r.getMessage() for r in caplog.records
    ), "the rollback failure was swallowed by a bare except/pass"


def test_find_similar_clean_miss_logs_no_error():
    """Expected answer 'no finding': an empty result set is not an error."""
    with caplog_at_error() as records:
        assert (
            crud_personal_food.find_similar(
                _CapturingSession(), embedding=[0.1], threshold=0.15, user_id=7
            )
            is None
        )
    assert not records, (
        "a legitimate no-match logged an ERROR — the signal that distinguishes "
        "a broken ANN from an empty DB would be worthless"
    )


@contextmanager
def caplog_at_error():
    """Collect ERROR records from the crud logger (no pytest fixture needed)."""
    logger = logging.getLogger("app.crud.crud_personal_food")
    records: list = []

    class _Collector(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Collector(level=logging.ERROR)
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.ERROR)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


# ---------------------------------------------------------------------------
# F7 — times_used is retry-safe (gated on meal_id)
# ---------------------------------------------------------------------------


def test_upsert_same_meal_id_does_not_double_count(db_session):
    """A Celery retry re-upserts with the SAME meal_id — times_used must not inflate (F7)."""
    user = _make_user(db_session, telegram_id=10_701)
    first = _upsert_basic(db_session, user.id, name="Овсянка", meal_id=555)
    assert first.times_used == 1

    again = _upsert_basic(db_session, user.id, name="Овсянка", meal_id=555)
    assert again.id == first.id
    assert again.times_used == 1  # not double-counted


def test_upsert_new_meal_id_increments(db_session):
    """A genuinely new meal (different meal_id) of the same food increments times_used (F7)."""
    user = _make_user(db_session, telegram_id=10_702)
    _upsert_basic(db_session, user.id, name="Овсянка", meal_id=1)
    second = _upsert_basic(db_session, user.id, name="Овсянка", meal_id=2)
    assert second.times_used == 2


# ---------------------------------------------------------------------------
# F6 — get_by_barcode tolerates duplicate barcodes (most-used wins)
# ---------------------------------------------------------------------------


def test_get_by_barcode_duplicate_returns_most_used(db_session):
    """Same barcode on two rows (different names) must not raise; return most-used (F6)."""
    user = _make_user(db_session, telegram_id=10_601)
    less_used = _upsert_basic(
        db_session, user.id, name="Кола", barcode="4600000000017", meal_id=1
    )
    # Confirm the second food twice (distinct meals) so it's the most-used.
    _upsert_basic(
        db_session, user.id, name="Coca-Cola", barcode="4600000000017", meal_id=2
    )
    most_used = _upsert_basic(
        db_session, user.id, name="Coca-Cola", barcode="4600000000017", meal_id=3
    )
    assert most_used.times_used == 2
    assert less_used.times_used == 1

    hit = crud_personal_food.get_by_barcode(
        db_session, barcode="4600000000017", user_id=user.id
    )
    assert hit is not None
    assert hit.id == most_used.id  # returns the most-used row, no MultipleResultsFound


# ---------------------------------------------------------------------------
# F5 — embedding dedup backstopped by unique constraint
# ---------------------------------------------------------------------------


def test_add_embedding_duplicate_is_deduped():
    """A duplicate (food, text) embedding is deduped by the unique constraint (F5).

    Uses a standalone real session, not the db_session fixture: that fixture binds
    the session to an outer transaction that can't survive add_embedding's in-code
    rollback on the duplicate path.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db.base import Base

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        user = _make_user(db, telegram_id=10_501)
        food = _upsert_basic(db, user.id, name="Банан")
        vec = [0.1] * 1536

        first = crud_personal_food.add_embedding(
            db, personal_food_id=food.id, text_embedded="Банан", embedding=vec
        )
        # Second insert of the same (food, text) — deduped by the unique constraint.
        second = crud_personal_food.add_embedding(
            db, personal_food_id=food.id, text_embedded="Банан", embedding=vec
        )
        assert second.id == first.id

        rows = crud_personal_food.get_embeddings_for_food(db, personal_food_id=food.id)
        assert len(rows) == 1
    finally:
        db.close()
        engine.dispose()
