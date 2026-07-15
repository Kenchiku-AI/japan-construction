import uuid
import enum
from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, ForeignKey, Enum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base

class ConversationItemStatus(str, enum.Enum):
  new = "new"
  in_progress = "in_progress"
  closed = "closed"

class ConversationItem(Base):
  __tablename__ = "conversation_items"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

  project_id = Column(
    UUID(as_uuid=True),
    ForeignKey("projects.id", ondelete="CASCADE"),
    nullable=True,
    index=True,
  )

  conversation_id = Column(
    UUID(as_uuid=True),
    ForeignKey("line_conversations.id", ondelete="SET NULL"),
    nullable=True,
    index=True,
  )

  conversation_item_type_id = Column(
    UUID(as_uuid=True),
    ForeignKey("conversation_item_types.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  name = Column(String, nullable=False)
  description = Column(String, nullable=True)
  source_message_text = Column(String, nullable=True)
  line_timestamp = Column(DateTime(timezone=True), nullable=True)

  status = Column(
    Enum(ConversationItemStatus),
    nullable=False,
    default=ConversationItemStatus.new,
  )

  project = relationship(
    "Project",
    back_populates="conversation_items",
  )

  conversation = relationship(
    "LineConversation",
    back_populates="conversation_items",
  )

  item_type = relationship(
    "ConversationItemType",
    back_populates="conversation_items",
  )

  created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
  updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))