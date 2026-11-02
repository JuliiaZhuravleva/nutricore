"""when a person whose door was closed was told so, so they are told once

Revision ID: e7158dcd97a2
Revises: f2a3b4c5d6e7
Create Date: 2026-11-02

Additive for old containers: one nullable column they never read. The downgrade loses only
who has already heard the line, and they may hear it once more.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "e7158dcd97a2"
down_revision: Union[str, None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "access_grants",
        sa.Column("closed_told_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("access_grants", "closed_told_at")
