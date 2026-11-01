"""add meal_sources, meal_readings, meal_messages and the capture columns

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-10-31

Stage 5, slice M: a message is in the diary before anything is read. ``meal_sources``
keeps what was sent, ``meal_readings`` what each reading said, ``meal_messages`` which
meal a message in the chat is about. ``meals`` learns its reading state, whether it is
a draft and what it was merged into; ``meal_items`` learns the source it came from.

Additive: migrate runs before start, so old containers may meet this schema. Every new
column is nullable or has a server default, and the server default is the backfill: an
old meal reads as already read, so it needs no UPDATE.

``meal_sources.meal_id`` is nullable: a stop word or a text with no food keeps its
source and has no meal. ``meal_items.source_id`` is a plain integer, as the other
fields that make an undo possible.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamps() -> list:
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


def upgrade() -> None:
    op.add_column(
        "meals",
        sa.Column("reading_status", sa.String(), nullable=False, server_default="read"),
    )
    op.add_column(
        "meals",
        sa.Column("read_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "meals",
        sa.Column("is_draft", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    with op.batch_alter_table("meals") as batch:
        batch.add_column(sa.Column("merged_into_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_meals_merged_into_id_meals", "meals", ["merged_into_id"], ["id"]
        )
    op.add_column("meal_items", sa.Column("source_id", sa.Integer(), nullable=True))

    op.create_table(
        "meal_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("meal_id", sa.Integer(), sa.ForeignKey("meals.id"), nullable=True),
        sa.Column("origin_meal_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("media_group_id", sa.String(), nullable=True),
        sa.Column("reply_to_message_id", sa.Integer(), nullable=True),
        sa.Column("forward_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("text", sa.String(), nullable=True),
        sa.Column("file_id", sa.String(), nullable=True),
        sa.Column("file_unique_id", sa.String(), nullable=True),
        sa.Column("duration_s", sa.Integer(), nullable=True),
        sa.Column("audio_path", sa.String(), nullable=True),
        sa.Column("supersedes_id", sa.Integer(), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint(
            "chat_id", "message_id", "kind", name="uq_meal_sources_message"
        ),
    )
    op.create_index(op.f("ix_meal_sources_id"), "meal_sources", ["id"])
    op.create_index(op.f("ix_meal_sources_user_id"), "meal_sources", ["user_id"])
    op.create_index(op.f("ix_meal_sources_meal_id"), "meal_sources", ["meal_id"])
    op.create_index(
        op.f("ix_meal_sources_media_group_id"), "meal_sources", ["media_group_id"]
    )

    op.create_table(
        "meal_readings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("meal_id", sa.Integer(), sa.ForeignKey("meals.id"), nullable=False),
        sa.Column("source_ids", sa.JSON(), nullable=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("backend", sa.String(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("prompt_version", sa.String(), nullable=True),
        sa.Column("output", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("applied", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_timestamps(),
    )
    op.create_index(op.f("ix_meal_readings_id"), "meal_readings", ["id"])
    op.create_index(op.f("ix_meal_readings_meal_id"), "meal_readings", ["meal_id"])

    op.create_table(
        "meal_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("meal_id", sa.Integer(), sa.ForeignKey("meals.id"), nullable=False),
        sa.Column("view", sa.String(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("chat_id", "message_id", name="uq_meal_messages_message"),
    )
    op.create_index(op.f("ix_meal_messages_id"), "meal_messages", ["id"])
    op.create_index(op.f("ix_meal_messages_meal_id"), "meal_messages", ["meal_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_meal_messages_meal_id"), table_name="meal_messages")
    op.drop_index(op.f("ix_meal_messages_id"), table_name="meal_messages")
    op.drop_table("meal_messages")
    op.drop_index(op.f("ix_meal_readings_meal_id"), table_name="meal_readings")
    op.drop_index(op.f("ix_meal_readings_id"), table_name="meal_readings")
    op.drop_table("meal_readings")
    op.drop_index(op.f("ix_meal_sources_media_group_id"), table_name="meal_sources")
    op.drop_index(op.f("ix_meal_sources_meal_id"), table_name="meal_sources")
    op.drop_index(op.f("ix_meal_sources_user_id"), table_name="meal_sources")
    op.drop_index(op.f("ix_meal_sources_id"), table_name="meal_sources")
    op.drop_table("meal_sources")
    op.drop_column("meal_items", "source_id")
    with op.batch_alter_table("meals") as batch:
        batch.drop_constraint("fk_meals_merged_into_id_meals", type_="foreignkey")
        batch.drop_column("merged_into_id")
    op.drop_column("meals", "is_draft")
    op.drop_column("meals", "read_attempts")
    op.drop_column("meals", "reading_status")
