"""fix result json

Revision ID: 214981cc221c
Revises: 90c8c365f69f
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "214981cc221c"
down_revision = "90c8c365f69f"
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.alter_column(
    "form_jobs",
    "result_json",
    existing_type=sa.Text(),
    type_=postgresql.JSONB(),
    postgresql_using="result_json::jsonb",
  )


def downgrade() -> None:
  op.alter_column(
    "form_jobs",
    "result_json",
    existing_type=postgresql.JSONB(),
    type_=sa.Text(),
    postgresql_using="result_json::text",
  )