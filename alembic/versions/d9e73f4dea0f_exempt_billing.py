"""exempt billing

Revision ID: d9e73f4dea0f
Revises: c1b180f8b2f4
Create Date: 2026-09-18 23:51:49.127971

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd9e73f4dea0f'
down_revision: Union[str, Sequence[str], None] = 'c1b180f8b2f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column(
            "billing_exempt",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("companies", "billing_exempt")
