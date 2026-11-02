"""The slice O migration, actually executed on SQLite through alembic's ``Operations``.

``conftest`` builds its schema with ``create_all``, so nothing else runs it. What this
does not prove: the same DDL on a live Postgres. That stays the local check.
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
from app.models import (  # noqa: F401 (registers the tables)
    AccessGrant,
    FavoriteMeal,
    PersonalFood,
)
from tests.test_migrations import _chain, _heads

_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "a7b8c9d0e1f2_dish_aliases_hidden_merged_favourite_links.py"
)
NEW_FOOD_COLUMNS = {"aliases", "hidden_at", "merged_into_id"}
NEW_FAVOURITE_COLUMNS = {"personal_food_id", "usual_grams"}
_NEW = {"personal_foods": NEW_FOOD_COLUMNS, "favorite_meals": NEW_FAVOURITE_COLUMNS}


def _migration():
    spec = importlib.util.spec_from_file_location("slice_o_migration", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, step: str, module=None) -> None:
    with Operations.context(MigrationContext.configure(conn)):
        getattr(module or _migration(), step)()


def _schema_before(conn) -> None:
    """Both tables as they stand on the slice M head, with their defaults."""
    old = sa.MetaData()
    for name, new in _NEW.items():
        columns = [
            sa.Column(
                c.name,
                c.type,
                primary_key=c.primary_key,
                nullable=c.nullable,
                server_default=c.server_default.arg if c.server_default else None,
            )
            for c in Base.metadata.tables[name].columns
            if c.name not in new
        ]
        sa.Table(name, old, *columns)
    old.create_all(conn)


def _columns(conn, table: str) -> set:
    return {c["name"] for c in sa.inspect(conn).get_columns(table)}


def _food(conn, row_id: int, user: int, name: str, **columns) -> None:
    values = {"id": row_id, "user_id": user, "canonical_name": name, **columns}
    conn.execute(
        sa.text(
            f"INSERT INTO personal_foods ({', '.join(values)}) "
            f"VALUES ({', '.join(':' + key for key in values)})"
        ),
        values,
    )


def _favourite(conn, row_id: int, user: int, name: str, **columns) -> None:
    values = {
        "id": row_id,
        "user_id": user,
        "name": name,
        "calories": 360.0,
        "proteins": 24.0,
        "use_count": 3,
        "updated_at": "2026-01-02 03:04:05",
        **columns,
    }
    conn.execute(
        sa.text(
            f"INSERT INTO favorite_meals ({', '.join(values)}) "
            f"VALUES ({', '.join(':' + key for key in values)})"
        ),
        values,
    )


def _link(conn, fav_id: int):
    return conn.execute(
        sa.text("SELECT personal_food_id FROM favorite_meals WHERE id = :id"),
        {"id": fav_id},
    ).scalar_one()


def _dish(conn, food_id: int):
    return (
        conn.execute(
            sa.text("SELECT * FROM personal_foods WHERE id = :id"), {"id": food_id}
        )
        .mappings()
        .one()
    )


def _count(conn, table: str) -> int:
    return conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()


@pytest.fixture
def conn():
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        _schema_before(connection)
        yield connection
    engine.dispose()


def test_it_stands_on_the_edits_migration_and_one_head_follows():
    module = _migration()
    assert module.revision == "a7b8c9d0e1f2"
    assert module.down_revision == "f6a7b8c9d0e1"
    chain = _chain()
    assert _heads(chain) == ["e1f2a3b4c5d6"]
    assert chain["e1f2a3b4c5d6"][0] == ("d0e1f2a3b4c5",)
    assert chain["d0e1f2a3b4c5"][0] == ("c9d0e1f2a3b4",)
    assert chain["c9d0e1f2a3b4"][0] == ("b8c9d0e1f2a3",)
    assert chain["b8c9d0e1f2a3"][0] == ("a7b8c9d0e1f2",)
    # Known-positive: the check would have seen the old head as a head without it.
    del chain["e1f2a3b4c5d6"]
    del chain["d0e1f2a3b4c5"]
    del chain["c9d0e1f2a3b4"]
    del chain["b8c9d0e1f2a3"]
    assert _heads(chain) == ["a7b8c9d0e1f2"]


def test_the_upgrade_gives_what_the_models_declare(conn):
    assert not NEW_FOOD_COLUMNS & _columns(conn, "personal_foods")
    assert not NEW_FAVOURITE_COLUMNS & _columns(conn, "favorite_meals")
    _run(conn, "upgrade")
    for name in _NEW:
        assert _columns(conn, name) == {
            c.name for c in Base.metadata.tables[name].columns
        }
    indexes = sa.inspect(conn).get_indexes("favorite_meals")
    linked = [i for i in indexes if i["column_names"] == ["personal_food_id"]]
    assert [i["name"] for i in linked] == ["ix_favorite_meals_personal_food_id"]
    assert "ix_favorite_meals_personal_food_id" in {
        i.name for i in Base.metadata.tables["favorite_meals"].indexes
    }


def test_foreign_keys_let_a_dish_go(conn):
    _run(conn, "upgrade")
    inspector = sa.inspect(conn)
    for table, column in (
        ("personal_foods", "merged_into_id"),
        ("favorite_meals", "personal_food_id"),
    ):
        keys = [
            key
            for key in inspector.get_foreign_keys(table)
            if key["constrained_columns"] == [column]
        ]
        assert [k["referred_table"] for k in keys] == ["personal_foods"], table
        assert keys[0]["options"]["ondelete"] == "SET NULL", table
        model = Base.metadata.tables[table].columns[column]
        assert [fk.ondelete for fk in model.foreign_keys] == ["SET NULL"], table


def test_a_favourite_finds_its_dish_and_the_dish_keeps_its_numbers(conn):
    """Cyrillic case, which SQLite's own ``lower()`` cannot fold."""
    _food(
        conn,
        7,
        1,
        "Омлет с сыром",
        per_100g_calories=180,
        numbers_basis="per_100",
        numbers_quality="label",
        times_used=9,
    )
    _favourite(conn, 1, 1, "ОМЛЕТ С СЫРОМ ")
    before = dict(_dish(conn, 7))

    _run(conn, "upgrade")

    assert _link(conn, 1) == 7
    assert _count(conn, "personal_foods") == 1
    after = _dish(conn, 7)
    assert {key: after[key] for key in before} == before


