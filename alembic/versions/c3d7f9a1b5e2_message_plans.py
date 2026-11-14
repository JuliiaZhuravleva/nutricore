"""Several actions in one message: the plan of a message, and the parts of its meals

Revision ID: c3d7f9a1b5e2
Revises: e2f6b8c1d3a5
Create Date: 2026-11-14

Additive for old containers: a new table they never read, and a column with a default
they never write (every row they insert is part 0, the message itself, as today). The
unique key of ``meal_sources`` gains ``part``, so one message may hold several meals.

The downgrade keeps every message's first meal source and deletes the later parts the
old key cannot hold; their meals stay, each without the source row that names its
words.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "c3d7f9a1b5e2"
down_revision: Union[str, None] = "e2f6b8c1d3a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_WHERE = sa.text("supersedes_id IS NULL")
_KEY = "uq_meal_sources_message"


def _stamps() -> list:
    return [
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
    ]


def _key(columns: list) -> None:
    op.create_index(
        _KEY,
        "meal_sources",
        columns,
        unique=True,
        sqlite_where=_WHERE,
        postgresql_where=_WHERE,
    )


def upgrade() -> None:
    op.create_table(
        "message_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reply_to_message_id", sa.BigInteger(), nullable=True),
        sa.Column("forward_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("want", sa.String(8), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("commands", sa.JSON(), nullable=True),
        sa.Column("meal_ids", sa.JSON(), nullable=True),
        sa.Column("line_ids", sa.JSON(), nullable=True),
        sa.Column("group_id", sa.String(36), nullable=True),
        sa.Column("line_message_id", sa.BigInteger(), nullable=True),
        sa.Column("result_message_id", sa.BigInteger(), nullable=True),
        *_stamps(),
        sa.UniqueConstraint("chat_id", "message_id", name="uq_message_plans_message"),
    )
    op.create_index("ix_message_plans_id", "message_plans", ["id"])
    op.create_index("ix_message_plans_user_id", "message_plans", ["user_id"])
    op.add_column(
        "meal_sources",
        sa.Column("part", sa.SmallInteger(), nullable=False, server_default="0"),
    )
    op.drop_index(_KEY, table_name="meal_sources")
    _key(["chat_id", "message_id", "kind", "part"])


def downgrade() -> None:
    # Only the later parts the old key cannot hold: a part with an earlier live part
    # of the same message beside it.
    op.execute(
        "DELETE FROM meal_sources WHERE part > 0 AND supersedes_id IS NULL AND EXISTS ("
        "SELECT 1 FROM meal_sources AS o WHERE o.chat_id = meal_sources.chat_id "
        "AND o.message_id = meal_sources.message_id AND o.kind = meal_sources.kind "
        "AND o.supersedes_id IS NULL AND o.part < meal_sources.part)"
    )
    op.drop_index(_KEY, table_name="meal_sources")
    with op.batch_alter_table("meal_sources") as batch:
        batch.drop_column("part")
    _key(["chat_id", "message_id", "kind"])
    op.drop_index("ix_message_plans_user_id", table_name="message_plans")
    op.drop_index("ix_message_plans_id", table_name="message_plans")
    op.drop_table("message_plans")
