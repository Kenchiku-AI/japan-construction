import enum
import uuid

from datetime import datetime

from sqlalchemy import Column, String, DateTime, ForeignKey, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base

class ProjectStatus(str, enum.Enum):
  active = "active"
  completed = "completed"
  requested = "requested"

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
  daily_reports = relationship("DailyReport", back_populates="project", cascade="all, delete-orphan")

  created_at = Column(DateTime, default=datetime.utcnow)
  updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)