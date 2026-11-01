"""what the models cost per person per day, and a journal of actions with no food in it

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-11-02

``usage_ledger``: one row a person a day, numbers only, kept for good. ``telegram_id`` 0
is the calls nobody can be named for: not NULL, because NULLs are distinct in a Postgres
unique constraint. No foreign key, so a row outlives anything that happens to a user.

``action_events``: when, what kind, and a fixed name from a closed list. Purged at 60
days.

Additive for old containers: two new tables, which they never read. The downgrade loses
the history in both.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "usage_ledger",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd", sa.Numeric(16, 10), nullable=False, server_default="0"),
        sa.Column("unpriced_calls", sa.Integer(), nullable=False, server_default="0"),
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
        sa.UniqueConstraint("telegram_id", "day", name="uq_usage_ledger_person_day"),
    )
    op.create_index("ix_usage_ledger_id", "usage_ledger", ["id"])
    op.create_index("ix_usage_ledger_day", "usage_ledger", ["day"])
    op.create_table(
        "action_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("label", sa.String(), nullable=True),
    )
    op.create_index("ix_action_events_id", "action_events", ["id"])
    op.create_index("ix_action_events_at", "action_events", ["at"])
    op.create_index(
        "ix_action_events_person_at", "action_events", ["telegram_id", "at"]
    )


def downgrade() -> None:
    op.drop_table("action_events")
    op.drop_table("usage_ledger")
