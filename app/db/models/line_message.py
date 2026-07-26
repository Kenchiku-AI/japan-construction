import enum
from uuid import uuid4
from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, ForeignKey, UniqueConstraint, Integer, Enum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base
from app.db.models.line_conversation import LineChatType

class LineMessageType(enum.Enum):
  text = "text"
  image = "image"
  video = "video"
  audio = "audio"
  file = "file"

class LineMessage(Base):
  __tablename__ = "line_messages"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
  company_id = Column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True)
  
  message_type = Column(
    Enum(LineMessageType),
    nullable=False,
  )

  line_platform_message_id = Column(String, nullable=False, index=True)
  line_user_id = Column(String, nullable=True, index=True)
  line_chat_id = Column(String, nullable=True, index=True)
  line_chat_type = Column(Enum(LineChatType), nullable=True)
  line_timestamp = Column(DateTime(timezone=True), nullable=True)
  
  sender_line_user_id = Column(String, nullable=False)
  
  text = Column(String, nullable=True)

  conversation_id = Column(
    UUID(as_uuid=True),
    ForeignKey("line_conversations.id", ondelete="SET NULL"),
    nullable=True,
    index=True,
  )

  conversation_item_links: Mapped[list["LineMessageConversationItemLink"]] = relationship(
    back_populates="line_message",
    cascade="all, delete-orphan",
  )

  conversation = relationship("LineConversation", back_populates="messages")

  attachments = relationship(
    "LineMessageAttachment",
    back_populates="line_message",
    cascade="all, delete-orphan",
  )

  created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class LineMessageAttachmentType(enum.Enum):
  image = "image"
  video = "video"
  audio = "audio"
  file = "file"

class LineMessageAttachment(Base):
  __tablename__ = "line_message_attachments"

  id = Column(
    UUID(as_uuid=True),
    primary_key=True,
    default=uuid4,
  )

  line_message_id = Column(
    UUID(as_uuid=True),
    ForeignKey("line_messages.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  type = Column(
    Enum(LineMessageAttachmentType),
    nullable=False,
  )

  s3_key = Column(String, nullable=False)

  mime_type = Column(String, nullable=True)

  file_size = Column(Integer, nullable=True)

  width = Column(Integer, nullable=True)
  height = Column(Integer, nullable=True)

  created_at = Column(
    DateTime(timezone=True),
    default=lambda: datetime.now(timezone.utc),
  )

  line_message = relationship(
    "LineMessage",
    back_populates="attachments",
  )

class LineMessageConversationItemLink(Base):
  __tablename__ = "line_message_conversation_item_links"

  __table_args__ = (
    UniqueConstraint(
      "line_message_id",
      "conversation_item_id",
      name="uq_line_message_conversation_item",
    ),
  )

  id = Column(
    UUID(as_uuid=True),
    primary_key=True,
    default=uuid4,
  )

  line_message_id = Column(
    UUID(as_uuid=True),
    ForeignKey("line_messages.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  conversation_item_id = Column(
    UUID(as_uuid=True),
    ForeignKey("conversation_items.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  created_at = Column(
    DateTime(timezone=True),
    default=lambda: datetime.now(timezone.utc),
  )

  line_message = relationship(
    "LineMessage",
    back_populates="conversation_item_links",
  )

  conversation_item = relationship(
    "ConversationItem",
    back_populates="line_message_links",
  )