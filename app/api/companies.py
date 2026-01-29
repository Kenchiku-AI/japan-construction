from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID

from app.api.invitations import invite_user
from app.core.dependencies import get_current_user, require_company_member, require_company_manager
from app.db.session import get_db
from app.db.models import Company, User
from app.schemas.company import CompanyCreate, CompanyRead
from app.schemas.invitation import InvitationCreate
from app.schemas.project import ProjectCreate, ProjectRead
from app.schemas.user import UserRead
from app.services.email import send_project_request_email

router = APIRouter(prefix="/companies", tags=["companies"])

@router.post(
  "/", 
  response_model=CompanyRead, 
  status_code=status.HTTP_201_CREATED
)
def create_company(
  payload: CompanyCreate,
  current_user: User = Depends(get_current_user),
  db: Session = Depends(get_db),
):
  if current_user.role != "admin":
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Only admins can create companies",
    )

  company = Company(name=payload.name)
  db.add(company)
  db.commit()
  db.refresh(company)

  if payload.manager_email:
    invitation_payload = InvitationCreate(
      email=payload.manager_email,
      company_id=company.id,
      role="manager",
    )

    invite_user(
      payload=invitation_payload,
      current_user=current_user,
      db=db,
    )

  return company

@router.get("/{company_id}", response_model=CompanyRead)
def get_company(
  company_id: UUID,
  current_user: User = Depends(get_current_user),
  db: Session = Depends(get_db),
):
  require_company_member(current_user, company_id)

  company = db.get(Company, company_id)
  if not company:
    raise HTTPException(status_code=404, detail="Company not found")

  return company

@router.get("/{company_id}/users", response_model=List[UserRead])
def list_company_users(
    company_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
  require_company_member(current_user, company_id)

  return (
    db.query(User)
    .filter(User.company_id == company_id)
    .all()
  )

@router.post(
  "/{company_id}/projects",
  response_model=ProjectRead,
  status_code=status.HTTP_201_CREATED,
)
def create_project(
  company_id: UUID,
  project_in: ProjectCreate,
  db: Session = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  company = db.get(Company, company_id)
  if not company:
    raise HTTPException(status_code=404, detail="Company not found")
  
  if current_user.role == "admin":
    status_value = ProjectStatus.active
  else:
    require_company_manager(current_user, company_id)

    if company.can_create_projects:
      status_value = ProjectStatus.active
    else:
      status_value = ProjectStatus.requested

      send_project_request_email(
        company_name=company.name,
        project_name=project.name,
        requested_by=current_user.email,
      )

  project = Project(
    **project_in.model_dump(),
    company_id=company_id,
    status=status_value,
  )

  db.add(project)
  db.commit()
  db.refresh(project)
  return project

@router.get(
  "/{company_id}/projects",
  response_model=list[ProjectRead],
)
def list_company_projects(
  company_id: UUID,
  db: Session = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  require_company_member(current_user, company_id)

  return (
    db.query(Project)
    .filter(Project.company_id == company_id)
    .all()
  )
