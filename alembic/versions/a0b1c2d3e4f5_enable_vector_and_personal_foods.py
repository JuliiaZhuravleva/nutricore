"""enable pgvector extension and create personal_foods tables

Revision ID: a0b1c2d3e4f5
Revises: f5a6b7c8d9e0
Create Date: 2026-07-10

Personal Food DB (B1): enables the pgvector extension and creates two tables
for semantic food-memory lookup (ADR-0003).

Prerequisites (openclaw-setup):
  Swap the DB image to pgvector/pgvector:pg15 BEFORE running this migration.
  See docs/decisions/ADR-0003-vector-store-personal-food-db.md §Deploy delta.

1. CREATE EXTENSION vector  — enables pgvector on the Postgres database.

2. personal_foods  — canonical food record per user.  One row per confirmed food.
   Dedup key: (user_id, lower(canonical_name)) — case-insensitive, one entry per
   food name per user.  Includes a nullable barcode column (fast exact short-circuit
   in B3/SavedFoodRAGStrategy §4c).  Provenance columns track which pipeline stage
   and meal confirmed this food (for misprediction analysis).
   created_at NOT NULL + server_default (TD-006 lesson).

3. personal_food_embeddings  — one row per embedded text (canonical name + aliases),
   all pointing to the same personal_food_id.  VECTOR(1536) column with HNSW index
   using cosine distance for ANN search.  ANN queries MUST join with personal_foods
   and filter by user_id (mandatory correctness — see ADR-0003 §3).

Migration-locked: VECTOR(1536) matches OPENAI_EMBEDDING_DIMS default 1536.
Do NOT change the default after this migration runs without a full re-embed.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a0b1c2d3e4f5"
down_revision: Union[str, None] = "f5a6b7c8d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Enable pgvector extension (must run first; requires pg image swap)
    # ------------------------------------------------------------------
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ------------------------------------------------------------------
    # 2. personal_foods
    # ------------------------------------------------------------------
    op.create_table(
        "personal_foods",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("brand", sa.Text(), nullable=True),
        sa.Column("per_100g_calories", sa.Numeric(8, 2), nullable=True),
        sa.Column("per_100g_proteins", sa.Numeric(8, 2), nullable=True),
        sa.Column("per_100g_fats", sa.Numeric(8, 2), nullable=True),
        sa.Column("per_100g_carbs", sa.Numeric(8, 2), nullable=True),
        # Provenance: which meal + pipeline stage confirmed this food
        sa.Column("meal_id", sa.Integer(), nullable=True),
        sa.Column("resolution_source", sa.Text(), nullable=True),
        # Nullable barcode for exact-barcode short-circuit (ADR-0003 §4c / B3)
        sa.Column("barcode", sa.Text(), nullable=True),
        # Usage tracking (for future quick-pick B5)
        sa.Column(
            "times_used",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        # NOT NULL + server_default (TD-006 lesson: rows never land with NULL dates)
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_personal_foods_user_id",
        ),
        sa.ForeignKeyConstraint(
            ["meal_id"],
            ["meals.id"],
            ondelete="SET NULL",
            name="fk_personal_foods_meal_id",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_personal_foods_id", "personal_foods", ["id"])
    op.create_index("ix_personal_foods_user_id", "personal_foods", ["user_id"])

    # Dedup key: case-insensitive unique per (user, food name)
    op.execute(
        "CREATE UNIQUE INDEX personal_foods_user_name_uq "
        "ON personal_foods (user_id, lower(canonical_name))"
    )
    # Partial index for fast barcode lookup (Phase A of SavedFoodRAGStrategy)
    op.execute(
        "CREATE INDEX personal_foods_user_barcode_idx "
        "ON personal_foods (user_id, barcode) "
        "WHERE barcode IS NOT NULL"
    )

    # ------------------------------------------------------------------
    # 3. personal_food_embeddings
    # ------------------------------------------------------------------
    op.create_table(
        "personal_food_embeddings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("personal_food_id", sa.Integer(), nullable=False),
        # The exact text string that was embedded (canonical name or alias)
        sa.Column("text_embedded", sa.Text(), nullable=False),
        # Temporary TEXT placeholder — immediately altered to vector(1536) below
        # (Cannot use vector DDL inside create_table; ALTER avoids the quoting issue)
        sa.Column("embedding", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["personal_food_id"],
            ["personal_foods.id"],
            ondelete="CASCADE",
            name="fk_personal_food_embeddings_food_id",
        ),
        sa.PrimaryKeyConstraint("id"),
        # Authoritative dedup backstop against duplicate vectors under concurrent
        # confirms / task retry (F5). The B4 task also checks app-side.
        sa.UniqueConstraint(
            "personal_food_id",
            "text_embedded",
            name="personal_food_embeddings_food_text_uq",
        ),
    )
    op.create_index(
        "ix_personal_food_embeddings_id", "personal_food_embeddings", ["id"]
    )
    op.create_index(
        "ix_personal_food_embeddings_food_id",
        "personal_food_embeddings",
        ["personal_food_id"],
    )

    # Convert embedding column from TEXT to VECTOR(1536)
    # 1536 = OPENAI_EMBEDDING_DIMS default (migration-locked; see ADR-0003 §2)
    op.execute(
        "ALTER TABLE personal_food_embeddings "
        "ALTER COLUMN embedding TYPE vector(1536) USING embedding::vector(1536)"
    )

    # HNSW index with cosine distance — mandatory user_id join at query time (B3)
    op.execute(
        "CREATE INDEX personal_food_embeddings_hnsw_idx "
        "ON personal_food_embeddings "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS personal_food_embeddings_hnsw_idx")
    op.drop_index(
        "ix_personal_food_embeddings_food_id",
        table_name="personal_food_embeddings",
    )
    op.drop_index(
        "ix_personal_food_embeddings_id", table_name="personal_food_embeddings"
    )
    op.drop_table("personal_food_embeddings")

    op.execute("DROP INDEX IF EXISTS personal_foods_user_barcode_idx")
    op.execute("DROP INDEX IF EXISTS personal_foods_user_name_uq")
    op.drop_index("ix_personal_foods_user_id", table_name="personal_foods")
    op.drop_index("ix_personal_foods_id", table_name="personal_foods")
    op.drop_table("personal_foods")

    # NOTE: We intentionally do NOT drop the vector extension in downgrade —
    # other tables may depend on it, and `DROP EXTENSION` requires CASCADE.
    # Removing the extension is a manual DBA step if truly needed.
