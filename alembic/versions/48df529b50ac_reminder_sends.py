"""🔔 reminders: one row per reminder, claimed before it is sent (stage 6, R1)

Revision ID: 48df529b50ac
Revises: e7158dcd97a2
Create Date: 2026-11-06

Additive for old containers: one new table, which they never read. The downgrade loses
which reminders went out and what came of them; the meals logged from them stay, and the
pause lives in ``users.settings``.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "48df529b50ac"
down_revision: Union[str, None] = "e7158dcd97a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "reminder_sends",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("local_day", sa.Date(), nullable=False),
        sa.Column("slot", sa.String(5), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("centre", sa.String(5), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("answer", sa.String(8), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("user_id", "local_day", "slot"),
    )
    op.create_index("ix_reminder_sends_id", "reminder_sends", ["id"])
    op.create_index(
        "ix_reminder_sends_user_sent", "reminder_sends", ["user_id", "sent_at"]
    )
    op.create_index(
        "ix_reminder_sends_chat_message", "reminder_sends", ["chat_id", "message_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_reminder_sends_chat_message", table_name="reminder_sends")
    op.drop_index("ix_reminder_sends_user_sent", table_name="reminder_sends")
    op.drop_index("ix_reminder_sends_id", table_name="reminder_sends")
    op.drop_table("reminder_sends")
