"""add routine_ticks

Revision ID: a9b8c7d6e5f4
Revises: c3d4e5f6a7b8
Create Date: 2026-10-30

Stage 5, slice C: one row per tick of a user's own routine. The routines live in
``User.settings``. Additive — a new table only; no existing row changes.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9b8c7d6e5f4"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "routine_ticks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("routine_id", sa.String(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "routine_id", "day"),
    )
    op.create_index(op.f("ix_routine_ticks_id"), "routine_ticks", ["id"])
    op.create_index(op.f("ix_routine_ticks_user_id"), "routine_ticks", ["user_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_routine_ticks_user_id"), table_name="routine_ticks")
    op.drop_index(op.f("ix_routine_ticks_id"), table_name="routine_ticks")
    op.drop_table("routine_ticks")
