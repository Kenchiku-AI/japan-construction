from datetime import datetime
import uuid

from sqlalchemy import Column, String, Boolean, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base

class User(Base):
  __tablename__ = "users"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
  email = Column(String, unique=True, index=True, nullable=False)
  hashed_password = Column(String, nullable=False)
  is_active = Column(Boolean, default=True)
  role = Column(String, default="user", nullable=False)

  refresh_tokens = relationship(
    "RefreshToken",
    back_populates="user",
    cascade="all, delete-orphan",
  )

  company_users = relationship(
    "CompanyUser",
    back_populates="user",
    cascade="all, delete-orphan",
  )

  created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
  updated_at = Column(
    DateTime,
    default=datetime.utcnow,
    onupdate=datetime.utcnow,
    nullable=False,
  )

  def role_in_company(self, company_id: int) -> str | None:
    for cu in self.company_users:
      if cu.company_id == company_id:
        return cu.role
    return None
