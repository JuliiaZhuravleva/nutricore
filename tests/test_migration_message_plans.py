"""The plans migration, actually executed on SQLite through alembic's ``Operations``.

What this does not prove: the same DDL on a live Postgres, which stays the local
check before hand-off (the partial index is rendered below in offline mode).
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
from app.models import MealSource, MessagePlan  # noqa: F401  (registers the tables)

_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "c3d7f9a1b5e2_message_plans.py"
)
_WHERE = sa.text("supersedes_id IS NULL")


def _migration():
    spec = importlib.util.spec_from_file_location("plans_migration", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, step: str) -> None:
    with Operations.context(MigrationContext.configure(conn)):
        getattr(_migration(), step)()


def _schema_before(conn) -> None:
    """``users`` and ``meal_sources`` as they stand on e2f6b8c1d3a5."""
    old = sa.MetaData()
    sa.Table("users", old, sa.Column("id", sa.Integer, primary_key=True))
    columns = [
        sa.Column(c.name, c.type, primary_key=c.primary_key)
        for c in Base.metadata.tables["meal_sources"].columns
        if c.name != "part"
    ]
    table = sa.Table("meal_sources", old, *columns)
    sa.Index(
        "uq_meal_sources_message",
        table.c.chat_id,
        table.c.message_id,
        table.c.kind,
        unique=True,
        sqlite_where=_WHERE,
    )
    old.create_all(conn)


def _insert(conn, row_id: int, part=None, supersedes=None) -> None:
    columns = "id, user_id, chat_id, message_id, kind, supersedes_id"
    values = ":id, 1, 42, 7, 'text', :supersedes"
    if part is not None:
        columns, values = f"{columns}, part", f"{values}, :part"
    conn.execute(
        sa.text(f"INSERT INTO meal_sources ({columns}) VALUES ({values})"),
        {"id": row_id, "part": part, "supersedes": supersedes},
    )


def _refused(conn, *args, **kwargs) -> bool:
    try:
        with conn.begin_nested():
            _insert(conn, *args, **kwargs)
    except sa.exc.IntegrityError:
        return True
    return False


def _columns(conn, table: str) -> set:
    return {c["name"] for c in sa.inspect(conn).get_columns(table)}


@pytest.fixture
def conn():
    engine = sa.create_engine("sqlite://")
    with engine.connect() as connection:
        _schema_before(connection)
        connection.commit()
        yield connection
    engine.dispose()


def test_it_stands_on_the_tidy_up():
    module = _migration()
    assert module.revision == "c3d7f9a1b5e2"
    assert module.down_revision == "e2f6b8c1d3a5"


def test_the_upgrade_gives_what_the_models_declare(conn):
    # Known positive: before the step the table and the column are not there.
    assert not sa.inspect(conn).has_table("message_plans")
    assert "part" not in _columns(conn, "meal_sources")

    _run(conn, "upgrade")

    for name in ("message_plans", "meal_sources"):
        assert _columns(conn, name) == {
            c.name for c in Base.metadata.tables[name].columns
        }, name
    inspector = sa.inspect(conn)
    found = {c["name"]: c for c in inspector.get_columns("message_plans")}
    for column in Base.metadata.tables["message_plans"].columns:
        if not column.primary_key:
            assert found[column.name]["nullable"] == column.nullable, column.name
    part = {c["name"]: c for c in inspector.get_columns("meal_sources")}["part"]
    assert part["nullable"] is False
    indexes = {i["name"]: i for i in inspector.get_indexes("message_plans")}
    model = {i.name for i in Base.metadata.tables["message_plans"].indexes}
    assert set(indexes) == model
    keys = {
        (u["name"], tuple(u["column_names"]))
        for u in inspector.get_unique_constraints("message_plans")
    }
    assert ("uq_message_plans_message", ("chat_id", "message_id")) in keys
    fks = {
        fk["constrained_columns"][0]: fk["options"].get("ondelete")
        for fk in inspector.get_foreign_keys("message_plans")
    }
    assert fks == {"user_id": "CASCADE"}
    key = {i["name"]: i for i in inspector.get_indexes("meal_sources")}[
        "uq_meal_sources_message"
    ]
    assert key["column_names"] == ["chat_id", "message_id", "kind", "part"]
    assert key["unique"]


def test_parts_of_one_message_stand_and_a_replay_is_still_refused(conn):
    _insert(conn, 1)
    # Known positive: under the old key a second meal of the message is refused.
    assert _refused(conn, 2)

    _run(conn, "upgrade")

    assert conn.execute(sa.text("SELECT part FROM meal_sources")).scalar() == 0
    assert not _refused(conn, 2, part=1)
    assert not _refused(conn, 3, part=2)
    assert _refused(conn, 4, part=1)  # the same part again: a replay
    assert _refused(conn, 5)  # the message itself again, written as old code does
    assert not _refused(conn, 6, part=1, supersedes=2)  # an edit beside a part


def test_the_downgrade_keeps_the_first_meal_of_each_message(conn):
    _run(conn, "upgrade")
    _insert(conn, 1, part=1)
    _insert(conn, 2, part=2)
    _insert(conn, 3, part=3)
    _insert(conn, 4, part=2, supersedes=2)  # an edit: the old key holds it
    conn.execute(
        sa.text(
            "INSERT INTO message_plans (user_id, chat_id, message_id, kind, want, "
            "status) VALUES (1, 42, 7, 'text', 'meals', 'done')"
        )
    )

    _run(conn, "downgrade")

    kept = conn.execute(sa.text("SELECT id FROM meal_sources ORDER BY id"))
    assert kept.scalars().all() == [1, 4]
    assert not sa.inspect(conn).has_table("message_plans")
    assert "part" not in _columns(conn, "meal_sources")
    assert _refused(conn, 5)  # the old key is back
    assert not _refused(conn, 6, supersedes=1)


def test_up_down_up(conn):
    _run(conn, "upgrade")
    _run(conn, "downgrade")
    _run(conn, "upgrade")
    assert "part" in _columns(conn, "meal_sources")


def test_postgres_rendering():
    """Offline mode: the DDL a real ``alembic upgrade`` would send to Postgres."""
    buffer = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": buffer}
    )
    with Operations.context(context):
        _migration().upgrade()
    sql = buffer.getvalue()

    assert "CREATE TABLE message_plans" in sql
    assert "ON DELETE CASCADE" in sql
    assert "ADD COLUMN part SMALLINT DEFAULT '0' NOT NULL" in sql
    assert "DROP INDEX uq_meal_sources_message" in sql
    assert (
        "CREATE UNIQUE INDEX uq_meal_sources_message ON meal_sources "
        "(chat_id, message_id, kind, part) WHERE supersedes_id IS NULL" in sql
    )
