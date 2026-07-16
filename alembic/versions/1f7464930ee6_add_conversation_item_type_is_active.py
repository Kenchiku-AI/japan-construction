"""add conversation item type is_active

Revision ID: 1f7464930ee6
Revises: 14670e1e3493
Create Date: 2026-07-16 13:22:20.302373

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1f7464930ee6'
down_revision: Union[str, Sequence[str], None] = '14670e1e3493'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.add_column(
        'conversation_item_types',
        sa.Column(
            'is_active',
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )

    op.alter_column(
        'conversation_item_types',
        'is_active',
        server_default=None,
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_column(
        'conversation_item_types',
        'is_active',
    )