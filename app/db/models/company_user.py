import uuid

from sqlalchemy import Column, ForeignKey, String, DateTime, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime

from app.db.base import Base
from app.db.models.user import User
from app.db.models.company import Company

class CompanyUser(Base):
  __tablename__ = "company_users"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
  user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
  company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id"), primary_key=True)
  role = Column(String, default="member", nullable=False)

  created_at = Column(DateTime, default=datetime.utcnow)
  updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

  user = relationship("User", back_populates="company_users")
  company = relationship("Company", back_populates="company_users")

  __table_args__ = (
    UniqueConstraint("company_id", "user_id", name="uq_company_user"),
  )
