from uuid import UUID
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api.invitations import invite_user
from app.core.dependencies import get_current_user, require_company_member, require_company_manager
from app.db.session import get_db
from app.db.models import Company, User, Project
from app.schemas.company import CompanyCreate, CompanyRead
from app.schemas.invitation import InvitationCreate
from app.schemas.project import ProjectCreate, ProjectRead
from app.schemas.user import UserRead

router = APIRouter(prefix="/companies", tags=["companies"])

@router.get("/", response_model=List[CompanyRead])
async def list_companies(
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role != "admin":
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Only admins can list all companies",
    )

  stmt = (
    select(Company)
    .outerjoin(Project, Project.company_id == Company.id)
    .group_by(Company.id)
    .order_by(desc(func.max(Project.created_at)))
    .limit(25)
  )

  result = await db.execute(stmt)
  companies = result.scalars().all()

  return companies

@router.post(
  "/",
  response_model=CompanyRead,
  status_code=status.HTTP_201_CREATED,
)
async def create_company(
  payload: CompanyCreate,
  current_user: User = Depends(get_current_user),
  db: AsyncSession = Depends(get_db),
):
  if current_user.role != "admin":
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Only admins can create companies",
    )

  company = Company(name=payload.name)
  db.add(company)

  await db.commit()
  await db.refresh(company)

  if payload.manager_email:
    invitation_payload = InvitationCreate(
      email=payload.manager_email,
      company_id=company.id,
      role="manager",
    )
    
    await invite_user(
      payload=invitation_payload,
      current_user=current_user,
      db=db,
    )

  return company