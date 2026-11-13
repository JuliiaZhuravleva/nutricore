"""record_events.group_id: one undo for a batch of dish changes (tidy-up, slice 1)

Revision ID: d1e5a7b0c2f4
Revises: a1b2c3d4e5f6
Create Date: 2026-11-13

Additive for old containers: a nullable column they never write. The downgrade loses
which changes went together; every change keeps its own undo.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "d1e5a7b0c2f4"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "record_events", sa.Column("group_id", sa.String(36), nullable=True)
    )
    op.create_index(
        "ix_record_events_group_id", "record_events", ["group_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_record_events_group_id", table_name="record_events")
    op.drop_column("record_events", "group_id")
