import uuid
from datetime import datetime
from sqlalchemy import Column, String, Date, Time, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.db.base import Base
from app.db.models.project import Project

class DailyReport(Base):
  __tablename__ = "daily_reports"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
  project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False)
  date = Column(Date, nullable=False)
  start_time = Column(Time, nullable=True)
  end_time = Column(Time, nullable=True)
  work_performed = Column(String, nullable=True)
  weather = Column(String, nullable=True)

  created_at = Column(DateTime, default=datetime.utcnow)
  updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

  project = relationship("Project", back_populates="daily_reports")

  __table_args__ = (
    UniqueConstraint("project_id", "date", name="uq_project_daily_report"),
  )
