import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import (
    Column,
    String,
    DateTime,
    ForeignKey,
    Integer,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base

class ImageProcessingType(str, Enum):
  report = "report"
  status = "status"

class Image(Base):
  __tablename__ = "images"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
  report_id = Column(UUID(as_uuid=True), ForeignKey("reports.id", ondelete="CASCADE"), nullable=False)
  image_url = Column(String, nullable=False)
  status = Column(String, nullable=False, default="pending")
  width = Column(Integer, nullable=True)
  height = Column(Integer, nullable=True)
  description = Column(String, nullable=True)
  created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
  created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

  # Leaving as string instead of enum - no migrations required as enum values change
  processing_type = Column(String, nullable=True)
  
  tag_links = relationship(
    "ImageTagLink",
    back_populates="image",
    cascade="all, delete-orphan",
  )

class ImageTag(Base):
  __tablename__ = "image_tags"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

  company_id = Column(
    UUID(as_uuid=True),
    ForeignKey("companies.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  name = Column(String, nullable=False)
  description = Column(String, nullable=True)

  company = relationship("Company", back_populates="image_tags")

  created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

  tag_links = relationship(
    "ImageTagLink",
    back_populates="tag",
    cascade="all, delete-orphan",
  )

  __table_args__ = (
    UniqueConstraint("company_id", "name", name="uq_company_tag_name"),
  )

class ImageTagLink(Base):
  __tablename__ = "image_tag_links"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

  image_id = Column(
    UUID(as_uuid=True),
    ForeignKey("images.id", ondelete="CASCADE"),
    nullable=False,
  )

  tag_id = Column(
    UUID(as_uuid=True),
    ForeignKey("image_tags.id", ondelete="CASCADE"),
    nullable=False,
  )

  image = relationship(
    "Image",
    back_populates="tag_links"
  )

  tag = relationship(
    "ImageTag",
    back_populates="tag_links"
  )

  __table_args__ = (
    UniqueConstraint(
      "image_id",
      "tag_id",
      name="uq_image_tag"
    ),
  )