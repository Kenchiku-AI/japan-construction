import enum
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, ForeignKey, Enum, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from app.db.base import Base


class CustomRelationshipEntityType(str, enum.Enum):
  company = "company"
  user = "user"
  project = "project"
  custom_object = "custom_object"


class CustomRelationshipCardinality(str, enum.Enum):
  one = "one"
  many = "many"


class CustomRelationshipDefinition(Base):
  __tablename__ = "custom_relationship_definitions"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

  company_id = Column(
    UUID(as_uuid=True),
    ForeignKey("companies.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  key = Column(String, nullable=False)
  name = Column(String, nullable=False)
  description = Column(Text, nullable=True)

  source_entity_type = Column(
    Enum(CustomRelationshipEntityType, name="custom_relationship_entity_type"),
    nullable=False,
  )

  target_entity_type = Column(
    Enum(CustomRelationshipEntityType, name="custom_relationship_entity_type"),
    nullable=False,
  )

  source_cardinality = Column(
    Enum(
      CustomRelationshipCardinality,
      name="custom_relationship_cardinality",
    ),
    nullable=False,
    default=CustomRelationshipCardinality.one,
  )

  target_cardinality = Column(
    Enum(
      CustomRelationshipCardinality,
      name="custom_relationship_cardinality",
    ),
    nullable=False,
    default=CustomRelationshipCardinality.many,
  )

  company = relationship(
    "Company",
    back_populates="custom_relationship_definitions",
  )

  relationships = relationship(
    "CustomRelationship",
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

  __table_args__ = (
    UniqueConstraint(
      "company_id",
      "key",
      name="uq_custom_relationship_definition_company_key",
    ),
  )