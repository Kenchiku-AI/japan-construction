from datetime import date
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.api.invitations import invite_user
from app.core.dependencies import get_current_user, require_company_manager
from app.db.models import Company, Project, User, ReportImageTag
from app.db.session import get_db
from app.schemas.company import (
  CompanyCreate,
  CompanyProjectRead,
  CompanyRead,
  CompanyWithProjectsAndUsers,
  ReportImageTagCreate,
  ReportImageTagUpdate
)
from app.schemas.invitation import InvitationCreate

router = APIRouter(prefix="/companies", tags=["companies"])

@router.get("", response_model=List[CompanyRead])
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

@router.get("/search", response_model=List[CompanyRead])
async def search_companies(
  q: str,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role != "admin":
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Only admins can search companies",
    )

  search = f"%{q.lower()}%"

  stmt = (
    select(Company)
    .where(
      func.lower(Company.name).like(search)
      | Company.corporate_number.like(search)
    )
    .order_by(Company.name.asc())
    .limit(25)
  )

  result = await db.execute(stmt)
  companies = result.scalars().all()

  return companies

@router.post(
  "",
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

  company = Company(
    name=payload.name,
    corporate_number=payload.corporate_number
  )
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

@router.get(
  "/{company_id}",
  response_model=CompanyWithProjectsAndUsers,
)
async def get_company(
  company_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role != "admin" and current_user.company_id != company_id:
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Not authorized to view this company",
    )

  result = await db.execute(
    select(Company)
    .options(
      selectinload(Company.users),
      selectinload(Company.projects),
    )
    .where(Company.id == company_id)
  )
  company = result.scalar_one_or_none()

  if not company:
    raise HTTPException(status_code=404, detail="Company not found")

  projects = sorted(
    company.projects,
    key=lambda p: p.updated_at,
    reverse=True,
  )[:25]

  return CompanyWithProjectsAndUsers(
    id=company.id,
    name=company.name,
    corporate_number=company.corporate_number,
    users=company.users,
    projects=projects,
    created_at=company.created_at,
    updated_at=company.updated_at
  )

@router.get("/{company_id}/tags")
async def get_tags(
  company_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role != "admin" and current_user.company_id != company_id:
    raise HTTPException(
      status_code=403,
      detail="Not authorized"
    )

  stmt = (
    select(ReportImageTag)
    .where(ReportImageTag.company_id == company_id)
    .order_by(ReportImageTag.name.asc())
  )

  result = await db.execute(stmt)
  tags = result.scalars().all()

  return tags

@router.post("/{company_id}/tags")
async def create_tag(
  company_id: UUID,
  payload: ReportImageTagCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role != "admin":
    require_company_manager(current_user, company_id)

  tag = ReportImageTag(
    company_id=company_id,
    name=payload.name,
    description=payload.description
  )

  db.add(tag)
  await db.commit()
  await db.refresh(tag)

  return tag

@router.patch("/{company_id}/tags/{tag_id}")
async def update_tag(
  company_id: UUID,
  tag_id: UUID,
  payload: ReportImageTagUpdate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role != "admin":
    require_company_manager(current_user, company_id)

  stmt = (
    select(ReportImageTag)
    .where(
      ReportImageTag.id == tag_id,
      ReportImageTag.company_id == company_id
    )
  )

  result = await db.execute(stmt)
  tag = result.scalar_one_or_none()

  if not tag:
    raise HTTPException(
      status_code=404,
      detail="Tag not found"
    )

  if payload.name is not None:
    tag.name = payload.name

  if payload.description is not None:
    tag.description = payload.description

  await db.commit()
  await db.refresh(tag)

  return tag

@router.delete("/{company_id}/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(
  company_id: UUID,
  tag_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role != "admin":
    require_company_manager(current_user, company_id)

  stmt = (
    select(ReportImageTag)
    .where(
      ReportImageTag.id == tag_id,
      ReportImageTag.company_id == company_id
    )
  )

  result = await db.execute(stmt)
  tag = result.scalar_one_or_none()

  if not tag:
    raise HTTPException(
      status_code=404,
      detail="Tag not found"
    )

  await db.delete(tag)
  await db.commit()

  return None