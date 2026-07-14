from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.session import get_db
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

  conversation = LineConversation(
    name=payload.name,
    company_id=payload.company_id,
    project_id=payload.project_id,
    line_link_code=line_link_code,
  )

  db.add(conversation)
  await db.commit()
  await db.refresh(conversation)

  return conversation

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
    select(LineConversation).where(LineConversation.id == conversation_id)
  )
  conversation = result.scalar_one_or_none()

  if not conversation:
    raise HTTPException(status_code=404, detail="Conversation not found")

  if current_user.role != "admin":
    require_company_manager(current_user, conversation.company_id)

  for field, value in payload.model_dump(exclude_unset=True).items():
    setattr(conversation, field, value)

  await db.commit()
  await db.refresh(conversation)

  return conversation

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