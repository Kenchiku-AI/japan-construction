import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean, Text
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from app.db.base import Base


class CustomObjectDefinition(Base):
  __tablename__ = "custom_object_definitions"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

  company_id = Column(
    UUID(as_uuid=True),
    ForeignKey("companies.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  name = Column(String, nullable=False)
  description = Column(Text, nullable=True)

  company = relationship(
    "Company",
    back_populates="custom_object_definitions",
  )

  objects = relationship(
    "CustomObject",
    back_populates="definition",
    cascade="all, delete-orphan",
  )

  custom_field_definitions = relationship(
    "CustomFieldDefinition",
    back_populates="custom_object_definition",
    cascade="all, delete-orphan",
  )

  custom_relationship_definitions_as_source = relationship(
    "CustomRelationshipDefinition",
    foreign_keys="CustomRelationshipDefinition.source_custom_object_definition_id",
    back_populates="source_custom_object_definition",
  )

  custom_relationship_definitions_as_target = relationship(
    "CustomRelationshipDefinition",
    foreign_keys="CustomRelationshipDefinition.target_custom_object_definition_id",
    back_populates="target_custom_object_definition",
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