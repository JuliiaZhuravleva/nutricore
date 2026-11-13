"""🍲 Порядок в блюдах: the weekly tidy-up's batches and rows (tidy-up, slice 2)

Revision ID: e2f6b8c1d3a5
Revises: d1e5a7b0c2f4
Create Date: 2026-11-13

Additive for old containers: two new tables, which they never read. The downgrade loses
the batches (what was proposed and declined); the merges and ⭐ themselves stay, each
with its own undo in ``record_events``.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "e2f6b8c1d3a5"
down_revision: Union[str, None] = "d1e5a7b0c2f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _stamps() -> list:
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
    op.create_table(
        "dish_tidy_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("week", sa.Date(), nullable=True),
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(12), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("tries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        *_stamps(),
        sa.UniqueConstraint("user_id", "week"),
    )
    op.create_index("ix_dish_tidy_batches_id", "dish_tidy_batches", ["id"])
    op.create_index("ix_dish_tidy_batches_user_id", "dish_tidy_batches", ["user_id"])
    op.create_table(
        "dish_tidy_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "batch_id",
            sa.Integer(),
            sa.ForeignKey("dish_tidy_batches.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idx", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column(
            "source_id",
            sa.Integer(),
            sa.ForeignKey("personal_foods.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "target_id",
            sa.Integer(),
            sa.ForeignKey("personal_foods.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("key", sa.String(32), nullable=False),
        sa.Column("label", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(16), nullable=False),
        sa.Column("ticked", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "event_id",
            sa.Integer(),
            sa.ForeignKey("record_events.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("state", sa.String(12), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        *_stamps(),
    )
    op.create_index("ix_dish_tidy_items_id", "dish_tidy_items", ["id"])
    op.create_index("ix_dish_tidy_items_batch_id", "dish_tidy_items", ["batch_id"])


def downgrade() -> None:
    op.drop_index("ix_dish_tidy_items_batch_id", table_name="dish_tidy_items")
    op.drop_index("ix_dish_tidy_items_id", table_name="dish_tidy_items")
    op.drop_table("dish_tidy_items")
    op.drop_index("ix_dish_tidy_batches_user_id", table_name="dish_tidy_batches")
    op.drop_index("ix_dish_tidy_batches_id", table_name="dish_tidy_batches")
    op.drop_table("dish_tidy_batches")
