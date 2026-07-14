import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class ConversationItem(Base):
  __tablename__ = "conversation_items"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)

  conversation_id = Column(
    UUID(as_uuid=True),
    ForeignKey("line_conversations.id", ondelete="CASCADE"),
    nullable=False,
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

  conversation = relationship("LineConversation", back_populates="conversation_items")
  item_type = relationship("ConversationItemType", back_populates="conversation_items")

  created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
  updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))