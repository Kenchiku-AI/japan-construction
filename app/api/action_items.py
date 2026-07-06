from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_db
from app.db.models.user import User
from app.db.models.project import Project
from app.db.models.action_item import ActionItem, ActionItemStatus
from app.schemas.action_item import ActionItemCreate, ActionItemRead, ActionItemUpdate
from app.services.billing import can_use_billed_features
from app.core.dependencies import (
  get_current_user,
  require_company_manager,
  require_project_access,
)

router = APIRouter(
  prefix="/action-items",
  tags=["action-items"],
)

@router.post(
  "",
  response_model=ActionItemRead,
  status_code=status.HTTP_201_CREATED,
)
async def create_action_item(
  payload: ActionItemCreate,
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

  action_item = ActionItem(
    project_id=payload.project_id,
    name=payload.name,
    description=payload.description,
    status=ActionItemStatus.new,
    assignee_id=payload.assignee_id,
    scheduled_date=payload.scheduled_date,
  )

  db.add(action_item)
  await db.commit()
  await db.refresh(action_item)

  return action_item

@router.patch(
  "/{action_item_id}",
  response_model=ActionItemRead,
)
async def update_action_item(
  action_item_id: UUID,
  payload: ActionItemUpdate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  result = await db.execute(
    select(ActionItem).where(ActionItem.id == action_item_id)
  )
  action_item = result.scalar_one_or_none()

  if not action_item:
    raise HTTPException(status_code=404, detail="Action item not found")

  project = await db.get(Project, action_item.project_id)
  if not project:
    raise HTTPException(status_code=404, detail="Project not found")

  allowed, reason = await can_use_billed_features(project.company_id, db)
  if not allowed and current_user.role != "admin":
    raise HTTPException(status_code=402, detail=reason)

  if current_user.role != "admin":
    await require_project_access(current_user, action_item.project_id, project.company_id, db)

  for field, value in payload.model_dump(exclude_unset=True).items():
    setattr(action_item, field, value)

  await db.commit()
  await db.refresh(action_item)

  return action_item

@router.delete(
  "/{action_item_id}",
  status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_action_item(
  action_item_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  result = await db.execute(
    select(ActionItem).where(ActionItem.id == action_item_id)
  )
  action_item = result.scalar_one_or_none()

  if not action_item:
    raise HTTPException(status_code=404, detail="Action item not found")

  project = await db.get(Project, action_item.project_id)
  if not project:
    raise HTTPException(status_code=404, detail="Project not found")

  allowed, reason = await can_use_billed_features(project.company_id, db)
  if not allowed and current_user.role != "admin":
    raise HTTPException(status_code=402, detail=reason)

  if current_user.role != "admin":
    require_company_manager(current_user, project.company_id)

  await db.delete(action_item)
  await db.commit()

  return None