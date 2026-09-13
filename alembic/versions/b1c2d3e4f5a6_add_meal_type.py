"""add meals.meal_type

Revision ID: b1c2d3e4f5a6
Revises: a0b1c2d3e4f5
Create Date: 2026-09-13

The bot has always asked "Когда был прием пищи?" and offered Завтрак / Обед /
Ужин. The answer was written to ``context.user_data["meal_type"]`` and read
nowhere: there was no column, no schema field, and no mention in the reply. Every
meal logged as breakfast/lunch/dinner threw that classification away.

This adds the column so the answer is kept. Nullable and additive — existing rows
are unaffected, and "Сейчас" (the default path) stores NULL rather than inventing
a label.

Deliberately NOT done here: deriving a clock time from the label. Picking
"Завтрак" still records ``meal_time`` as now, because guessing 08:00 would invent
a timestamp the user never gave. The label is what they actually said.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "a0b1c2d3e4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("meals", sa.Column("meal_type", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("meals", "meal_type")
