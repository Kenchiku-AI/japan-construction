from datetime import datetime, timezone
import uuid

from sqlalchemy import Column, String, DateTime, ForeignKey, UniqueConstraint
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

  line_link_code = Column(String, nullable=False, unique=True, index=True)

  created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
  updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class UserLineLink(Base):
  __tablename__ = "user_line_links"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
  user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
  company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
  line_user_id = Column(String, nullable=True, index=True)

  __table_args__ = (
    UniqueConstraint("user_id", "company_id", name="uq_user_line_link_user_company"),
    UniqueConstraint("company_id", "line_user_id", name="uq_user_line_link_company_line_user"),
  )