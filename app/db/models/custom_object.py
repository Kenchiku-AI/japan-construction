import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean, Text
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from app.db.base import Base


class CustomObject(Base):
  __tablename__ = "custom_objects"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

  company_id = Column(
    UUID(as_uuid=True),
    ForeignKey("companies.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  custom_object_definition_id = Column(
    UUID(as_uuid=True),
    ForeignKey("custom_object_definitions.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  company = relationship(
    "Company",
    back_populates="custom_objects",
  )

  definition = relationship(
    "CustomObjectDefinition",
    back_populates="objects",
  )

  custom_field_links = relationship(
    "CustomFieldCustomObjectLink",
    back_populates="custom_object",
    cascade="all, delete-orphan",
  )

  custom_relationships = relationship(
    "CustomRelationship",
    primaryjoin=(
      "and_("
      "CustomObject.id == foreign(CustomRelationship.source_entity_id), "
      "CustomRelationship.source_entity_type == 'custom_object', "
      "CustomObject.company_id == foreign(CustomRelationship.company_id)"
      ")"
    ),
    viewonly=True,
  )

  created_at = Column(
    DateTime(timezone=True),
    default=lambda: datetime.now(timezone.utc),
  )
  
  updated_at = Column(
    DateTime(timezone=True),
    default=lambda: datetime.now(timezone.utc),
    onupdate=lambda: datetime.now(timezone.utc),
  )