import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base

class LineConversation(Base):
  __tablename__ = "line_conversations"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
  name = Column(String, nullable=False)
  line_link_code = Column(String, nullable=False, unique=True, index=True)
  line_group_id = Column(String, nullable=True, unique=True, index=True)

  project_id = Column(
    UUID(as_uuid=True),
    ForeignKey("projects.id", ondelete="SET NULL"),
    nullable=True,
    index=True,
  )

  company_id = Column(
    UUID(as_uuid=True),
    ForeignKey("companies.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  project = relationship("Project", back_populates="conversations")
  company = relationship("Company", back_populates="conversations")

  conversation_items = relationship(
    "ConversationItem",
    back_populates="conversation",
  )

  item_type_links = relationship(
    "ConversationItemTypeLink",
    back_populates="conversation",
    cascade="all, delete-orphan",
  )

  messages = relationship(
    "LineMessage",
    back_populates="conversation",
    cascade="all, delete-orphan",
  )

  created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
  updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

  @property
  def item_types(self):
    return [link.item_type for link in self.item_type_links]