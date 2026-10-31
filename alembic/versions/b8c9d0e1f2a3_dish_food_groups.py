"""a dish can hold the groups a person gave it

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-10-31

``personal_foods.food_groups``: NULL says nobody gave a word, so a dish goes on carrying
what its last tagged meal had. A list, an empty one too, is the person's word.

Additive for old containers: nullable, and they never read it. No backfill: nothing was
said yet. The downgrade loses only that word, and the meals keep their own tags.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("personal_foods") as batch:
        batch.add_column(sa.Column("food_groups", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("personal_foods") as batch:
        batch.drop_column("food_groups")
