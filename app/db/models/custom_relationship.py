import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from app.db.base import Base


class CustomRelationship(Base):
  __tablename__ = "custom_relationships"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

  company_id = Column(
    UUID(as_uuid=True),
    ForeignKey("companies.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  custom_relationship_definition_id = Column(
    UUID(as_uuid=True),
    ForeignKey(
      "custom_relationship_definitions.id",
      ondelete="CASCADE",
    ),
    nullable=False,
    index=True,
  )

  source_entity_type = Column(String, nullable=False)
  source_entity_id = Column(
    UUID(as_uuid=True),
    nullable=False,
    index=True,
  )
  source_custom_object_definition_id = Column(
    UUID(as_uuid=True),
    ForeignKey(
      "custom_object_definitions.id",
      ondelete="CASCADE",
    ),
    nullable=True,
    index=True,
  )

  target_entity_type = Column(String, nullable=False)
  target_entity_id = Column(
    UUID(as_uuid=True),
    nullable=False,
    index=True,
  )
  target_custom_object_definition_id = Column(
    UUID(as_uuid=True),
    ForeignKey(
      "custom_object_definitions.id",
      ondelete="CASCADE",
    ),
    nullable=True,
    index=True,
  )

  company = relationship(
    "Company",
    back_populates="custom_relationships",
  )

  definition = relationship(
    "CustomRelationshipDefinition",
    back_populates="relationships",
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
      "custom_relationship_definition_id",
      "source_entity_id",
      "target_entity_id",
      name="uq_custom_relationship",
    ),
  )