"""data type

Revision ID: 18816542c564
Revises: 39accf019af9
Create Date: 2026-08-18 18:38:13.120456

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '18816542c564'
down_revision: Union[str, Sequence[str], None] = '39accf019af9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
  """Upgrade schema."""
  op.add_column(
    'custom_field_definitions',
    sa.Column(
      'data_type',
      sa.String(),
      nullable=True,
    ),
  )

  op.execute(
    """
    UPDATE custom_field_definitions
    SET data_type = 'text'
    WHERE data_type IS NULL
    """
  )

  op.alter_column(
    'custom_field_definitions',
    'data_type',
    nullable=False,
  )


def downgrade() -> None:
  """Downgrade schema."""
  op.drop_column(
    'custom_field_definitions',
    'data_type',
  )