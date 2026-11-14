"""The 🍲 tidy-up as a widget: which form a batch's message went out in

Revision ID: a7c1e9d3b5f2
Revises: c3d7f9a1b5e2
Create Date: 2026-11-15

Additive for old containers: a nullable column they never read or write (null = text,
as every batch so far).
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "a7c1e9d3b5f2"
down_revision: Union[str, None] = "c3d7f9a1b5e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("dish_tidy_batches", sa.Column("form", sa.String(8), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("dish_tidy_batches") as batch:
        batch.drop_column("form")
