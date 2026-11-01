"""add meal_items, record_events and the slice N columns

Revision ID: d4e5f6a7b8c9
Revises: a9b8c7d6e5f4
Create Date: 2026-10-31

Stage 5, slice N: a meal gets dishes of its own (``meal_items``), every change leaves
an event (``record_events``), ``meals`` learns when a message was sent, how exact its
time is, what its totals stand on, and a soft delete, and ``personal_foods`` learns
the usual portion and whose numbers it holds.

Additive: migrate runs before start, so old containers may meet this schema. Every
new column is nullable or has a server default. ``meal_time`` keeps its name and its
meaning. Old meals are marked ``numbers_basis='plate'`` and are never recomputed. They
get ``sent_at`` from ``created_at``, else ``meal_time``, and no eaten time is invented.

``(user_id, meal_time)`` is a new index: history screens read by it and none existed.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "a9b8c7d6e5f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamps() -> list:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    ]


def upgrade() -> None:
    op.add_column("meals", sa.Column("sent_at", sa.DateTime(timezone=True)))
    op.add_column(
        "meals",
        sa.Column(
            "time_precision", sa.String(), nullable=False, server_default="exact"
        ),
    )
    op.add_column(
        "meals",
        sa.Column("time_source", sa.String(), nullable=False, server_default="sent"),
    )
    op.add_column("meals", sa.Column("time_phrase", sa.String()))
    op.add_column(
        "meals",
        sa.Column("numbers_basis", sa.String(), nullable=False, server_default="plate"),
    )
    op.add_column("meals", sa.Column("plate_totals", sa.JSON()))
    op.add_column("meals", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.add_column(
        "meals", sa.Column("rev", sa.Integer(), nullable=False, server_default="0")
    )
    op.execute(
        "UPDATE meals SET sent_at = COALESCE(created_at, meal_time) "
        "WHERE sent_at IS NULL"
    )
    op.create_index("ix_meals_user_id_meal_time", "meals", ["user_id", "meal_time"])

    op.add_column("personal_foods", sa.Column("usual_grams", sa.Float()))
    op.add_column(
        "personal_foods",
        sa.Column(
            "usual_grams_by_user",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "personal_foods",
        sa.Column(
            "numbers_basis", sa.String(), nullable=False, server_default="per_100"
        ),
    )
    op.add_column(
        "personal_foods",
        sa.Column(
            "numbers_quality",
            sa.String(),
            nullable=False,
            server_default="unverified",
        ),
    )
    op.add_column(
        "personal_foods",
        sa.Column(
            "numbers_by_user",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute(
        "UPDATE personal_foods SET numbers_quality = 'barcode' "
        "WHERE barcode IS NOT NULL"
    )

    op.create_table(
        "meal_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("meal_id", sa.Integer(), nullable=False),
        sa.Column("origin_meal_id", sa.Integer(), nullable=False),
        sa.Column("personal_food_id", sa.Integer(), nullable=True),
        sa.Column("original_food_id", sa.Integer(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("amount_text", sa.String(), nullable=True),
        sa.Column("grams", sa.Float(), nullable=True),
        sa.Column("base_grams", sa.Float(), nullable=True),
        sa.Column("base_calories", sa.Float(), nullable=True),
        sa.Column("base_proteins", sa.Float(), nullable=True),
        sa.Column("base_fats", sa.Float(), nullable=True),
        sa.Column("base_carbohydrates", sa.Float(), nullable=True),
        sa.Column("factor", sa.Float(), nullable=False, server_default="1"),
        sa.Column("numbers_source", sa.String(), nullable=False, server_default="none"),
        sa.Column("by_user", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("replaced_by_item_id", sa.Integer(), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["meal_id"], ["meals.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["personal_food_id"], ["personal_foods.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["replaced_by_item_id"], ["meal_items.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_meal_items_id"), "meal_items", ["id"])
    op.create_index(op.f("ix_meal_items_meal_id"), "meal_items", ["meal_id"])
    op.create_index(
        op.f("ix_meal_items_personal_food_id"), "meal_items", ["personal_food_id"]
    )

    op.create_table(
        "record_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("meal_id", sa.Integer(), nullable=True),
        sa.Column("food_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("undoes_event_id", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["meal_id"], ["meals.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["food_id"], ["personal_foods.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["undoes_event_id"], ["record_events.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_record_events_id"), "record_events", ["id"])
    op.create_index(op.f("ix_record_events_user_id"), "record_events", ["user_id"])
    op.create_index(op.f("ix_record_events_meal_id"), "record_events", ["meal_id"])
    op.create_index(op.f("ix_record_events_food_id"), "record_events", ["food_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_record_events_food_id"), table_name="record_events")
    op.drop_index(op.f("ix_record_events_meal_id"), table_name="record_events")
    op.drop_index(op.f("ix_record_events_user_id"), table_name="record_events")
    op.drop_index(op.f("ix_record_events_id"), table_name="record_events")
    op.drop_table("record_events")

    op.drop_index(op.f("ix_meal_items_personal_food_id"), table_name="meal_items")
    op.drop_index(op.f("ix_meal_items_meal_id"), table_name="meal_items")
    op.drop_index(op.f("ix_meal_items_id"), table_name="meal_items")
    op.drop_table("meal_items")

    op.drop_column("personal_foods", "numbers_by_user")
    op.drop_column("personal_foods", "numbers_quality")
    op.drop_column("personal_foods", "numbers_basis")
    op.drop_column("personal_foods", "usual_grams_by_user")
    op.drop_column("personal_foods", "usual_grams")

    op.drop_index("ix_meals_user_id_meal_time", table_name="meals")
    op.drop_column("meals", "rev")
    op.drop_column("meals", "deleted_at")
    op.drop_column("meals", "plate_totals")
    op.drop_column("meals", "numbers_basis")
    op.drop_column("meals", "time_phrase")
    op.drop_column("meals", "time_source")
    op.drop_column("meals", "time_precision")
    op.drop_column("meals", "sent_at")
