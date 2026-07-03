from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.session import get_db
from app.db.models.user import User
from app.db.models.project import Project
from app.db.models.work_item import WorkItem, WorkItemStatus
from app.schemas.work_item import WorkItemCreate, WorkItemRead, WorkItemUpdate
from app.core.dependencies import (
  get_current_user,
  require_company_manager,
  require_project_access,
)

router = APIRouter(
  prefix="/work-items",
  tags=["work-items"],
)

@router.post(
  "",
  response_model=WorkItemRead,
  status_code=status.HTTP_201_CREATED,
)
async def create_work_item(
  payload: WorkItemCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  project = await db.get(Project, payload.project_id)
  if not project:
    raise HTTPException(status_code=404, detail="Project not found")

  if current_user.role != "admin":
    await require_project_access(current_user, payload.project_id, project.company_id, db)

  work_item = WorkItem(
    project_id=payload.project_id,
    name=payload.name,
    description=payload.description,
    status=WorkItemStatus.new,
    assignee_id=payload.assignee_id,
    scheduled_date=payload.scheduled_date,
  )

  db.add(work_item)
  await db.commit()
  await db.refresh(work_item)

  return work_item

@router.patch(
  "/{work_item_id}",
  response_model=WorkItemRead,
)
async def update_work_item(
  work_item_id: UUID,
  payload: WorkItemUpdate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  result = await db.execute(
    select(WorkItem).where(WorkItem.id == work_item_id)
  )
  work_item = result.scalar_one_or_none()

  if not work_item:
    raise HTTPException(status_code=404, detail="Work item not found")

  project = await db.get(Project, work_item.project_id)
  if not project:
    raise HTTPException(status_code=404, detail="Project not found")

  if current_user.role != "admin":
    await require_project_access(current_user, work_item.project_id, project.company_id, db)

  for field, value in payload.model_dump(exclude_unset=True).items():
    setattr(work_item, field, value)

  await db.commit()
  await db.refresh(work_item)

  return work_item

@router.delete(
  "/{work_item_id}",
  status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_work_item(
  work_item_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  result = await db.execute(
    select(WorkItem).where(WorkItem.id == work_item_id)
  )
  work_item = result.scalar_one_or_none()

  if not work_item:
    raise HTTPException(status_code=404, detail="Work item not found")

  project = await db.get(Project, work_item.project_id)
  if not project:
    raise HTTPException(status_code=404, detail="Project not found")

  if current_user.role != "admin":
    require_company_manager(current_user, project.company_id)

  await db.delete(work_item)
  await db.commit()

  return None