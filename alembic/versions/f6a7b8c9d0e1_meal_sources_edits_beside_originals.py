"""an edited message keeps a new source beside the one that came

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-10-31

The unique key on ``meal_sources`` is what stops a replayed update making a second
meal. M11 stores an edit as a new row with ``supersedes_id``, under the same chat,
message and kind, so the key now holds for the rows that came, and not for their edits.

Additive for old containers: they write only originals, which the key still holds.
The downgrade drops the edits' rows (the original words stay), because the old key
cannot stand beside them.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_WHERE = sa.text("supersedes_id IS NULL")


def upgrade() -> None:
    with op.batch_alter_table("meal_sources") as batch:
        batch.drop_constraint("uq_meal_sources_message", type_="unique")
    op.create_index(
        "uq_meal_sources_message",
        "meal_sources",
        ["chat_id", "message_id", "kind"],
        unique=True,
        sqlite_where=_WHERE,
        postgresql_where=_WHERE,
    )


def downgrade() -> None:
    op.execute("DELETE FROM meal_sources WHERE supersedes_id IS NOT NULL")
    op.drop_index("uq_meal_sources_message", table_name="meal_sources")
    with op.batch_alter_table("meal_sources") as batch:
        batch.create_unique_constraint(
            "uq_meal_sources_message", ["chat_id", "message_id", "kind"]
        )
