"""add line chat type

Revision ID: e04313f40c98
Revises: 99175e65b8b4
Create Date: 2026-07-19 02:20:31.609819

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e04313f40c98"
down_revision: Union[str, Sequence[str], None] = "99175e65b8b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


line_chat_type = sa.Enum(
    "user",
    "group",
    "room",
    name="linechattype",
)


def upgrade() -> None:
    """Upgrade schema."""

    bind = op.get_bind()

    # Create the PostgreSQL enum type if it doesn't already exist.
    line_chat_type.create(bind, checkfirst=True)

    op.add_column(
        "line_messages",
        sa.Column(
            "line_chat_type",
            line_chat_type,
            nullable=True,
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_column("line_messages", "line_chat_type")

    bind = op.get_bind()

    # Drop the PostgreSQL enum type if nothing else is using it.
    line_chat_type.drop(bind, checkfirst=True)