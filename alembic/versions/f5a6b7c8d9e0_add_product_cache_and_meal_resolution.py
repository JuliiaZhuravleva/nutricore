"""add product_cache table and meal resolution columns

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-07-07

DB infra for the photo-product-lookup feature (A1):

1. product_cache — caches OFF (Open Food Facts) product lookups keyed by barcode
   (EAN/UPC).  Avoids re-hitting the OFF API on repeat scans of the same product.
   Stores per-100g КБЖУ + the raw OFF JSON.  created_at NOT NULL + server_default
   (TD-006 lesson).

2. meals.resolution_source — which pipeline strategy produced the final numbers:
   "barcode_off" | "name_off" | "label_ocr" | "vision" | NULL (legacy/unknown).

3. meals.resolution_signals — JSON dict of key intermediate values for transparency
   and misprediction analysis (e.g. barcode_raw, product_name, portion_grams,
   confidence_tier, lookup_latency_ms).  NOT a flat enum — carries enough signal
   for A5's reply badge and A7's regression checks (per A1 spec).

Both meals columns are nullable and additive — existing rows are unaffected.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f5a6b7c8d9e0"
down_revision: Union[str, None] = "e4f5a6b7c8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. product_cache
    # ------------------------------------------------------------------
    op.create_table(
        "product_cache",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("barcode", sa.String(), nullable=False),
        sa.Column("off_code", sa.String(), nullable=True),
        sa.Column("product_name", sa.Text(), nullable=True),
        sa.Column("brand", sa.Text(), nullable=True),
        sa.Column("calories_per_100g", sa.Float(), nullable=True),
        sa.Column("proteins_per_100g", sa.Float(), nullable=True),
        sa.Column("fats_per_100g", sa.Float(), nullable=True),
        sa.Column("carbohydrates_per_100g", sa.Float(), nullable=True),
        sa.Column("raw_data", sa.JSON(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_product_cache_id", "product_cache", ["id"])
    # Unique index on barcode — primary lookup key; unique=True also enforces the
    # no-duplicate-barcode constraint without a separate UniqueConstraint.
    op.create_index(
        "ix_product_cache_barcode", "product_cache", ["barcode"], unique=True
    )
    op.create_index("ix_product_cache_created_at", "product_cache", ["created_at"])

    # ------------------------------------------------------------------
    # 2. meals — resolution tracking columns (nullable, additive)
    # ------------------------------------------------------------------
    op.add_column(
        "meals",
        sa.Column("resolution_source", sa.String(), nullable=True),
    )
    op.add_column(
        "meals",
        sa.Column("resolution_signals", sa.JSON(), nullable=True),
    )
    op.create_index(
        "ix_meals_resolution_source", "meals", ["resolution_source"]
    )


def downgrade() -> None:
    op.drop_index("ix_meals_resolution_source", table_name="meals")
    op.drop_column("meals", "resolution_signals")
    op.drop_column("meals", "resolution_source")

    op.drop_index("ix_product_cache_created_at", table_name="product_cache")
    op.drop_index("ix_product_cache_barcode", table_name="product_cache")  # unique
    op.drop_index("ix_product_cache_id", table_name="product_cache")
    op.drop_table("product_cache")
