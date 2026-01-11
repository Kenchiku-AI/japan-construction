from sqlalchemy import (
  Column,
  Integer,
  String,
  DateTime,
  ForeignKey,
  Boolean,
)
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.db.base import Base

class RefreshToken(Base):
  __tablename__ = "refresh_tokens"

  id = Column(Integer, primary_key=True, index=True)

  user_id = Column(
    Integer,
    ForeignKey("users.id", ondelete="CASCADE"),
    nullable=False,
  )

  token_hash = Column(String, nullable=False, index=True)

  revoked = Column(Boolean, default=False, nullable=False)

  created_at = Column(
    DateTime(timezone=True),
    server_default=func.now(),
    nullable=False,
  )

  expires_at = Column(DateTime(timezone=True), nullable=False)

  user = relationship("User", back_populates="refresh_tokens")