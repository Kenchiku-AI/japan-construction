import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, ForeignKey, Boolean, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class ConversationItemType(Base):
  __tablename__ = "conversation_item_types"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
  name = Column(String, nullable=False)
  description = Column(String, nullable=True)

  company_id = Column(
    UUID(as_uuid=True),
    ForeignKey("companies.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  company = relationship("Company", back_populates="conversation_item_types")

  conversation_links = relationship(
    "ConversationItemTypeLink",
    back_populates="item_type",
    cascade="all, delete-orphan",
  )

  conversation_items = relationship(
    "ConversationItem",
    back_populates="item_type",
  )

  is_active = Column(
    Boolean,
    nullable=False,
    default=True,
  )

  created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
  updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class ConversationItemTypeLink(Base):
  __tablename__ = "conversation_item_type_links"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

  conversation_id = Column(
    UUID(as_uuid=True),
    ForeignKey("line_conversations.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  item_type_id = Column(
    UUID(as_uuid=True),
    ForeignKey("conversation_item_types.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  conversation = relationship("LineConversation", back_populates="item_type_links")
  item_type = relationship("ConversationItemType", back_populates="conversation_links")

  __table_args__ = (
    UniqueConstraint("conversation_id", "item_type_id", name="uq_conversation_item_type_link"),
  )