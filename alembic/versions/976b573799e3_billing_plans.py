"""billing plans

Revision ID: 976b573799e3
Revises: a80d8bb596a1
Create Date: 2026-07-01 04:46:29.039953

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '976b573799e3'
down_revision: Union[str, Sequence[str], None] = 'a80d8bb596a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
