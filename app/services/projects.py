from uuid import UUID
import logging
from datetime import datetime

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.user import User
from app.db.models.project import Project
from app.db.models.project_guest_link import ProjectGuestLink
from app.db.models.line_conversation import LineConversation
from app.db.models.line_message import LineMessage, LineMessageConversationItemLink
from app.db.models.conversation_item import ConversationItem, ConversationItemStatus
from app.db.models.conversation_item_type import ConversationItemType, ConversationItemTypeLink
from app.db.session import AsyncSessionLocal
from app.services.openai import extract_conversation_items

logger = logging.getLogger(__name__)

async def get_project_members(
  project_id: UUID,
  company_id: UUID,
  db: AsyncSession,
) -> list[User]:
  company_members_result = await db.execute(
    select(User).where(User.company_id == company_id)
  )
  company_members = company_members_result.scalars().all()

  guest_members_result = await db.execute(
    select(User)
    .join(ProjectGuestLink, ProjectGuestLink.user_id == User.id)
    .where(ProjectGuestLink.project_id == project_id)
  )
  guest_members = guest_members_result.scalars().all()

  seen = set()
  members = []
  for user in list(company_members) + list(guest_members):
    if user.id not in seen:
      seen.add(user.id)
      members.append(user)

  return members

async def handle_line_conversation_items(
  text: str,
  conversation_id: UUID,
  sender_line_user_id: str,
  company_id: UUID,
  line_timestamp: datetime | None,
):
  async with AsyncSessionLocal() as db:
    conversation_result = await db.execute(
      select(LineConversation)
      .options(
        selectinload(LineConversation.project),
        selectinload(LineConversation.item_type_links)
          .selectinload(ConversationItemTypeLink.item_type),
      )
      .where(LineConversation.id == conversation_id)
    )

    conversation = conversation_result.scalar_one_or_none()

    if not conversation:
      return

    current_message = LineMessage(
      conversation_id=conversation.id,
      company_id=company_id,
      line_chat_id=conversation.line_chat_id,
      text=text,
      line_timestamp=line_timestamp,
      sender_line_user_id=sender_line_user_id,
    )

    db.add(current_message)
    await db.flush()

    if not conversation.project_id:
      await db.commit()
      return

    item_types = [
      link.item_type
      for link in conversation.item_type_links
      if link.item_type.is_active
    ]

    if not item_types:
      await db.commit()
      return

    history_result = await db.execute(
      select(LineMessage)
      .options(
        selectinload(
          LineMessage.conversation_item_links
        ).selectinload(
          LineMessageConversationItemLink.conversation_item
        ).selectinload(
          ConversationItem.item_type
        )
      )
      .where(
        LineMessage.conversation_id == conversation.id,
        LineMessage.id != current_message.id,
      )
      .order_by(
        LineMessage.line_timestamp.desc().nullslast(),
        LineMessage.created_at.desc(),
      )
      .limit(20)
    )

    recent_messages = list(reversed(history_result.scalars().all()))

    recent_items_result = await db.execute(
      select(ConversationItem)
      .where(
        ConversationItem.project_id == conversation.project_id,
        ConversationItem.conversation_item_type_id.in_(
          [t.id for t in item_types]
        ),
      )
      .order_by(
        ConversationItem.updated_at.desc()
      )
    )

    recent_items = recent_items_result.scalars().all()

    grouped_items = {}

    for item in recent_items:
      grouped_items.setdefault(
        item.conversation_item_type_id,
        [],
      ).append(item)

    limited_items = []

    for items in grouped_items.values():
      limited_items.extend(items[:5])

    limited_items = limited_items[:30]

    extracted_items = await extract_conversation_items(
      message_text=text,
      conversation=conversation,
      recent_messages=recent_messages,
      item_types=item_types,
      existing_items=limited_items,
    )

    for extracted in extracted_items:
      action = extracted.get("action")

      if action == "create":
        item = ConversationItem(
          project_id=conversation.project_id,
          conversation_id=conversation.id,
          conversation_item_type_id=UUID(
            extracted["conversation_item_type_id"]
          ),
          name=extracted["name"],
          description=extracted.get("description"),
          status=ConversationItemStatus.new,
          source_message_text=text,
          line_timestamp=line_timestamp,
        )

        db.add(item)
        await db.flush()

        db.add(
          LineMessageConversationItemLink(
            line_message_id=current_message.id,
            conversation_item_id=item.id,
          )
        )

        logger.info(
          "Conversation item created | type=%s name=%s",
          item.conversation_item_type_id,
          item.name,
        )

      elif action == "update":
        item_result = await db.execute(
          select(ConversationItem).where(
            ConversationItem.id == extracted["conversation_item_id"],
            ConversationItem.project_id == conversation.project_id,
          )
        )

        item = item_result.scalar_one_or_none()

        if item:
          if "name" in extracted:
            item.name = extracted["name"]

          if "description" in extracted:
            item.description = extracted["description"]
          
          db.add(
            LineMessageConversationItemLink(
              line_message_id=current_message.id,
              conversation_item_id=item.id,
            )
          )

          logger.info(
            "Conversation item updated | id=%s",
            item.id,
          )

    await db.commit()