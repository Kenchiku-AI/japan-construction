from datetime import datetime
import uuid

from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base

class User(Base):
  __tablename__ = "users"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
  email = Column(String, unique=True, index=True, nullable=False)
  first_name = Column(String, nullable=True)
  last_name = Column(String, nullable=True)
  hashed_password = Column(String, nullable=True)
  role = Column(String, default="user", nullable=True)

  refresh_tokens = relationship(
    "RefreshToken",
    back_populates="user",
    cascade="all, delete-orphan",
  )

  company_id = Column(
    UUID(as_uuid=True),
    ForeignKey("companies.id", ondelete="CASCADE"),
    nullable=True,
  )

  company = relationship(
    "Company",
    back_populates="users",
  )

  guest_project_links = relationship(
    "ProjectGuestLink",
    back_populates="user",
    cascade="all, delete-orphan",
  )

  line_user_id = Column(String, nullable=True, unique=True, index=True)
  line_link_code = Column(String, nullable=True, unique=True, index=True)

  created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
  updated_at = Column(
    DateTime,
    default=datetime.utcnow,
    onupdate=datetime.utcnow,
    nullable=False,
  )
