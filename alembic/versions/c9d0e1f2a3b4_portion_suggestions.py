"""a dish can hold a guessed portion, and an item can say its weight was a default

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-11-01

``personal_foods.suggested_grams`` and ``suggested_unit``: a model's guess at a serving
for a dish nobody weighed. ``meal_items.base_grams_assumed``: this item's base_grams is a
default, so the next portion of its dish does not read it as a real weight.

Additive for old containers: nullable or with a server default, and they never read
them. The downgrade loses only the guesses and the flags.

One backfill: a past product read with no weight says "100г", meaning «per 100 g», and
that is no portion. Those items get the flag, as new ones do, or a juice scanned before
this release would stay at a real 100 g and scripts/suggest_portions.py would never reach
it. The rule is meal_items._no_weight_read, for an item nobody changed since. Any other
past item keeps reading as the real weight it has always been.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# meal_items._PRODUCT_SOURCES on this day: a migration does not follow the code.
_PRODUCT_SOURCES = ("saved_rag", "barcode_off", "name_off", "label_ocr", "name_web")


def upgrade() -> None:
    with op.batch_alter_table("personal_foods") as batch:
        batch.add_column(sa.Column("suggested_grams", sa.Float(), nullable=True))
        batch.add_column(sa.Column("suggested_unit", sa.String(), nullable=True))
    with op.batch_alter_table("meal_items") as batch:
        batch.add_column(
            sa.Column(
                "base_grams_assumed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
    meals = sa.table(
        "meals",
        sa.column("id", sa.Integer),
        sa.column("resolution_source", sa.String),
        sa.column("resolution_signals", sa.JSON),
    )
    items = sa.table(
        "meal_items",
        sa.column("meal_id", sa.Integer),
        sa.column("base_grams", sa.Float),
        sa.column("grams", sa.Float),
        sa.column("factor", sa.Float),
        sa.column("by_user", sa.Boolean),
        sa.column("base_grams_assumed", sa.Boolean),
    )
    op.execute(
        items.update()
        .where(
            items.c.base_grams == 100,
            items.c.grams.is_(None),
            items.c.factor == 1,
            items.c.by_user.is_(False),
            sa.exists().where(
                meals.c.id == items.c.meal_id,
                meals.c.resolution_source.in_(_PRODUCT_SOURCES),
                meals.c.resolution_signals["portion_grams"].as_string().is_(None),
            ),
        )
        .values(base_grams_assumed=sa.true())
    )


def downgrade() -> None:
    with op.batch_alter_table("meal_items") as batch:
        batch.drop_column("base_grams_assumed")
    with op.batch_alter_table("personal_foods") as batch:
        batch.drop_column("suggested_unit")
        batch.drop_column("suggested_grams")
