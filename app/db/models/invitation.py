import uuid

from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime, timedelta

from app.db.base import Base

class Invitation(Base):
  __tablename__ = "invitations"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
  email = Column(String, index=True, nullable=False)
  token_hash = Column(String, unique=True, nullable=False)
  role = Column(String, default="user", nullable=False)
  expires_at = Column(DateTime, nullable=False)

  company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), nullable=True)

  project_id = Column(
    UUID(as_uuid=True),
    ForeignKey("projects.id", ondelete="CASCADE"),
    nullable=True,
    index=True,
  )

  
  created_at = Column(DateTime, default=datetime.utcnow)