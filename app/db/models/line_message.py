import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base

class LineMessage(Base):
  __tablename__ = "line_messages"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
  company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
  
  line_user_id = Column(String, nullable=True, index=True)
  line_group_id = Column(String, nullable=True, index=True)
  
  sender_line_user_id = Column(String, nullable=False)
  triggered_work_item = Column(Boolean, nullable=False, default=False)
  text = Column(String, nullable=False)
  created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))