def test_a_favourite_with_no_dish_gets_one_with_no_numbers_and_keeps_its_own(conn):
    _favourite(conn, 1, 1, "Самса", last_used_at="2026-03-04 05:06:07")

    _run(conn, "upgrade")

    dish = _dish(conn, _link(conn, 1))
    assert dish["canonical_name"] == "Самса"
    assert dish["user_id"] == 1
    assert dish["numbers_basis"] == "per_portion"
    assert dish["numbers_quality"] == "unverified"
    assert dish["resolution_source"] == "favorite"
    assert dish["per_100g_calories"] is None  # nothing is converted by a guess
    assert dish["per_100g_proteins"] is None
    assert dish["times_used"] == 0  # no meal has used it yet
    assert dish["meal_id"] is None  # a dish's meal is where its numbers came from
    assert str(dish["last_used_at"]).startswith("2026-03-04")
    fav = (
        conn.execute(sa.text("SELECT * FROM favorite_meals WHERE id = 1"))
        .mappings()
        .one()
    )
    assert (fav["calories"], fav["proteins"], fav["use_count"]) == (360.0, 24.0, 3)
    assert fav["usual_grams"] is None  # reads «Порция: как в той записи.»
    assert str(fav["updated_at"]).startswith("2026-01-02")  # no change to pull


def test_every_row_stays_and_one_name_makes_one_dish_per_person(conn):
    _favourite(conn, 1, 1, "Самса")
    _favourite(conn, 2, 1, "самса")
    _favourite(conn, 3, 2, "Самса")
    # Removed still has its ↩️ Вернуть on screen, and the dish stays by O11b.
    _favourite(conn, 4, 1, "Хлеб", removed_at="2026-01-01 00:00:00")

    _run(conn, "upgrade")

    assert _count(conn, "favorite_meals") == 4
    assert _link(conn, 1) == _link(conn, 2)
    assert _link(conn, 3) != _link(conn, 1)
    assert _dish(conn, _link(conn, 3))["user_id"] == 2
    assert _dish(conn, _link(conn, 4))["canonical_name"] == "Хлеб"
    assert _count(conn, "personal_foods") == 3


def test_another_persons_dish_is_never_found(conn):
    _food(conn, 7, 2, "Самса", per_100g_calories=300)
    _favourite(conn, 1, 1, "Самса")
    _run(conn, "upgrade")
    assert _link(conn, 1) != 7
    assert _dish(conn, _link(conn, 1))["user_id"] == 1


def test_a_blank_name_still_gets_a_named_dish(conn):
    _favourite(conn, 1, 1, "   ")
    _run(conn, "upgrade")
    assert _dish(conn, _link(conn, 1))["canonical_name"] == "Блюдо"


