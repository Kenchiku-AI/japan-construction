"""fix invitations expires_at timezone

Revision ID: a80d8bb596a1
Revises: 081cd484dcea
Create Date: 2026-06-30 05:37:06.947927

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a80d8bb596a1'
down_revision: Union[str, Sequence[str], None] = '081cd484dcea'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'invitations', 'expires_at',
        existing_type=sa.DateTime(),
        type_=sa.DateTime(timezone=True),
        existing_nullable=False,
        postgresql_using="expires_at AT TIME ZONE 'UTC'",
    )

    op.alter_column(
        'password_reset_tokens', 'expires_at',
        existing_type=sa.DateTime(),
        type_=sa.DateTime(timezone=True),
        existing_nullable=False,
        postgresql_using="expires_at AT TIME ZONE 'UTC'",
    )

def downgrade() -> None:
    op.alter_column(
        'invitations', 'expires_at',
        existing_type=sa.DateTime(timezone=True),
        type_=sa.DateTime(),
        existing_nullable=False,
    )

    op.alter_column(
        'password_reset_tokens', 'expires_at',
        existing_type=sa.DateTime(timezone=True),
        type_=sa.DateTime(),
        existing_nullable=False,
    )