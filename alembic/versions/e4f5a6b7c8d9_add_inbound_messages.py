"""add inbound_messages table

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-07-06

Persist-on-receipt of meal messages (TD-009): a raw record written the moment a
photo/text arrives, BEFORE the OpenAI call, so a failed or dropped analysis
leaves a replayable trace. status pending -> analyzed (parsed nutrition) / failed
(error). telegram_id is a plain BigInteger (no FK). created_at is NOT NULL +
server_default so a NULL-dated row can't escape the retention purge (TD-006).
Pruned by a daily Celery-beat job per INBOUND_MESSAGE_RETENTION_DAYS.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, None] = "d3e4f5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "inbound_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("photo_file_id", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), server_default="pending", nullable=False),
        sa.Column("ai_analysis", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_inbound_messages_id", "inbound_messages", ["id"])
    op.create_index(
        "ix_inbound_messages_telegram_id", "inbound_messages", ["telegram_id"]
    )
    op.create_index("ix_inbound_messages_status", "inbound_messages", ["status"])
    op.create_index(
        "ix_inbound_messages_created_at", "inbound_messages", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_inbound_messages_created_at", table_name="inbound_messages")
    op.drop_index("ix_inbound_messages_status", table_name="inbound_messages")
    op.drop_index("ix_inbound_messages_telegram_id", table_name="inbound_messages")
    op.drop_index("ix_inbound_messages_id", table_name="inbound_messages")
    op.drop_table("inbound_messages")
