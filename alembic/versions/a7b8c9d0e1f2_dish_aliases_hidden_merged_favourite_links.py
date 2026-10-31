"""a dish can be hidden, merged or renamed, and a favourite belongs to a dish

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-10-31

Slice O. ``personal_foods`` gets ``aliases``, ``hidden_at`` and ``merged_into_id``;
``favorite_meals`` gets ``personal_food_id`` and ``usual_grams``.

The backfill finds or creates a dish for every favourite, removed ones too, because
↩️ Вернуть can still bring those back. A dish it finds keeps its numbers. A dish it
creates has none and reads ``per_portion``: the favourite keeps its own numbers as the
portion, and nothing is converted to per-100 g by a guess. ``usual_grams`` stays NULL,
which reads «Порция: как в той записи.».

Additive for old containers: every new column is nullable, and a favourite they write
is linked later by ``crud_favorite_meal.dish_of``. That also covers ``--sql``, where
the backfill cannot read rows.

The downgrade drops the columns and keeps the dishes the backfill made: an item may
already point at one, and they hold no numbers to mislead anybody.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MERGED_FK = "fk_personal_foods_merged_into_id_personal_foods"
_FAVOURITE_FK = "fk_favorite_meals_personal_food_id_personal_foods"
_FAVOURITE_INDEX = "ix_favorite_meals_personal_food_id"
_NO_NAME = "Блюдо"


def _key(name: str) -> str:
    """Python's fold: SQLite's ``lower()`` is ASCII only, and the names are Russian."""
    return name.strip().lower()


def _backfill(conn) -> None:
    favourites = conn.execute(
        sa.text(
            "SELECT id, user_id, name, last_used_at FROM favorite_meals "
            "WHERE personal_food_id IS NULL ORDER BY id"
        )
    ).all()
    known: dict = {}
    for fav in favourites:
        dishes = known.get(fav.user_id)
        if dishes is None:
            rows = conn.execute(
                sa.text(
                    "SELECT id, canonical_name FROM personal_foods "
                    "WHERE user_id = :user_id"
                ),
                {"user_id": fav.user_id},
            ).all()
            dishes = known[fav.user_id] = {
                _key(row.canonical_name): row.id for row in rows
            }
        name = (fav.name or "").strip() or _NO_NAME
        dish_id = dishes.get(_key(name))
        if dish_id is None:
            # Postgres folds with its own lower(), which can join two names Python
            # keeps apart. The unique key then decides, and the row that holds the
            # name is the dish: an IntegrityError here would stop the release.
            conn.execute(
                sa.text(
                    "INSERT INTO personal_foods (user_id, canonical_name, "
                    "numbers_basis, numbers_quality, times_used, resolution_source, "
                    "last_used_at) VALUES (:user_id, :name, 'per_portion', "
                    "'unverified', 0, 'favorite', :last_used_at) "
                    "ON CONFLICT DO NOTHING"
                ),
                {
                    "user_id": fav.user_id,
                    "name": name,
                    "last_used_at": fav.last_used_at,
                },
            )
            dish_id = conn.execute(
                sa.text(
                    "SELECT id FROM personal_foods WHERE user_id = :user_id "
                    "AND lower(canonical_name) = lower(:name) ORDER BY id"
                ),
                {"user_id": fav.user_id, "name": name},
            ).first()[0]
            dishes[_key(name)] = dish_id
        conn.execute(
            sa.text(
                "UPDATE favorite_meals SET personal_food_id = :dish_id "
                "WHERE id = :fav_id"
            ),
            {"dish_id": dish_id, "fav_id": fav.id},
        )


def upgrade() -> None:
    with op.batch_alter_table("personal_foods") as batch:
        batch.add_column(sa.Column("aliases", sa.JSON(), nullable=True))
        batch.add_column(
            sa.Column("hidden_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.add_column(sa.Column("merged_into_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            _MERGED_FK,
            "personal_foods",
            ["merged_into_id"],
            ["id"],
            ondelete="SET NULL",
        )
    with op.batch_alter_table("favorite_meals") as batch:
        batch.add_column(sa.Column("personal_food_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("usual_grams", sa.Float(), nullable=True))
        batch.create_foreign_key(
            _FAVOURITE_FK,
            "personal_foods",
            ["personal_food_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(_FAVOURITE_INDEX, "favorite_meals", ["personal_food_id"])

    if op.get_context().as_sql:
        op.execute("-- favourites: the backfill needs a live connection, not --sql")
        return
    _backfill(op.get_bind())


def downgrade() -> None:
    op.drop_index(_FAVOURITE_INDEX, table_name="favorite_meals")
    with op.batch_alter_table("favorite_meals") as batch:
        batch.drop_constraint(_FAVOURITE_FK, type_="foreignkey")
        batch.drop_column("usual_grams")
        batch.drop_column("personal_food_id")
    with op.batch_alter_table("personal_foods") as batch:
        batch.drop_constraint(_MERGED_FK, type_="foreignkey")
        batch.drop_column("merged_into_id")
        batch.drop_column("hidden_at")
        batch.drop_column("aliases")
