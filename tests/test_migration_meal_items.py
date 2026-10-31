"""The slice N migration, actually executed.

``conftest`` builds its schema with ``create_all``, so no test ever runs a migration
and a wrong backfill would first run at release. Here the one migration runs on
SQLite through alembic's real ``Operations`` against a schema shaped like the day
before it, and the result is compared with the models. The Postgres rendering is
read from alembic's offline mode. What this does not prove: the same DDL on a live
Postgres, which stays the local check before handoff (section 10 of the spec).
"""

from __future__ import annotations

import importlib.util
import io
import pathlib

import pytest
import sqlalchemy as sa

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.db.base import Base
from app.models import MealItem, RecordEvent  # noqa: F401  (registers the tables)

_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "d4e5f6a7b8c9_add_meal_items_and_record_events.py"
)

NEW_MEAL_COLUMNS = {
    "sent_at",
    "time_precision",
    "time_source",
    "time_phrase",
    "numbers_basis",
    "plate_totals",
    "deleted_at",
    "rev",
}
NEW_FOOD_COLUMNS = {
    "usual_grams",
    "usual_grams_by_user",
    "numbers_basis",
    "numbers_quality",
    "numbers_by_user",
}
_NEW_BY_TABLE = {"meals": NEW_MEAL_COLUMNS, "personal_foods": NEW_FOOD_COLUMNS}


def _migration():
    spec = importlib.util.spec_from_file_location("slice_n_migration", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, step: str) -> None:
    with Operations.context(MigrationContext.configure(conn)):
        getattr(_migration(), step)()


def _schema_before(conn) -> None:
    """``meals`` and ``personal_foods`` as they stand on a9b8c7d6e5f4."""
    old = sa.MetaData()
    for name, new in _NEW_BY_TABLE.items():
        columns = [
            sa.Column(c.name, c.type, primary_key=c.primary_key)
            for c in Base.metadata.tables[name].columns
            if c.name not in new
        ]
        sa.Table(name, old, *columns)
    old.create_all(conn)


def _gaps(conn) -> dict:
    """{table: columns the model has and the database lacks, or the reverse}."""
    inspector = sa.inspect(conn)
    gaps = {}
    for name in ("meals", "personal_foods", "meal_items", "record_events"):
        model = {c.name for c in Base.metadata.tables[name].columns}
        found = (
            {c["name"] for c in inspector.get_columns(name)}
            if inspector.has_table(name)
            else set()
        )
        if model != found:
            gaps[name] = model ^ found
    return gaps


@pytest.fixture
def conn():
    engine = sa.create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        _schema_before(connection)
        yield connection
    engine.dispose()


def test_upgrade_gives_every_column_the_models_declare(conn):
    # Known positive: the same comparison, before the migration, names the gaps.
    before = _gaps(conn)
    assert before["meals"] == NEW_MEAL_COLUMNS
    assert before["personal_foods"] == NEW_FOOD_COLUMNS
    assert "position" in before["meal_items"]
    assert "undoes_event_id" in before["record_events"]

    _run(conn, "upgrade")

    assert _gaps(conn) == {}


def test_nullability_and_foreign_keys_match_the_models(conn):
    _run(conn, "upgrade")
    inspector = sa.inspect(conn)
    for name in ("meals", "personal_foods", "meal_items", "record_events"):
        table = Base.metadata.tables[name]
        found = {c["name"]: c for c in inspector.get_columns(name)}
        watched = (
            {c.name for c in table.columns}
            if name not in _NEW_BY_TABLE
            else _NEW_BY_TABLE[name]
        )
        for column in watched:
            if table.c[column].primary_key:
                continue
            assert found[column]["nullable"] == table.c[column].nullable, (
                name,
                column,
            )
    for name in ("meal_items", "record_events"):
        table = Base.metadata.tables[name]
        model = {
            fk.parent.name: (fk.column.table.name, fk.ondelete)
            for fk in table.foreign_keys
        }
        found = {
            fk["constrained_columns"][0]: (
                fk["referred_table"],
                fk["options"].get("ondelete"),
            )
            for fk in inspector.get_foreign_keys(name)
        }
        assert found == model, name


def test_immutable_links_are_plain_integers_not_foreign_keys(conn):
    """A SET NULL on either would rewrite what the spec calls immutable."""
    _run(conn, "upgrade")
    keys = {
        fk["constrained_columns"][0]
        for fk in sa.inspect(conn).get_foreign_keys("meal_items")
    }
    assert keys == {"meal_id", "personal_food_id", "replaced_by_item_id"}


