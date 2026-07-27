"""line message attachments

Revision ID: 937ff52a491b
Revises: 67260481971b
Create Date: 2026-07-26 15:05:02.611216

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '937ff52a491b'
down_revision: Union[str, Sequence[str], None] = '67260481971b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    bind = op.get_bind()

    line_message_type = postgresql.ENUM(
        'text',
        'image',
        'video',
        'audio',
        'file',
        name='linemessagetype',
        create_type=False,
    )

    line_message_attachment_type = postgresql.ENUM(
        'image',
        'video',
        'audio',
        'file',
        name='linemessageattachmenttype',
        create_type=False,
    )

    line_message_type.create(
        bind,
        checkfirst=True,
    )

    line_message_attachment_type.create(
        bind,
        checkfirst=True,
    )

    op.create_table(
        'line_message_attachments',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('line_message_id', sa.UUID(), nullable=False),
        sa.Column(
            'type',
            line_message_attachment_type,
            nullable=False,
        ),
        sa.Column('s3_key', sa.String(), nullable=False),
        sa.Column('mime_type', sa.String(), nullable=True),
        sa.Column('file_size', sa.Integer(), nullable=True),
        sa.Column('width', sa.Integer(), nullable=True),
        sa.Column('height', sa.Integer(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ['line_message_id'],
            ['line_messages.id'],
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_index(
        op.f('ix_line_message_attachments_line_message_id'),
        'line_message_attachments',
        ['line_message_id'],
        unique=False,
    )

    # Add new columns as nullable first
    op.add_column(
        'line_messages',
        sa.Column(
            'message_type',
            line_message_type,
            nullable=True,
        ),
    )

    op.add_column(
        'line_messages',
        sa.Column(
            'line_platform_message_id',
            sa.String(),
            nullable=True,
        ),
    )

    # Existing messages were text messages
    op.execute(
        """
        UPDATE line_messages
        SET message_type = 'text'
        WHERE message_type IS NULL
        """
    )

    # Existing rows don't have LINE IDs.
    # Use internal UUID as placeholder.
    op.execute(
        """
        UPDATE line_messages
        SET line_platform_message_id = id::text
        WHERE line_platform_message_id IS NULL
        """
    )

    op.alter_column(
        'line_messages',
        'message_type',
        nullable=False,
    )

    op.alter_column(
        'line_messages',
        'line_platform_message_id',
        nullable=False,
    )

    op.alter_column(
        'line_messages',
        'text',
        existing_type=sa.VARCHAR(),
        nullable=True,
    )

    op.create_index(
        op.f('ix_line_messages_line_platform_message_id'),
        'line_messages',
        ['line_platform_message_id'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""

    op.drop_index(
        op.f('ix_line_messages_line_platform_message_id'),
        table_name='line_messages',
    )

    op.alter_column(
        'line_messages',
        'text',
        existing_type=sa.VARCHAR(),
        nullable=False,
    )

    op.drop_column(
        'line_messages',
        'line_platform_message_id',
    )

    op.drop_column(
        'line_messages',
        'message_type',
    )

    op.drop_index(
        op.f('ix_line_message_attachments_line_message_id'),
        table_name='line_message_attachments',
    )

    op.drop_table(
        'line_message_attachments',
    )

    bind = op.get_bind()

    line_message_attachment_type = postgresql.ENUM(
        name='linemessageattachmenttype',
    )

    line_message_attachment_type.drop(
        bind,
        checkfirst=True,
    )

    line_message_type = postgresql.ENUM(
        name='linemessagetype',
    )

    line_message_type.drop(
        bind,
        checkfirst=True,
    )