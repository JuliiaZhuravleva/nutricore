"""the people the owner let in by hand: a list in the database, next to the settings'

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-11-02

Additive for old containers: one new table, which they never read. The downgrade loses
who was invited; their subscriptions and records stay, and the list in the settings goes
on working. Anyone let in only by a grant is then back outside the gate.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, None] = "d0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "access_grants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("granted_by", sa.BigInteger(), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_opened_at", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index("ix_access_grants_id", "access_grants", ["id"])
    op.create_index(
        "ix_access_grants_telegram_id", "access_grants", ["telegram_id"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_access_grants_telegram_id", table_name="access_grants")
    op.drop_index("ix_access_grants_id", table_name="access_grants")
    op.drop_table("access_grants")
