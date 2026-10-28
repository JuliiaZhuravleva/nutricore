"""add favorite_meals

Revision ID: c3d4e5f6a7b8
Revises: b1c2d3e4f5a6
Create Date: 2026-10-28

Favorites: a confirmed meal can be saved ("⭐ Да, и в избранное") and logged again
later with one tap, copying its numbers instead of analysing anything. Additive —
a new table only; no existing row changes.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b1c2d3e4f5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "favorite_meals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("calories", sa.Float(), nullable=True),
        sa.Column("proteins", sa.Float(), nullable=True),
        sa.Column("fats", sa.Float(), nullable=True),
        sa.Column("carbohydrates", sa.Float(), nullable=True),
        sa.Column("nutrients", sa.JSON(), nullable=True),
        sa.Column("source_meal_id", sa.Integer(), nullable=True),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["source_meal_id"], ["meals.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_favorite_meals_id"), "favorite_meals", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_favorite_meals_user_id"), "favorite_meals", ["user_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_favorite_meals_user_id"), table_name="favorite_meals")
    op.drop_index(op.f("ix_favorite_meals_id"), table_name="favorite_meals")
    op.drop_table("favorite_meals")
