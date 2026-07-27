import uuid
from datetime import datetime, timezone
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

class ReportStatus(str, Enum):
  open = "open"
  closed = "closed"

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

  created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
  updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

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

  status = Column(SQLEnum(ReportStatus), nullable=False, default=ReportStatus.open)

  fields = relationship(
    "ReportField",
    back_populates="report",
    cascade="all, delete-orphan",
  )

  images = relationship(
    "Image",
    back_populates="report",
    cascade="all, delete-orphan",
  )

  template = relationship("ReportTemplate")

  created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
  updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
  created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

  creator = relationship(
    "User",
    foreign_keys=[created_by],
  )

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

  created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

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