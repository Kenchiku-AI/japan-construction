import uuid

from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime, timezone

from app.core.security import encrypt_secret, decrypt_secret
from app.db.base import Base
from app.db.models.line_conversation import LineConversation
from app.db.models.conversation_item_type import ConversationItemType

class Company(Base):
  __tablename__ = "companies"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
  name = Column(String, nullable=False, index=True)
  corporate_number = Column(String, nullable=True, index=True)
  image_descriptions_enabled = Column(Boolean, default=True, nullable=False)

  users = relationship("User", back_populates="company")

  projects = relationship(
    "Project",
    back_populates="company",
    cascade="all, delete-orphan",
  )

  images = relationship(
    "Image",
    back_populates="company",
  )

  image_tags = relationship(
    "ImageTag",
    back_populates="company",
    cascade="all, delete-orphan",
  )

  company_report_templates = relationship(
    "CompanyReportTemplate",
    back_populates="company",
    cascade="all, delete-orphan",
  )

  stripe_customer_id = Column(String, nullable=True, index=True)
  stripe_subscription_id = Column(String, nullable=True, index=True)

  billing_plan_id = Column(
    UUID(as_uuid=True),
    ForeignKey("billing_plans.id"),
    nullable=True,
  )

  conversations = relationship(
    "LineConversation",
    back_populates="company",
  )

  conversation_item_types = relationship(
    "ConversationItemType",
    back_populates="company",
    cascade="all, delete-orphan",
  )

  _line_channel_secret = Column("line_channel_secret", String, nullable=True)
  _line_channel_access_token = Column("line_channel_access_token", String, nullable=True)

  created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
  updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

  @property
  def line_channel_secret(self) -> str | None:
    return decrypt_secret(self._line_channel_secret) if self._line_channel_secret else None

  @line_channel_secret.setter
  def line_channel_secret(self, value: str | None) -> None:
    self._line_channel_secret = encrypt_secret(value) if value else None

  @property
  def line_channel_access_token(self) -> str | None:
    return decrypt_secret(self._line_channel_access_token) if self._line_channel_access_token else None

  @line_channel_access_token.setter
  def line_channel_access_token(self, value: str | None) -> None:
    self._line_channel_access_token = encrypt_secret(value) if value else None

  @property
  def line_channel_secret_last4(self) -> str | None:
    secret = self.line_channel_secret  # decrypts via the existing property
    return secret[-4:] if secret else None