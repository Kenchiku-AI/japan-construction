from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from app.db.base import Base
import uuid

class PasswordResetToken(Base):
  __tablename__ = "password_reset_tokens"

  id = Column(UUID(as_uuid=True), primary_key=True)
  user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

  token_hash = Column(String, nullable=False, index=True)
  expires_at = Column(DateTime(timezone=True), nullable=False)
  created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))