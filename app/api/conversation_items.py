from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy import select

from app.db.session import get_db
from app.db.models.user import User
from app.db.models.project import Project
from app.db.models.conversation_item import ConversationItem, ConversationItemStatus
from app.db.models.conversation_item_type import ConversationItemType
from app.schemas.conversation import (
  ConversationItemCreate, 
  ConversationItemRead, 
  ConversationItemUpdate,
)
from app.schemas.project import ConversationItemsGroupedRead
from app.services.billing import can_use_billed_features
from app.core.dependencies import (
  get_current_user,
  require_company_manager,
  require_project_access,
)

router = APIRouter(
  prefix="/conversation-items",
  tags=["conversation-items"],
)

@router.get(
  "",
  response_model=ConversationItemsGroupedRead,
)
async def get_conversation_items(
  project_id: UUID,
  conversation_item_type_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  project = await db.get(Project, project_id)
  if not project:
    raise HTTPException(status_code=404, detail="Project not found")

  allowed, reason = await can_use_billed_features(project.company_id, db)
  if not allowed and current_user.role != "admin":
    raise HTTPException(status_code=402, detail=reason)

  if current_user.role != "admin":
    await require_project_access(
      current_user,
      project_id,
      project.company_id,
      db,
    )

  result = await db.execute(
    select(ConversationItemType)
    .where(
      ConversationItemType.id == conversation_item_type_id,
      ConversationItemType.company_id == project.company_id,
    )
  )

  item_type = result.scalar_one_or_none()

  if not item_type:
    raise HTTPException(
      status_code=404,
      detail="Conversation item type not found",
    )

  result = await db.execute(
    select(ConversationItem)
    .where(
      ConversationItem.project_id == project_id,
      ConversationItem.conversation_item_type_id == conversation_item_type_id,
    )
    .options(
      selectinload(ConversationItem.item_type),
    )
    .order_by(
      ConversationItem.updated_at.desc(),
    )
  )

  items = result.scalars().all()

  return ConversationItemsGroupedRead(
    conversation_item_type_id=item_type.id,
    conversation_item_type_name=item_type.name,
    items=items,
  )

@router.post(
  "",
  response_model=ConversationItemRead,
  status_code=status.HTTP_201_CREATED,
)
async def create_conversation_item(
  payload: ConversationItemCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  project = await db.get(Project, payload.project_id)
  if not project:
    raise HTTPException(status_code=404, detail="Project not found")

  allowed, reason = await can_use_billed_features(project.company_id, db)
  if not allowed and current_user.role != "admin":
    raise HTTPException(status_code=402, detail=reason)

  if current_user.role != "admin":
    await require_project_access(current_user, payload.project_id, project.company_id, db)

  conversation_item = ConversationItem(
    conversation_item_type_id=payload.conversation_item_type_id,
    project_id=payload.project_id,
    name=payload.name,
    description=payload.description,
    status=ConversationItemStatus.new,
  )

  db.add(conversation_item)
  await db.commit()
  await db.refresh(conversation_item)

  return conversation_item

@router.patch(
  "/{conversation_item_id}",
  response_model=ConversationItemRead,
)
async def update_conversation_item(
  conversation_item_id: UUID,
  payload: ConversationItemUpdate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  result = await db.execute(
    select(ConversationItem).where(ConversationItem.id == conversation_item_id)
  )
  conversation_item = result.scalar_one_or_none()

  if not conversation_item:
    raise HTTPException(status_code=404, detail="Conversation item not found")

  project = await db.get(Project, conversation_item.project_id)
  if not project:
    raise HTTPException(status_code=404, detail="Project not found")

  allowed, reason = await can_use_billed_features(project.company_id, db)
  if not allowed and current_user.role != "admin":
    raise HTTPException(status_code=402, detail=reason)

  if current_user.role != "admin":
    await require_project_access(current_user, conversation_item.project_id, project.company_id, db)

  for field, value in payload.model_dump(exclude_unset=True).items():
    setattr(conversation_item, field, value)

  await db.commit()
  await db.refresh(conversation_item)

  return conversation_item

@router.delete(
  "/{conversation_item_id}",
  status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_conversation_item(
  conversation_item_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  result = await db.execute(
    select(ConversationItem).where(ConversationItem.id == conversation_item_id)
  )
  conversation_item = result.scalar_one_or_none()

  if not conversation_item:
    raise HTTPException(status_code=404, detail="Conversation item not found")

  project = await db.get(Project, conversation_item.project_id)
  if not project:
    raise HTTPException(status_code=404, detail="Project not found")

  allowed, reason = await can_use_billed_features(project.company_id, db)
  if not allowed and current_user.role != "admin":
    raise HTTPException(status_code=402, detail=reason)

  if current_user.role != "admin":
    require_company_manager(current_user, project.company_id)

  await db.delete(conversation_item)
  await db.commit()

  return None