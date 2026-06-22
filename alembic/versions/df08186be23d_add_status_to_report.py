"""add status to report

Revision ID: df08186be23d
Revises: 39382da18c6d
Create Date: 2026-06-22 18:43:54.087187

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'df08186be23d'
down_revision: Union[str, Sequence[str], None] = '39382da18c6d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    sa.Enum('open', 'closed', name='reportstatus').create(op.get_bind())
    op.add_column('reports', sa.Column(
        'status',
        sa.Enum('open', 'closed', name='reportstatus'),
        nullable=False,
        server_default='open',
    ))
    op.alter_column('reports', 'status', server_default=None)


def downgrade() -> None:
    op.drop_column('reports', 'status')
    sa.Enum(name='reportstatus').drop(op.get_bind())
