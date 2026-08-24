import enum
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, Enum, String, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from app.db.base import Base

class CustomFieldEntityType(str, enum.Enum):
  company = "company"
  user = "user"
  project = "project"
  custom_object = "custom_object"

class CustomFieldDataType(str, enum.Enum):
  text = "text"
  boolean = "boolean"

class CustomFieldDefinition(Base):
  __tablename__ = "custom_field_definitions"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

  company_id = Column(
    UUID(as_uuid=True),
    ForeignKey("companies.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  name = Column(String, nullable=False)
  description = Column(Text, nullable=True)

  entity_type = Column(
    String,
    nullable=False,
  )

  data_type = Column(
    String,
    nullable=False,
    default=CustomFieldDataType.text.value,
  )

  sort_order = Column(Integer, default=0)

  custom_object_definition_id = Column(
    UUID(as_uuid=True),
    ForeignKey(
      "custom_object_definitions.id",
      ondelete="CASCADE",
    ),
    nullable=True,
    index=True,
  )

  custom_object_definition = relationship(
    "CustomObjectDefinition",
    back_populates="custom_field_definitions",
  )

  fields = relationship(
    "CustomField",
    back_populates="definition",
    cascade="all, delete-orphan",
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