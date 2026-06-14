from datetime import date
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy import desc, func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.core.dependencies import get_current_user, require_company_manager
from app.db.models import Company, Project, User, ReportImageTag, ProjectGuestLink
from app.db.session import get_db
from app.schemas.company import (
  CompanyCreate,
  CompanyProjectRead,
  CompanyRead,
  CompanyUpdate,
  CompanyWithProjectsAndUsers,
  ReportImageTagCreate,
  ReportImageTagUpdate
)
from app.schemas.invitation import CompanyInvitationCreate
from app.services.invitations import create_company_invitation

import stripe

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
  background_tasks: BackgroundTasks,
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

  try:
    stripe_customer = stripe.Customer.create(
      name=company.name,
      metadata={"company_id": str(company.id)},
    )
    company.stripe_customer_id = stripe_customer.id
    await db.commit()
    await db.refresh(company)
  except stripe.error.StripeError:
    pass

  if payload.manager_email:
    invitation_payload = CompanyInvitationCreate(
      email=payload.manager_email,
      company_id=company.id,
      role="manager",
    )

    await create_company_invitation(
      invitation_payload,
      db,
      current_user,
      background_tasks,
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

  payment_method_name = await get_payment_method_display(company)

  return CompanyWithProjectsAndUsers(
    id=company.id,
    name=company.name,
    corporate_number=company.corporate_number,
    has_payment_method=company.has_payment_method,
    payment_method_name=payment_method_name,
    billing_exempt=company.billing_exempt,
    created_at=company.created_at,
    updated_at=company.updated_at,
    users=company.users,
    projects=projects
  )

async def get_payment_method_display(company: Company) -> Optional[str]:
  if not company.has_payment_method or not company.stripe_customer_id:
    return None

  try:
    payment_methods = stripe.PaymentMethod.list(
      customer=company.stripe_customer_id,
      type="card",
    )
  except stripe.error.StripeError:
    logger.exception(
      "Failed to fetch payment method for company %s", company.id
    )
    return None

  if not payment_methods.data:
    return None

  pm = payment_methods.data[0]

  brand_names = {
    "visa": "Visa",
    "mastercard": "Mastercard",
    "jcb": "JCB",
    "amex": "American Express",
  }

  brand = brand_names.get(pm.card.brand, pm.card.brand.capitalize())

  return f"{brand} •••• {pm.card.last4}"


@router.patch(
  "/{company_id}",
  response_model=CompanyRead,
)
async def update_company(
  company_id: UUID,
  payload: CompanyUpdate,
  current_user: User = Depends(get_current_user),
  db: AsyncSession = Depends(get_db),
):
  if current_user.role != "admin":
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Only admins can update companies",
    )

  result = await db.execute(
    select(Company).where(Company.id == company_id)
  )
  company = result.scalar_one_or_none()

  if not company:
    raise HTTPException(status_code=404, detail="Company not found")

  if payload.corporate_number is not None:
    stmt = select(Company).where(
      Company.corporate_number == payload.corporate_number,
      Company.id != company_id,
    )
    result = await db.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing:
      raise HTTPException(
        status_code=409,
        detail="Corporate number already in use",
      )

    company.corporate_number = payload.corporate_number

  if payload.name is not None:
    company.name = payload.name

  if payload.billing_exempt is not None:
    company.billing_exempt = payload.billing_exempt

    if payload.billing_exempt and company.stripe_subscription_id:
      try:
        stripe.Subscription.cancel(company.stripe_subscription_id)
      except stripe.error.StripeError:
        logger.exception(
          "Failed to cancel subscription for exempt company %s", company.id
        )
      company.stripe_subscription_id = None
      company.stripe_subscription_status = None

  await db.commit()
  await db.refresh(company)

  return company

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

@router.get("/{company_id}/guests")
async def list_company_guests(
  company_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  result = await db.execute(
    select(
      User.id,
      User.email,
      User.first_name,
      User.last_name,
      Project.id.label("project_id"),
      Project.name.label("project_name"),
      ProjectGuestLink.id.label("guest_link_id"),
    )
    .join(ProjectGuestLink, ProjectGuestLink.user_id == User.id)
    .join(Project, Project.id == ProjectGuestLink.project_id)
    .where(
      Project.company_id == company_id,
      or_(
        User.company_id.is_(None),
        User.company_id != company_id,
      ),
    )
    .order_by(User.email.asc(), Project.name.asc())
  )

  rows = result.all()

  guests: dict = {}

  for row in rows:
    user_id = str(row.id)

    if user_id not in guests:
      guests[user_id] = {
        "id": row.id,
        "email": row.email,
        "first_name": row.first_name,
        "last_name": row.last_name,
        "projects": [],
      }

    guests[user_id]["projects"].append({
      "project_id": row.project_id,
      "project_name": row.project_name,
      "guest_link_id": row.guest_link_id,
    })

  return list(guests.values())

@router.post("/{company_id}/billing/setup-intent")
async def create_setup_intent(
  company_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  company = await db.get(Company, company_id)
  if not company:
    raise HTTPException(status_code=404, detail="Company not found")

  if not company.stripe_customer_id:
    stripe_customer = stripe.Customer.create(
      name=company.name,
      metadata={"company_id": str(company.id)},
    )
    company.stripe_customer_id = stripe_customer.id
    await db.commit()
    await db.refresh(company)

  intent = stripe.SetupIntent.create(
    customer=company.stripe_customer_id,
  )

  return {"client_secret": intent.client_secret}