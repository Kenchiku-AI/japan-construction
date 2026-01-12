from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from uuid import UUID

from app.db.session import get_db
from app.db.models import Project, Company, User
from app.schemas.project import ProjectUpdate, ProjectRead
from app.core.dependencies import get_current_user, require_company_member, require_company_manager

router = APIRouter(prefix="/projects", tags=["Projects"])

@router.get(
  "/{project_id}",
  response_model=ProjectRead,
)
def get_project(
  project_id: UUID,
  db: Session = Depends(get_db),
):
  project = db.get(Project, project_id)
  if not project:
    raise HTTPException(status_code=404, detail="Project not found")

  require_company_member(project.company_id)(db=db)
  return project

@router.put(
  "/{project_id}",
  response_model=ProjectRead,
)
def update_project(
  project_id: UUID,
  project_in: ProjectUpdate,
  db: Session = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  project = db.get(Project, project_id)
  if not project:
    raise HTTPException(status_code=404, detail="Project not found")

  if current_user.role != "admin":
    if project.status != ProjectStatus.active:
      raise HTTPException(
        status_code=403,
        detail="Only active projects can be updated",
      )

    require_company_member(db, current_user, project.company_id)

  for field, value in project_in.model_dump(exclude_unset=True).items():
    setattr(project, field, value)

  db.commit()
  db.refresh(project)
  return project

@router.delete(
  "/{project_id}",
  status_code=status.HTTP_204_NO_CONTENT,
)
def delete_project(
  project_id: UUID,
  db: Session = Depends(get_db),
):
  project = db.get(Project, project_id)
  if not project:
    raise HTTPException(status_code=404, detail="Project not found")

  require_company_member(project.company_id)(db=db)

  db.delete(project)
  db.commit()
