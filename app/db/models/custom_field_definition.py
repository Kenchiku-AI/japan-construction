import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from app.db.base import Base

class CustomFieldDefinition(Base):
  __tablename__ = "custom_field_definitions"

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

  company = relationship(
    "Company",
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

  __table_args__ = (
    UniqueConstraint(
      "company_id",
      "key",
      name="uq_custom_field_definition_company_key",
    ),
  )