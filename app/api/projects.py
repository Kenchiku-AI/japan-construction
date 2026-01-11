from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.db.models import Project, Company
from app.schemas.project import ProjectUpdate, ProjectRead
from app.core.dependencies import require_company_member, require_company_admin

router = APIRouter(prefix="/projects", tags=["Projects"])

@router.get(
  "/{project_id}",
  response_model=ProjectRead,
)
def get_project(
  project_id: int,
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
  project_id: int,
  project_in: ProjectUpdate,
  db: Session = Depends(get_db),
):
  project = db.get(Project, project_id)
  if not project:
    raise HTTPException(status_code=404, detail="Project not found")

  require_company_member(project.company_id)(db=db)

  if project_in.name is not None:
    project.name = project_in.name
  if project_in.description is not None:
    project.description = project_in.description

  db.commit()
  db.refresh(project)
  return project

@router.delete(
  "/{project_id}",
  status_code=status.HTTP_204_NO_CONTENT,
)
def delete_project(
  project_id: int,
  db: Session = Depends(get_db),
):
  project = db.get(Project, project_id)
  if not project:
    raise HTTPException(status_code=404, detail="Project not found")

  require_company_admin(project.company_id)(db=db)

  db.delete(project)
  db.commit()
