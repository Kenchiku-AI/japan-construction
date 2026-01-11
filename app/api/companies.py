from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from app.db.session import get_db
from app.db.models import Company, User, CompanyUser
from app.schemas.company import CompanyCreate, CompanyRead
from app.schemas.project import ProjectCreate, ProjectRead
from app.schemas.user import UserRead
from app.core.dependencies import get_current_user

router = APIRouter(prefix="/companies", tags=["companies"])

@router.post("/", response_model=CompanyRead)
def create_company(
  company_in: CompanyCreate,
  current_user: User = Depends(get_current_user),
  db: Session = Depends(get_db),
):
  company = Company(name=company_in.name)
  db.add(company)
  db.commit()
  db.refresh(company)

  company_user = CompanyUser(
      company_id=company.id,
      user_id=current_user.id,
      role="admin"
  )
  db.add(company_user)
  db.commit()

  return company

@router.get("/{company_id}", response_model=CompanyRead)
def get_company(
  company_id: int,
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
  company_id: int,
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
  status_code=201,
)
def create_project(
  company_id: int,
  project_in: ProjectCreate,
  db: Session = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  require_company_admin(db, current_user, company_id)

  project = Project(
    **project_in.model_dump(),
    company_id=company_id,
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
  company_id: int,
  db: Session = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  require_company_member(db, current_user, company_id)

  return (
    db.query(Project)
    .filter(Project.company_id == company_id)
    .all()
  )