def test_the_unique_key_decides_where_python_and_the_database_fold_apart(conn):
    """Postgres's own ``lower()`` may join names Python keeps apart. The upgrade then
    has to take the row that holds the name: an IntegrityError here stops a release.

    SQLite's batch step drops an expression index, so the backfill runs on its own
    after it, with Python's fold switched off to make the two disagree.
    """
    module = _migration()
    _run(conn, "upgrade", module)
    conn.execute(
        sa.text(
            "CREATE UNIQUE INDEX uq_test "
            "ON personal_foods (user_id, lower(canonical_name))"
        )
    )
    _food(conn, 7, 1, "Omlet", per_100g_calories=180)
    # Known-positive: the key is live, and refuses this name by itself.
    with pytest.raises(sa.exc.IntegrityError):
        with conn.begin_nested():
            _food(conn, 8, 1, "OMLET")
    _favourite(conn, 1, 1, "OMLET")

    module._key = lambda name: name
    with Operations.context(MigrationContext.configure(conn)):
        module._backfill(conn)

    assert _link(conn, 1) == 7
    assert _count(conn, "personal_foods") == 1


def test_a_second_run_changes_nothing(conn):
    module = _migration()
    _favourite(conn, 1, 1, "Самса")
    _run(conn, "upgrade", module)
    first = _link(conn, 1)
    with Operations.context(MigrationContext.configure(conn)):
        module._backfill(conn)
    assert _link(conn, 1) == first
    assert _count(conn, "personal_foods") == 1


def test_a_row_from_an_old_container_still_inserts(conn):
    _run(conn, "upgrade")
    _favourite(conn, 1, 1, "Самса")
    _food(conn, 7, 1, "Хлеб")
    assert _link(conn, 1) is None
    dish = _dish(conn, 7)
    assert dish["aliases"] is None
    assert dish["hidden_at"] is None
    assert dish["merged_into_id"] is None


def test_the_downgrade_takes_the_columns_back_and_keeps_the_rows(conn):
    _favourite(conn, 1, 1, "Самса")
    _run(conn, "upgrade")
    _run(conn, "downgrade")
    assert not NEW_FOOD_COLUMNS & _columns(conn, "personal_foods")
    assert not NEW_FAVOURITE_COLUMNS & _columns(conn, "favorite_meals")
    assert _count(conn, "favorite_meals") == 1
    # A later item may link the backfilled dish: it stays, and has no numbers to lie.
    assert _count(conn, "personal_foods") == 1


def test_postgres_renders_real_foreign_keys_and_says_the_backfill_did_not_run():
    out = io.StringIO()
    context = MigrationContext.configure(
        dialect_name="postgresql", opts={"as_sql": True, "output_buffer": out}
    )
    with Operations.context(context):
        _migration().upgrade()
    sql = out.getvalue()
    assert "ADD COLUMN aliases JSON" in sql
    assert "ADD COLUMN hidden_at TIMESTAMP WITH TIME ZONE" in sql
    assert "FOREIGN KEY(merged_into_id) REFERENCES personal_foods (id)" in sql
    assert "FOREIGN KEY(personal_food_id) REFERENCES personal_foods (id)" in sql
    assert sql.count("ON DELETE SET NULL") == 2
    assert "CREATE INDEX ix_favorite_meals_personal_food_id" in sql
    assert "backfill needs a live connection" in sql


# --- access_grants, the same way -------------------------------------------------------

_GRANTS = _PATH.parent / "e1f2a3b4c5d6_access_grants.py"


def _grants_migration():
    spec = importlib.util.spec_from_file_location("access_grants_migration", _GRANTS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_access_grants_goes_up_as_the_model_and_comes_back_down(conn):
    module = _grants_migration()
    _run(conn, "upgrade", module)
    found = {c["name"]: c for c in sa.inspect(conn).get_columns("access_grants")}
    model = Base.metadata.tables["access_grants"]
    assert set(found) == {c.name for c in model.columns}
    assert isinstance(found["telegram_id"]["type"], sa.BigInteger)
    for column in model.columns:
        assert found[column.name]["nullable"] == column.nullable, column.name
    indexes = {i["name"]: i for i in sa.inspect(conn).get_indexes("access_grants")}
    assert indexes["ix_access_grants_telegram_id"]["unique"] == 1
    assert {i.name for i in model.indexes} == set(indexes)

    _run(conn, "downgrade", module)
    assert "access_grants" not in sa.inspect(conn).get_table_names()
