"""what people wrote to the owner, kept before any of it is sent

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-11-02

Additive for old containers: one new table, which they never read. The downgrade loses
what people wrote, delivered or not; nothing in the diary depends on it.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feedback",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("file_id", sa.String(255), nullable=True),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.Column("journal", sa.Text(), nullable=True),
        sa.Column("original", sa.JSON(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("was_food_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
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
    )
    op.create_index("ix_feedback_id", "feedback", ["id"])
    op.create_index("ix_feedback_telegram_id", "feedback", ["telegram_id"])


def downgrade() -> None:
    op.drop_index("ix_feedback_telegram_id", table_name="feedback")
    op.drop_index("ix_feedback_id", table_name="feedback")
    op.drop_table("feedback")
