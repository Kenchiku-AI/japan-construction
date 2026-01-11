from sqlalchemy import Column, Integer, ForeignKey, String, DateTime, UniqueConstraint
from sqlalchemy.orm import relationship
from datetime import datetime

from app.db.base import Base
from app.db.models.user import User
from app.db.models.company import Company

class CompanyUser(Base):
  __tablename__ = "company_users"

  id = Column(Integer, primary_key=True, index=True)
  company_id = Column(Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
  user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
  role = Column(String, default="member", nullable=False)

  created_at = Column(DateTime, default=datetime.utcnow)
  updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

  user = relationship("User", back_populates="company_users")
  company = relationship("Company", back_populates="company_users")

  __table_args__ = (
    UniqueConstraint("company_id", "user_id", name="uq_company_user"),
  )
