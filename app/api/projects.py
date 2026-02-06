from datetime import datetime
from typing import List
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, case

from app.db.session import get_db
from app.db.models import Project, Company, User, ProjectStatus
from app.schemas.project import ProjectCreate, ProjectUpdate, ProjectWithCompany
from app.core.dependencies import get_current_user, require_company_member, require_company_manager
from app.services.email import send_project_request_email

router = APIRouter(prefix="/projects", tags=["Projects"])

@router.get("", response_model=List[ProjectWithCompany])
async def list_projects(
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user)
):
  if current_user.role == "admin":
    order_by_clause = [
      case((Project.status == ProjectStatus.requested, 0), else_=1),
      desc(Project.updated_at)
    ]
    stmt = select(Project).order_by(*order_by_clause).limit(25)
  else:
    if current_user.company_id is None:
      return []

    stmt = (
      select(Project)
      .where(Project.company_id == current_user.company_id)
      .order_by(desc(Project.updated_at))
      .limit(25)
    )

  result = await db.execute(stmt)
  projects = result.scalars().all()
  return projects

@router.post(
  "",
  response_model=ProjectWithCompany,
  status_code=status.HTTP_201_CREATED,
)
async def create_project(
  payload: ProjectCreate,
  background_tasks: BackgroundTasks,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  company = await db.get(Company, payload.company_id)
  if not company:
    raise HTTPException(status_code=404, detail="Company not found")

  if current_user.role == "admin":
    status_value = ProjectStatus.active
  else:
    require_company_manager(current_user, payload.company_id)
    status_value = ProjectStatus.requested

    background_tasks.add_task(
      send_project_request_email,
      company_name=company.name,
      project_name=payload.name,
      requested_by=current_user.email,
    )

  project = Project(
    **payload.model_dump(),
    status=status_value,
  )

  db.add(project)
  await db.commit()
  await db.refresh(project)

  return project

@router.put("/{project_id}", response_model=ProjectWithCompany)
async def update_project(
  project_id: UUID,
  payload: ProjectUpdate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  project = await db.get(Project, project_id)
  if not project:
    raise HTTPException(status_code=404, detail="Project not found")

  if current_user.role != "admin":
    if project.status != ProjectStatus.active:
      raise HTTPException(
        status_code=403,
        detail="Only active projects can be updated",
      )
    require_company_member(current_user, project.company_id)

  for field, value in payload.model_dump(exclude_unset=True).items():
    setattr(project, field, value)

  await db.commit()
  await db.refresh(project)

  return project