def test_indexes_match_the_models(conn):
    _run(conn, "upgrade")
    inspector = sa.inspect(conn)
    meal_indexes = {
        i["name"]: i["column_names"] for i in inspector.get_indexes("meals")
    }
    assert meal_indexes["ix_meals_user_id_meal_time"] == ["user_id", "meal_time"]
    model = {i.name for i in Base.metadata.tables["meals"].indexes}
    assert "ix_meals_user_id_meal_time" in model
    for name in ("meal_items", "record_events"):
        found = {i["name"] for i in inspector.get_indexes(name)}
        assert found == {i.name for i in Base.metadata.tables[name].indexes}, name
    item_indexed = {i["column_names"][0] for i in inspector.get_indexes("meal_items")}
    assert {"meal_id", "personal_food_id"} <= item_indexed


def test_backfill_of_existing_rows(conn):
    conn.execute(
        sa.text(
            "INSERT INTO meals (id, user_id, meal_time, created_at) VALUES "
            "(1, 1, '2026-10-01 08:00:00', '2026-10-01 09:00:00'), "
            "(2, 1, '2026-10-02 08:00:00', NULL), "
            "(3, 1, NULL, NULL)"
        )
    )
    conn.execute(
        sa.text(
            "INSERT INTO personal_foods (id, user_id, canonical_name, barcode) "
            "VALUES (1, 1, 'a', '4600000000000'), (2, 1, 'b', NULL)"
        )
    )

    _run(conn, "upgrade")

    meals = conn.execute(
        sa.text(
            "SELECT sent_at, time_precision, time_source, time_phrase, numbers_basis, "
            "plate_totals, deleted_at, rev FROM meals ORDER BY id"
        )
    ).all()
    assert meals[0][0].startswith("2026-10-01 09:00")  # created_at wins
    assert meals[1][0].startswith("2026-10-02 08:00")  # else meal_time
    assert meals[2][0] is None  # nothing to go on: no invented time
    for row in meals:
        assert tuple(row[1:]) == ("exact", "sent", None, "plate", None, None, 0)

    foods = conn.execute(
        sa.text(
            "SELECT numbers_basis, numbers_quality, numbers_by_user, "
            "usual_grams_by_user, usual_grams FROM personal_foods ORDER BY id"
        )
    ).all()
    assert tuple(foods[0]) == ("per_100", "barcode", 0, 0, None)
    assert tuple(foods[1]) == ("per_100", "unverified", 0, 0, None)


def test_a_row_from_an_old_container_still_inserts(conn):
    """Migrate runs before start, so old code can meet the new schema."""
    _run(conn, "upgrade")
    conn.execute(sa.text("INSERT INTO meals (user_id, description) VALUES (1, 'x')"))
    conn.execute(
        sa.text("INSERT INTO personal_foods (user_id, canonical_name) VALUES (1, 'x')")
    )
    assert conn.execute(sa.text("SELECT rev, numbers_basis FROM meals")).one() == (
        0,
        "plate",
    )


def test_downgrade_returns_to_the_day_before_and_keeps_rows(conn):
    conn.execute(sa.text("INSERT INTO meals (id, user_id) VALUES (1, 1)"))
    _run(conn, "upgrade")
    _run(conn, "downgrade")

    inspector = sa.inspect(conn)
    assert not inspector.has_table("meal_items")
    assert not inspector.has_table("record_events")
    names = {c["name"] for c in inspector.get_columns("meals")}
    assert not names & NEW_MEAL_COLUMNS
    names = {c["name"] for c in inspector.get_columns("personal_foods")}
    assert not names & NEW_FOOD_COLUMNS
    assert conn.execute(sa.text("SELECT count(*) FROM meals")).scalar() == 1


def test_postgres_rendering():
    """Offline mode: the DDL a real ``alembic upgrade`` would send to Postgres."""
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": buffer}
    )
    with Operations.context(context):
        _migration().upgrade()
    sql = buffer.getvalue()

    assert "CREATE TABLE meal_items" in sql
    assert "CREATE TABLE record_events" in sql
    assert "COALESCE(created_at, meal_time)" in sql
    assert "ON DELETE CASCADE" in sql
    assert "ON DELETE SET NULL" in sql
    assert "by_user BOOLEAN DEFAULT false NOT NULL" in sql
    assert (
        "CREATE INDEX ix_meals_user_id_meal_time ON meals (user_id, meal_time)" in sql
    )
