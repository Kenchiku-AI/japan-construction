"""clean up invalid image company relationships

Revision ID: 707b54c07572
Revises: 3781bb9779cf
Create Date: 2026-07-29 02:30:04.757685

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "707b54c07572"
down_revision: Union[str, Sequence[str], None] = "3781bb9779cf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Clean up invalid image/tag relationships."""

    # ---------------------------------------------------------
    # 1. Delete tag links pointing to images that have no company.
    #
    # This must happen before deleting the images themselves.
    # ---------------------------------------------------------
    op.execute("""
        DELETE FROM image_tag_links
        WHERE image_id IN (
            SELECT id
            FROM images
            WHERE company_id IS NULL
        );
    """)

    # ---------------------------------------------------------
    # 2. Delete images that have no company.
    # ---------------------------------------------------------
    op.execute("""
        DELETE FROM images
        WHERE company_id IS NULL;
    """)

    # ---------------------------------------------------------
    # 3. Delete tag links where the image and tag belong
    #    to different companies.
    #
    # Example:
    #   image.company_id = Company A
    #   tag.company_id   = Company B
    #
    # These links are invalid.
    # ---------------------------------------------------------
    op.execute("""
        DELETE FROM image_tag_links itl
        USING images i, image_tags it
        WHERE itl.image_id = i.id
          AND itl.tag_id = it.id
          AND i.company_id != it.company_id;
    """)

    # ---------------------------------------------------------
    # 4. Delete any image_tag_links whose image no longer exists.
    #
    # Normally the FK ON DELETE CASCADE should prevent these,
    # but clean them up if they somehow exist.
    # ---------------------------------------------------------
    op.execute("""
        DELETE FROM image_tag_links itl
        WHERE NOT EXISTS (
            SELECT 1
            FROM images i
            WHERE i.id = itl.image_id
        );
    """)

    # ---------------------------------------------------------
    # 5. Delete any image_tag_links whose tag no longer exists.
    #
    # Again, ON DELETE CASCADE should normally prevent this,
    # but this makes the migration defensive.
    # ---------------------------------------------------------
    op.execute("""
        DELETE FROM image_tag_links itl
        WHERE NOT EXISTS (
            SELECT 1
            FROM image_tags it
            WHERE it.id = itl.tag_id
        );
    """)


def downgrade() -> None:
    """No safe downgrade exists for deleted data."""
    pass