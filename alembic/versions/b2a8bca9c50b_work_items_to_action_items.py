"""work items to action items

Revision ID: b2a8bca9c50b
Revises: dae4a91480e6
Create Date: 2026-07-04 14:00:21.158330

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b2a8bca9c50b'
down_revision: Union[str, Sequence[str], None] = 'dae4a91480e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Rename the table
    op.rename_table("work_items", "action_items")

    # Rename the enum type
    op.execute("ALTER TYPE workitemstatus RENAME TO actionitemstatus")

    # Rename indexes
    op.execute("ALTER INDEX ix_work_items_id RENAME TO ix_action_items_id")
    op.execute("ALTER INDEX ix_work_items_project_id RENAME TO ix_action_items_project_id")
    op.execute("ALTER INDEX ix_work_items_assignee_id RENAME TO ix_action_items_assignee_id")

    # Add triggered_action_item_id to line_messages
    op.add_column("line_messages", sa.Column("triggered_action_item_id", sa.UUID(), nullable=True))
    op.create_index("ix_line_messages_triggered_action_item_id", "line_messages", ["triggered_action_item_id"], unique=False)
    op.create_foreign_key(
        "line_messages_triggered_action_item_id_fkey",
        "line_messages",
        "action_items",
        ["triggered_action_item_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Drop old triggered_work_item_id column from line_messages
    op.drop_constraint("line_messages_triggered_work_item_id_fkey", "line_messages", type_="foreignkey")
    op.drop_index("ix_line_messages_triggered_work_item_id", table_name="line_messages")
    op.drop_column("line_messages", "triggered_work_item_id")


def downgrade() -> None:
    # Restore triggered_work_item_id on line_messages
    op.add_column("line_messages", sa.Column("triggered_work_item_id", sa.UUID(), nullable=True))
    op.create_index("ix_line_messages_triggered_work_item_id", "line_messages", ["triggered_work_item_id"], unique=False)
    op.create_foreign_key(
        "line_messages_triggered_work_item_id_fkey",
        "line_messages",
        "work_items",
        ["triggered_work_item_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Drop triggered_action_item_id from line_messages
    op.drop_constraint("line_messages_triggered_action_item_id_fkey", "line_messages", type_="foreignkey")
    op.drop_index("ix_line_messages_triggered_action_item_id", table_name="line_messages")
    op.drop_column("line_messages", "triggered_action_item_id")

    # Drop source_message_text from action_items
    op.drop_column("action_items", "source_message_text")

    # Rename indexes back
    op.execute("ALTER INDEX ix_action_items_id RENAME TO ix_work_items_id")
    op.execute("ALTER INDEX ix_action_items_project_id RENAME TO ix_work_items_project_id")
    op.execute("ALTER INDEX ix_action_items_assignee_id RENAME TO ix_work_items_assignee_id")

    # Rename enum type back
    op.execute("ALTER TYPE actionitemstatus RENAME TO workitemstatus")

    # Rename table back
    op.rename_table("action_items", "work_items")