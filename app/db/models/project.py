import enum
import uuid

from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, ForeignKey, Enum, and_
from sqlalchemy.orm import relationship, foreign
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base
from app.db.models.report import Report, ReportProjectLink
from app.db.models.line_conversation import LineConversation
from app.db.models.conversation_item import ConversationItem

class ProjectStatus(str, enum.Enum):
  active = "active"
  completed = "completed"
  requested = "requested"
  archived = "archived"

class Project(Base):
  __tablename__ = "projects"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
  name = Column(String, nullable=False)
  description = Column(String, nullable=True)
  company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False, index=True)

  status = Column(
    Enum(ProjectStatus, name="project_status"),
    nullable=False,
    default=ProjectStatus.active,
  )

  company = relationship("Company", back_populates="projects")

  reports = relationship(
    "Report",
    secondary="report_project_links",
    primaryjoin="Project.id == ReportProjectLink.project_id",
    secondaryjoin="Report.id == ReportProjectLink.report_id",
    viewonly=True,
  )

  report_links = relationship(
    "ReportProjectLink",
    back_populates="project",
    cascade="all, delete-orphan",
  )

  guest_links = relationship(
    "ProjectGuestLink",
    back_populates="project",
    cascade="all, delete-orphan",
  )

  conversations = relationship(
    "LineConversation",
    back_populates="project",
    order_by="desc(LineConversation.updated_at)",
    cascade="all, delete-orphan",
  )

  conversation_items = relationship(
    "ConversationItem",
    back_populates="project",
    cascade="all, delete-orphan",
  )

  custom_field_links = relationship(
    "CustomFieldProjectLink",
    back_populates="project",
    cascade="all, delete-orphan",
  )

  custom_relationships = relationship(
    "CustomRelationship",
    primaryjoin=(
      "and_("
      "Project.id == foreign(CustomRelationship.source_entity_id), "
      "CustomRelationship.source_entity_type == 'project', "
      "Project.company_id == foreign(CustomRelationship.company_id)"
      ")"
    ),
    viewonly=True,
  )

  created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
  updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))