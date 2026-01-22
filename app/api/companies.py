from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID

from app.api.invitations import invite_user
from app.core.dependencies import get_current_user, require_company_member, require_company_manager
from app.db.session import get_db
from app.db.models import Company, User, CompanyUser
from app.schemas.company import CompanyCreate, CompanyRead, CompanyUserBase
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
  company_in: CompanyCreate,
  current_user: User = Depends(get_current_user),
  db: Session = Depends(get_db),
):
  if current_user.role != "admin":
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Only admins can create companies",
    )

  company = Company(name=company_in.name)
  db.add(company)
  db.commit()
  db.refresh(company)

  if company_in.manager_email:
    invitation_payload = InvitationCreate(
      email=company_in.manager_email,
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
  company = db.get(Company, company_id)
  if not company:
    raise HTTPException(status_code=404, detail="Company not found")

  # Check that user belongs to company
  company_user = db.query(CompanyUser).filter_by(
    company_id=company_id, user_id=current_user.id
  ).first()
  if not company_user:
    raise HTTPException(
      status_code=403,
      detail="You are not a member of this company"
    )

  return company

@router.get("/{company_id}/users", response_model=List[UserRead])
def list_company_users(
  company_id: UUID,
  current_user: User = Depends(get_current_user),
  db: Session = Depends(get_db),
):
  company_user = db.query(CompanyUser).filter_by(
    company_id=company_id, user_id=current_user.id
  ).first()
  if not company_user:
    raise HTTPException(
      status_code=403,
      detail="You are not a member of this company"
    )

  company_users = db.query(CompanyUser).filter_by(company_id=company_id).all()
  users = [cu.user for cu in company_users]
  return users

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

@router.put(
  "/{company_id}/users/{user_id}/role",
  status_code=status.HTTP_204_NO_CONTENT,
)
def update_company_user_role(
  company_id: UUID,
  user_id: UUID,
  payload: CompanyUserBase,
  db: Session = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  company = db.get(Company, company_id)
  if not company:
    raise HTTPException(status_code=404, detail="Company not found")

  if current_user.role != "admin":
    manager_relation = db.query(CompanyUser).filter(
      CompanyUser.company_id == company_id,
      CompanyUser.user_id == current_user.id,
      CompanyUser.role == "manager",
    ).first()

    if not manager_relation:
      raise HTTPException(
        status_code=403,
        detail="Forbidden: must be company manager or admin",
      )

  company_user = db.query(CompanyUser).filter(
    CompanyUser.company_id == company_id,
    CompanyUser.user_id == user_id,
  ).first()

  if not company_user:
    raise HTTPException(
      status_code=404,
      detail="User is not a member of this company",
    )

  company_user.role = payload.role

  db.commit()
