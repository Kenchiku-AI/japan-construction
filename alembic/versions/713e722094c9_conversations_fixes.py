"""conversations fixes

Revision ID: 713e722094c9
Revises: 5bac3d33ce35
Create Date: 2026-07-15 00:40:56.546914

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '713e722094c9'
down_revision: Union[str, Sequence[str], None] = '5bac3d33ce35'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    conversation_item_status = sa.Enum(
        'new',
        'in_progress',
        'closed',
        name='conversationitemstatus',
    )

    # Create PostgreSQL enum type
    conversation_item_status.create(
        op.get_bind(),
        checkfirst=True,
    )

    # Add optional project association
    op.add_column(
        'conversation_items',
        sa.Column(
            'project_id',
            sa.UUID(),
            nullable=True,
        ),
    )

    # Add status
    op.add_column(
        'conversation_items',
        sa.Column(
            'status',
            conversation_item_status,
            nullable=False,
            server_default='new',
        ),
    )

    # Conversation is now optional
    op.alter_column(
        'conversation_items',
        'conversation_id',
        existing_type=sa.UUID(),
        nullable=True,
    )

    op.create_index(
        op.f('ix_conversation_items_project_id'),
        'conversation_items',
        ['project_id'],
        unique=False,
    )

    # Replace conversation FK to allow orphan conversation items
    op.drop_constraint(
        op.f('conversation_items_conversation_id_fkey'),
        'conversation_items',
        type_='foreignkey',
    )

    op.create_foreign_key(
        None,
        'conversation_items',
        'projects',
        ['project_id'],
        ['id'],
        ondelete='CASCADE',
    )

    op.create_foreign_key(
        None,
        'conversation_items',
        'line_conversations',
        ['conversation_id'],
        ['id'],
        ondelete='SET NULL',
    )

    # Remove temporary default
    op.alter_column(
        'conversation_items',
        'status',
        server_default=None,
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_constraint(
        None,
        'conversation_items',
        type_='foreignkey',
    )

    op.drop_constraint(
        None,
        'conversation_items',
        type_='foreignkey',
    )

    op.create_foreign_key(
        op.f('conversation_items_conversation_id_fkey'),
        'conversation_items',
        'line_conversations',
        ['conversation_id'],
        ['id'],
        ondelete='CASCADE',
    )

    op.drop_index(
        op.f('ix_conversation_items_project_id'),
        table_name='conversation_items',
    )

    op.alter_column(
        'conversation_items',
        'conversation_id',
        existing_type=sa.UUID(),
        nullable=False,
    )

    op.drop_column(
        'conversation_items',
        'status',
    )

    op.drop_column(
        'conversation_items',
        'project_id',
    )

    sa.Enum(
        'new',
        'in_progress',
        'closed',
        name='conversationitemstatus',
    ).drop(
        op.get_bind(),
        checkfirst=True,
    )