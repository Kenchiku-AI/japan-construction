"""add is_source_owner

Revision ID: c1b180f8b2f4
Revises: d3066a8992e1
Create Date: 2026-09-08 05:26:17.893377

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1b180f8b2f4'
down_revision: Union[str, Sequence[str], None] = 'd3066a8992e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
  op.add_column(
    "custom_relationship_definitions",
    sa.Column(
      "is_source_owner",
      sa.Boolean(),
      nullable=False,
      server_default=sa.false(),
    ),
  )


def downgrade():
  op.drop_column(
    "custom_relationship_definitions",
    "is_source_owner",
  )
