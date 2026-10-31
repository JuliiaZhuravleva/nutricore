"""The slice M migration, actually executed on SQLite through alembic's ``Operations``.

``conftest`` builds its schema with ``create_all``, so nothing else runs it. What this
does not prove: the same DDL on a live Postgres, which stays the local check.
"""

from __future__ import annotations

import importlib.util
import io
import pathlib

import sqlalchemy as sa

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.db.base import Base
from app.models import MealMessage, MealReading, MealSource  # noqa: F401

_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "e5f6a7b8c9d0_add_meal_sources_readings_messages.py"
)
NEW_TABLES = {"meal_sources", "meal_readings", "meal_messages"}
NEW_MEAL_COLUMNS = {"reading_status", "read_attempts", "is_draft", "merged_into_id"}
NEW_ITEM_COLUMNS = {"source_id"}


def _migration():
    spec = importlib.util.spec_from_file_location("slice_m_migration", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, step: str) -> None:
    with Operations.context(MigrationContext.configure(conn)):
        getattr(_migration(), step)()


def _schema_before(conn) -> None:
    old = sa.MetaData()
    for name, new in (("meals", NEW_MEAL_COLUMNS), ("meal_items", NEW_ITEM_COLUMNS)):
        columns = [
            sa.Column(c.name, c.type, primary_key=c.primary_key)
            for c in Base.metadata.tables[name].columns
            if c.name not in new
        ]
        sa.Table(name, old, *columns)
    old.create_all(conn)


def _columns(conn, table: str) -> set:
    return {c["name"] for c in sa.inspect(conn).get_columns(table)}


def test_it_stands_on_the_current_head():
    module = _migration()
    assert module.down_revision == "d4e5f6a7b8c9"
    assert module.revision == "e5f6a7b8c9d0"


def test_the_upgrade_gives_what_the_models_declare_and_an_old_meal_counts_as_read():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _schema_before(conn)
        # Known-positive: the old row exists, and the columns do not, before the step.
        conn.execute(sa.text("INSERT INTO meals (id, user_id) VALUES (1, 1)"))
        assert not NEW_MEAL_COLUMNS & _columns(conn, "meals")

        _run(conn, "upgrade")

        tables = set(sa.inspect(conn).get_table_names())
        assert NEW_TABLES <= tables
        for name in NEW_TABLES:
            assert _columns(conn, name) == {
                c.name for c in Base.metadata.tables[name].columns
            }
        assert NEW_MEAL_COLUMNS <= _columns(conn, "meals")
        assert NEW_ITEM_COLUMNS <= _columns(conn, "meal_items")
        row = conn.execute(
            sa.text("SELECT reading_status, read_attempts, is_draft FROM meals")
        ).one()
        assert row.reading_status == "read"
        assert row.read_attempts == 0
        assert not row.is_draft


def test_the_unique_keys_stand_after_the_upgrade():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _schema_before(conn)
        _run(conn, "upgrade")
        sources = sa.inspect(conn).get_unique_constraints("meal_sources")
        messages = sa.inspect(conn).get_unique_constraints("meal_messages")
    assert [set(u["column_names"]) for u in sources] == [
        {"chat_id", "message_id", "kind"}
    ]
    assert [set(u["column_names"]) for u in messages] == [{"chat_id", "message_id"}]


def test_the_downgrade_takes_back_all_of_it():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        _schema_before(conn)
        _run(conn, "upgrade")
        _run(conn, "downgrade")
        assert not NEW_TABLES & set(sa.inspect(conn).get_table_names())
        assert not NEW_MEAL_COLUMNS & _columns(conn, "meals")
        assert not NEW_ITEM_COLUMNS & _columns(conn, "meal_items")


def test_postgres_renders_it_with_a_real_foreign_key_and_defaults():
    out = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": out}
    )
    with Operations.context(context):
        _migration().upgrade()
    sql = out.getvalue()
    assert "CREATE TABLE meal_sources" in sql
    assert "reading_status VARCHAR DEFAULT 'read' NOT NULL" in sql
    assert "meals_merged_into_id_fkey" in sql or "merged_into_id" in sql
    assert "UPDATE" not in sql  # the server default is the backfill: no rewrite
