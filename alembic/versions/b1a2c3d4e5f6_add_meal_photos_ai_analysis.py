"""add photos and ai_analysis to meals

Revision ID: b1a2c3d4e5f6
Revises: eadb499c760d
Create Date: 2026-07-02

The Meal model and MealCreate/Update schemas already declared `photos` and
`ai_analysis`, but the columns were never added to the table — so
`crud_meal.create` raised at runtime. This migration adds them (nullable JSON).
`ai_analysis` retains the raw OpenAI food analysis for later coaching features.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b1a2c3d4e5f6"
down_revision: Union[str, None] = "eadb499c760d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("meals", sa.Column("photos", sa.JSON(), nullable=True))
    op.add_column("meals", sa.Column("ai_analysis", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("meals", "ai_analysis")
    op.drop_column("meals", "photos")
