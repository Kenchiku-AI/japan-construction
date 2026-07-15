from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.db.models.conversation_item_type import (
  ConversationItemType,
  ConversationItemTypeLink,
)
from app.db.models.user import User
from app.db.models.project import Project
from app.db.models.line_conversation import LineConversation
from app.core.dependencies import get_current_user, require_company_manager
from app.core.security import generate_unique_conversation_line_link_code
from app.schemas.conversation import (
  ConversationCreate,
  ConversationUpdate,
  ConversationRead,
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