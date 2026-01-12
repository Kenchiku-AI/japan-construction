import uuid

from sqlalchemy import (
  Column,
  String,
  DateTime,
  ForeignKey,
  Boolean,
)
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base

class RefreshToken(Base):
  __tablename__ = "refresh_tokens"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
  user_id = Column(UUID(as_uuid=True), ForeignKey("user.id"), nullable=False)
  token_hash = Column(String, nullable=False, index=True)
  revoked = Column(Boolean, default=False, nullable=False)

  created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
  expires_at = Column(DateTime(timezone=True), nullable=False)

  user = relationship("User", back_populates="refresh_tokens")