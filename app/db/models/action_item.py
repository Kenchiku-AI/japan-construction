import uuid
import enum
from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base

class ActionItemStatus(str, enum.Enum):
  new = "new"
  scheduled = "scheduled"
  in_progress = "in_progress"
  closed = "closed"

class ActionItem(Base):
  __tablename__ = "action_items"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
  project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
  assignee_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

  name = Column(String, nullable=False)
  description = Column(String, nullable=True)
  source_message_text = Column(String, nullable=True)
  status = Column(SQLEnum(WorkItemStatus), nullable=False, default=WorkItemStatus.new)
  scheduled_date = Column(DateTime(timezone=True), nullable=True)

  project = relationship("Project", back_populates="action_items")
  assignee = relationship("User", foreign_keys=[assignee_id])

  created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
  updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))