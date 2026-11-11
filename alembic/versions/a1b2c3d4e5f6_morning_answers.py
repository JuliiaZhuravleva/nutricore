"""☀️ the morning question about yesterday's records (stage 6, D10-D14)

Revision ID: a1b2c3d4e5f6
Revises: 48df529b50ac
Create Date: 2026-11-11

Additive for old containers: one new table, which they never read. The downgrade loses
the answers (which days were marked «почти нет»); the meals and the on/off switch in
``users.settings`` stay.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "48df529b50ac"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "morning_answers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("answer", sa.String(8), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("user_id", "day"),
    )
    op.create_index("ix_morning_answers_id", "morning_answers", ["id"])


def downgrade() -> None:
    op.drop_index("ix_morning_answers_id", table_name="morning_answers")
    op.drop_table("morning_answers")
