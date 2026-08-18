import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from app.db.base import Base


class CustomField(Base):
  __tablename__ = "custom_fields"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

  company_id = Column(
    UUID(as_uuid=True),
    ForeignKey("companies.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  custom_field_definition_id = Column(
    UUID(as_uuid=True),
    ForeignKey("custom_field_definitions.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  value = Column(String, nullable=True)

  definition = relationship(
    "CustomFieldDefinition",
    back_populates="fields",
  )

  company_links = relationship(
    "CustomFieldCompanyLink",
    back_populates="custom_field",
    cascade="all, delete-orphan",
  )

  project_links = relationship(
    "CustomFieldProjectLink",
    back_populates="custom_field",
    cascade="all, delete-orphan",
  )

  user_links = relationship(
    "CustomFieldUserLink",
    back_populates="custom_field",
    cascade="all, delete-orphan",
  )

  custom_object_links = relationship(
    "CustomFieldCustomObjectLink",
    back_populates="custom_field",
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


class CustomFieldCompanyLink(Base):
  __tablename__ = "custom_field_company_links"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

  custom_field_id = Column(
    UUID(as_uuid=True),
    ForeignKey("custom_fields.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  company_id = Column(
    UUID(as_uuid=True),
    ForeignKey("companies.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  custom_field = relationship(
    "CustomField",
    back_populates="company_links",
  )

  company = relationship(
    "Company",
    back_populates="custom_field_links",
  )

  __table_args__ = (
    UniqueConstraint(
      "custom_field_id",
      "company_id",
      name="uq_custom_field_company_link",
    ),
  )


class CustomFieldProjectLink(Base):
  __tablename__ = "custom_field_project_links"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

  custom_field_id = Column(
    UUID(as_uuid=True),
    ForeignKey("custom_fields.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  project_id = Column(
    UUID(as_uuid=True),
    ForeignKey("projects.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  custom_field = relationship(
    "CustomField",
    back_populates="project_links",
  )

  project = relationship(
    "Project",
    back_populates="custom_field_links",
  )

  __table_args__ = (
    UniqueConstraint(
      "custom_field_id",
      "project_id",
      name="uq_custom_field_project_link",
    ),
  )


class CustomFieldUserLink(Base):
  __tablename__ = "custom_field_user_links"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

  custom_field_id = Column(
    UUID(as_uuid=True),
    ForeignKey("custom_fields.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  user_id = Column(
    UUID(as_uuid=True),
    ForeignKey("users.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  custom_field = relationship(
    "CustomField",
    back_populates="user_links",
  )

  user = relationship(
    "User",
    back_populates="custom_field_links",
  )

  __table_args__ = (
    UniqueConstraint(
      "custom_field_id",
      "user_id",
      name="uq_custom_field_user_link",
    ),
  )


class CustomFieldCustomObjectLink(Base):
  __tablename__ = "custom_field_custom_object_links"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

  custom_field_id = Column(
    UUID(as_uuid=True),
    ForeignKey("custom_fields.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  custom_object_id = Column(
    UUID(as_uuid=True),
    ForeignKey("custom_objects.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  custom_field = relationship(
    "CustomField",
    back_populates="custom_object_links",
  )

  custom_object = relationship(
    "CustomObject",
    back_populates="custom_field_links",
  )

  __table_args__ = (
    UniqueConstraint(
      "custom_field_id",
      "custom_object_id",
      name="uq_custom_field_custom_object_link",
    ),
  )