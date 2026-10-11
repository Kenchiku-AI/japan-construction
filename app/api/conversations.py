from uuid import UUID
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select, delete, inspect as sqlalchemy_inspect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.db.models.conversation_item_type import (
  ConversationItemType,
  ConversationItemTypeLink,
)
from app.db.models.user import User
from app.db.models.project import Project
from app.db.models.line_conversation import LineConversation
from app.db.models.line_message import LineMessage, LineMessageImageLink
from app.db.models.user import User
from app.core.dependencies import get_current_user, require_company_manager
from app.core.security import generate_unique_conversation_line_link_code
from app.schemas.conversation import (
  ConversationCreate,
  ConversationUpdate,
  ConversationRead,
  ConversationInfoRead,
  ConversationMessagesRead,
  LineMessageRead,
)

router = APIRouter(
  prefix="/conversations",
  tags=["conversations"],
)

async def get_conversation_with_item_types(
  db: AsyncSession,
  conversation_id: UUID,
) -> LineConversation:
  result = await db.execute(
    select(LineConversation)
    .options(
      selectinload(LineConversation.item_type_links)
      .selectinload(ConversationItemTypeLink.item_type)
    )
    .where(LineConversation.id == conversation_id)
  )

  return result.scalar_one()

@router.get(
  "/{conversation_id}/info",
  response_model=ConversationInfoRead,
)
async def get_conversation_info(
    conversation_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
  result = await db.execute(
    select(LineConversation).where(
      LineConversation.id == conversation_id,
    )
  )
  conversation = result.scalar_one_or_none()

  if (
    conversation is None
    or (
      current_user.role != "admin"
      and conversation.company_id != current_user.company_id
    )
  ):
    raise HTTPException(status_code=404, detail="Conversation not found")

  return conversation

@router.post(
  "",
  response_model=ConversationRead,
  status_code=status.HTTP_201_CREATED,
)
async def create_conversation(
  payload: ConversationCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role != "admin":
    require_company_manager(current_user, payload.company_id)

  if payload.project_id:
    project = await db.get(Project, payload.project_id)
    if not project:
      raise HTTPException(status_code=404, detail="Project not found")
    if project.company_id != payload.company_id:
      raise HTTPException(status_code=400, detail="Project does not belong to this company")

  line_link_code = await generate_unique_conversation_line_link_code(db)

  item_links = []

  if payload.item_type_ids:
    result = await db.execute(
      select(ConversationItemType).where(
        ConversationItemType.id.in_(payload.item_type_ids),
        ConversationItemType.company_id == payload.company_id,
        ConversationItemType.is_active == True
      )
    )

    item_types = result.scalars().all()

    if len(item_types) != len(set(payload.item_type_ids)):
      raise HTTPException(
        status_code=400,
        detail="One or more conversation item types are invalid.",
      )

    item_links = [
      ConversationItemTypeLink(item_type=item_type)
      for item_type in item_types
    ]

  conversation = LineConversation(
    name=payload.name,
    company_id=payload.company_id,
    project_id=payload.project_id,
    line_link_code=line_link_code,
    item_type_links=item_links,
  )

  db.add(conversation)

  await db.commit()

  return await get_conversation_with_item_types(
    db,
    conversation.id,
  )

@router.patch(
  "/{conversation_id}",
  response_model=ConversationRead,
)
async def update_conversation(
  conversation_id: UUID,
  payload: ConversationUpdate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  result = await db.execute(
    select(LineConversation)
    .options(
      selectinload(LineConversation.item_type_links)
    )
    .where(LineConversation.id == conversation_id)
  )
  conversation = result.scalar_one_or_none()

  if not conversation:
    raise HTTPException(status_code=404, detail="Conversation not found")

  if current_user.role != "admin":
    require_company_manager(current_user, conversation.company_id)

  update_data = payload.model_dump(exclude_unset=True)

  item_type_ids = update_data.pop("item_type_ids", None)

  if "project_id" in update_data and update_data["project_id"] is not None:
    project_id = update_data["project_id"]

    project = await db.get(Project, project_id)

    if not project:
      raise HTTPException(
        status_code=404,
        detail="Project not found",
      )

    if project.company_id != conversation.company_id:
      raise HTTPException(
        status_code=400,
        detail="Project does not belong to this company",
      )

  for field, value in update_data.items():
    setattr(conversation, field, value)

  if item_type_ids is not None:
    result = await db.execute(
      select(ConversationItemType).where(
        ConversationItemType.id.in_(item_type_ids),
        ConversationItemType.company_id == conversation.company_id,
      )
    )

    item_types = result.scalars().all()

    if len(item_types) != len(set(item_type_ids)):
      raise HTTPException(
        status_code=400,
        detail="One or more conversation item types are invalid.",
      )

    await db.execute(
      delete(ConversationItemTypeLink).where(
        ConversationItemTypeLink.conversation_id == conversation.id
      )
    )

    await db.flush()

    conversation.item_type_links = [
      ConversationItemTypeLink(item_type=item_type)
      for item_type in item_types
    ]

  await db.commit()

  return await get_conversation_with_item_types(
    db,
    conversation.id,
  )

@router.delete(
  "/{conversation_id}",
  status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_conversation(
  conversation_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  result = await db.execute(
    select(LineConversation).where(LineConversation.id == conversation_id)
  )
  conversation = result.scalar_one_or_none()

  if not conversation:
    raise HTTPException(status_code=404, detail="Conversation not found")

  if current_user.role != "admin":
    require_company_manager(current_user, conversation.company_id)

  await db.delete(conversation)
  await db.commit()

  return None



@router.get(
  "/{conversation_id}/messages",
  response_model=list[LineMessageRead],
)
async def get_conversation_messages(
  conversation_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  result = await db.execute(
    select(LineConversation).where(
      LineConversation.id == conversation_id
    )
  )
  conversation = result.scalar_one_or_none()

  if not conversation:
    raise HTTPException(
      status_code=404,
      detail="Conversation not found",
    )

  if (
    current_user.role != "admin"
    and conversation.company_id != current_user.company_id
  ):
    raise HTTPException(
      status_code=404,
      detail="Conversation not found",
    )

  result = await db.execute(
    select(LineMessage)
    .options(
      selectinload(LineMessage.image_links)
      .selectinload(LineMessageImageLink.image)
      .selectinload(Image.tag_links)
      .selectinload(ImageTagLink.tag)
    )
    .where(
      LineMessage.conversation_id == conversation_id,
      LineMessage.company_id == conversation.company_id,
    )
    .order_by(
      LineMessage.line_timestamp.asc(),
      LineMessage.created_at.asc(),
      LineMessage.id.asc(),
    )
  )

  messages = result.scalars().all()

  sender_first_seen = {}

  for message in messages:
    sender_id = message.sender_line_user_id

    if sender_id not in sender_first_seen:
      sender_first_seen[sender_id] = (
        message.line_timestamp or message.created_at,
        str(message.id),
      )

  ordered_senders = sorted(
    sender_first_seen,
    key=lambda sender_id: (
      sender_first_seen[sender_id][0] is None,
      sender_first_seen[sender_id][0],
      sender_first_seen[sender_id][1],
    ),
  )

  sender_numbers = {
    sender_id: index + 1
    for index, sender_id in enumerate(ordered_senders)
  }

  return [
    {
      "id": message.id,
      "line_platform_message_id": message.line_platform_message_id,
      "message_type": message.message_type.value,
      "text": message.text,
      "sender_line_user_id": message.sender_line_user_id,
      "sender_number": sender_numbers[message.sender_line_user_id],
      "line_user_id": message.line_user_id,
      "line_chat_id": message.line_chat_id,
      "line_chat_type": (
        message.line_chat_type.value
        if message.line_chat_type else None
      ),
      "line_timestamp": message.line_timestamp,
      "created_at": message.created_at,
      "images": [
        {
          "image_id": link.image_id,
          "image": (
            {
              "id": link.image.id,
              "image_url": link.image.image_url,
              "status": link.image.status,
              "width": link.image.width,
              "height": link.image.height,
              "description": link.image.description,
              "created_at": link.image.created_at,
              "processing_type": link.image.processing_type,
              "tags": [
                {
                  "id": tag_link.tag.id,
                  "name": tag_link.tag.name,
                  "description": tag_link.tag.description,
                }
                for tag_link in link.image.tag_links
                if tag_link.tag is not None
              ],
            }
            if link.image else None
          ),
        }
        for link in message.image_links
      ],
    }
    for message in messages
  ]