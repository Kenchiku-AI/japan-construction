import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import (
  Boolean,
  Column,
  String,
  DateTime,
  Enum as SQLEnum,
  ForeignKey,
  Index,
  Integer,
  UniqueConstraint
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base

class ReportParentType(str, Enum):
  project = "project"
  company = "company"

class ReportTemplate(Base):
  __tablename__ = "report_templates"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

  name = Column(String, nullable=False)
  description = Column(String, nullable=True)

  is_global = Column(Boolean, nullable=False, default=False)
  
  parent_type = Column(SQLEnum(ReportParentType), nullable=False)

  fields = relationship(
    "ReportTemplateField",
    back_populates="template",
    cascade="all, delete-orphan",
  )

  company_report_templates = relationship(
    "CompanyReportTemplate",
    back_populates="report_template",
    cascade="all, delete-orphan",
  )

  created_at = Column(DateTime, default=datetime.utcnow)
  updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class ReportTemplateField(Base):
  __tablename__ = "report_template_fields"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
  template_id = Column(
    UUID(as_uuid=True),
    ForeignKey("report_templates.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  name = Column(String, nullable=False)
  description = Column(String, nullable=False)
  order = Column(Integer, nullable=False)

  template = relationship("ReportTemplate", back_populates="fields")

class Report(Base):
  __tablename__ = "reports"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
  name = Column(String, nullable=False)

  template_id = Column(
    UUID(as_uuid=True),
    ForeignKey("report_templates.id", ondelete="RESTRICT"),
    nullable=False,
    index=True,
  )

  parent_type = Column(SQLEnum(ReportParentType), nullable=False)
  parent_id = Column(UUID(as_uuid=True), nullable=False)

  fields = relationship(
    "ReportField",
    back_populates="report",
    cascade="all, delete-orphan",
  )

  images = relationship(
    "ReportImage",
    back_populates="report",
    cascade="all, delete-orphan",
  )

  template = relationship("ReportTemplate")

  created_at = Column(DateTime, default=datetime.utcnow)
  updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
  created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

  __table_args__ = (
    Index("ix_reports_parent", "parent_type", "parent_id"),
  )

class ReportField(Base):
  __tablename__ = "report_fields"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
  name = Column(String, nullable=False)
  description = Column(String, nullable=False)
  value = Column(String, nullable=True)
  order = Column(Integer, nullable=False)

  report_id = Column(
    UUID(as_uuid=True),
    ForeignKey("reports.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  report = relationship("Report", back_populates="fields")

class CompanyReportTemplate(Base):
  __tablename__ = "company_report_templates"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

  company_id = Column(
    UUID(as_uuid=True),
    ForeignKey("companies.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  report_template_id = Column(
    UUID(as_uuid=True),
    ForeignKey("report_templates.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  created_at = Column(DateTime, default=datetime.utcnow)

  company = relationship("Company", back_populates="company_report_templates")
  report_template = relationship(
    "ReportTemplate",
    back_populates="company_report_templates",
  )

  __table_args__ = (
    Index(
      "ux_company_report_template",
      "company_id",
      "report_template_id",
      unique=True,
    ),
  )

class ReportImage(Base):
  __tablename__ = "report_images"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
  report_id = Column(UUID(as_uuid=True), ForeignKey("reports.id", ondelete="CASCADE"), nullable=False)
  image_url = Column(String, nullable=False)
  status = Column(String, nullable=False, default="pending")
  width = Column(Integer, nullable=True)
  height = Column(Integer, nullable=True)
  description = Column(String, nullable=True)
  created_at = Column(DateTime, default=datetime.utcnow)
  created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

  report = relationship("Report", back_populates="images")
  
  tag_links = relationship(
    "ReportImageTagLink",
    back_populates="image",
    cascade="all, delete-orphan",
  )

class ReportImageTag(Base):
  __tablename__ = "report_image_tags"

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

  created_at = Column(DateTime, default=datetime.utcnow)

  tag_links = relationship(
    "ReportImageTagLink",
    back_populates="tag",
    cascade="all, delete-orphan",
  )

  __table_args__ = (
    UniqueConstraint("company_id", "name", name="uq_company_tag_name"),
  )

class ReportImageTagLink(Base):
  __tablename__ = "report_image_tag_links"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

  report_image_id = Column(
    UUID(as_uuid=True),
    ForeignKey("report_images.id", ondelete="CASCADE"),
    nullable=False,
  )

  tag_id = Column(
    UUID(as_uuid=True),
    ForeignKey("report_image_tags.id", ondelete="CASCADE"),
    nullable=False,
  )

  image = relationship(
    "ReportImage",
    back_populates="tag_links"
  )

  tag = relationship(
    "ReportImageTag",
    back_populates="tag_links"
  )

  __table_args__ = (
    UniqueConstraint(
      "report_image_id",
      "tag_id",
      name="uq_report_image_tag"
    ),
  )