from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Boolean
from datetime import datetime, timedelta

from app.db.base import Base

class Invitation(Base):
  __tablename__ = "invitations"

  id = Column(Integer, primary_key=True)

  email = Column(String, index=True, nullable=False)
  token_hash = Column(String, unique=True, nullable=False)

  company_id = Column(ForeignKey("companies.id"), nullable=False)
  role = Column(String, default="member")

  expires_at = Column(DateTime, nullable=False)
  created_at = Column(DateTime, default=datetime.utcnow)