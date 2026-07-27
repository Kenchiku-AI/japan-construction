"""rename report images

Revision ID: d2e8545c4151
Revises: 937ff52a491b
Create Date: 2026-07-27 15:14:46.826826

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2e8545c4151"
down_revision: Union[str, Sequence[str], None] = "937ff52a491b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Rename tables
    op.rename_table("report_images", "images")
    op.rename_table("report_image_tags", "image_tags")
    op.rename_table("report_image_tag_links", "image_tag_links")

    # Rename FK column
    op.alter_column(
        "image_tag_links",
        "report_image_id",
        new_column_name="image_id",
    )

    # Rename unique constraint
    op.drop_constraint(
        "uq_report_image_tag",
        "image_tag_links",
        type_="unique",
    )

    op.create_unique_constraint(
        "uq_image_tag",
        "image_tag_links",
        ["image_id", "tag_id"],
    )

    # Rename indexes on images
    op.drop_index(
        "ix_report_images_id",
        table_name="images",
    )
    op.drop_index(
        "ix_report_images_created_by",
        table_name="images",
    )

    op.create_index(
        "ix_images_id",
        "images",
        ["id"],
    )
    op.create_index(
        "ix_images_created_by",
        "images",
        ["created_by"],
    )

    # Rename indexes on image_tags
    op.drop_index(
        "ix_report_image_tags_id",
        table_name="image_tags",
    )
    op.drop_index(
        "ix_report_image_tags_company_id",
        table_name="image_tags",
    )

    op.create_index(
        "ix_image_tags_id",
        "image_tags",
        ["id"],
    )
    op.create_index(
        "ix_image_tags_company_id",
        "image_tags",
        ["company_id"],
    )


def downgrade() -> None:
    # Rename indexes on image_tags
    op.drop_index(
        "ix_image_tags_company_id",
        table_name="image_tags",
    )
    op.drop_index(
        "ix_image_tags_id",
        table_name="image_tags",
    )

    op.create_index(
        "ix_report_image_tags_company_id",
        "image_tags",
        ["company_id"],
    )
    op.create_index(
        "ix_report_image_tags_id",
        "image_tags",
        ["id"],
    )

    # Rename indexes on images
    op.drop_index(
        "ix_images_created_by",
        table_name="images",
    )
    op.drop_index(
        "ix_images_id",
        table_name="images",
    )

    op.create_index(
        "ix_report_images_created_by",
        "images",
        ["created_by"],
    )
    op.create_index(
        "ix_report_images_id",
        "images",
        ["id"],
    )

    # Rename unique constraint
    op.drop_constraint(
        "uq_image_tag",
        "image_tag_links",
        type_="unique",
    )

    op.create_unique_constraint(
        "uq_report_image_tag",
        "image_tag_links",
        ["image_id", "tag_id"],
    )

    # Rename FK column back
    op.alter_column(
        "image_tag_links",
        "image_id",
        new_column_name="report_image_id",
    )

    # Rename tables back
    op.rename_table("image_tag_links", "report_image_tag_links")
    op.rename_table("image_tags", "report_image_tags")
    op.rename_table("images", "report_images")