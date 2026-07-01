"""billing plan id

Revision ID: 5cbe570c070f
Revises: 976b573799e3
Create Date: 2026-07-01 06:13:31.132027

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5cbe570c070f'
down_revision: Union[str, Sequence[str], None] = '976b573799e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
