"""add ai_call_logs debug table

Revision ID: c2d3e4f5a6b7
Revises: b1a2c3d4e5f6
Create Date: 2026-07-06

Debug log of food-analysis OpenAI calls (input reference, model, raw + parsed
response, status, latency). Pruned by a daily Celery-beat job per
DEBUG_LOG_RETENTION_DAYS. telegram_id is a plain BigInteger (no FK) so writing a
log never needs a user lookup and can't overflow a 32-bit int.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, None] = "b1a2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_call_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("input_ref", sa.Text(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("raw_response", sa.Text(), nullable=True),
        sa.Column("parsed_result", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_call_logs_id", "ai_call_logs", ["id"])
    op.create_index("ix_ai_call_logs_telegram_id", "ai_call_logs", ["telegram_id"])
    op.create_index("ix_ai_call_logs_created_at", "ai_call_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_call_logs_created_at", table_name="ai_call_logs")
    op.drop_index("ix_ai_call_logs_telegram_id", table_name="ai_call_logs")
    op.drop_index("ix_ai_call_logs_id", table_name="ai_call_logs")
    op.drop_table("ai_call_logs")
