from datetime import datetime
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, case, or_, func
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.db.models import Project, Company, User, ProjectStatus, ProjectGuestLink
from app.schemas.project import ProjectCreate, ProjectUpdate, ProjectRead, ProjectWithReports, ProjectWithCompanyName
from app.core.dependencies import get_current_user, require_company_member, require_company_manager, require_project_access

import logging
import stripe

import logging
import stripe

router = APIRouter(prefix="/projects", tags=["Projects"])

logger = logging.getLogger(__name__)

@router.get("", response_model=List[ProjectWithCompanyName])
async def list_projects(
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user)
):
  if current_user.role == "admin":
    order_by_clause = [
      case((Project.status == ProjectStatus.requested, 0), else_=1),
      desc(Project.updated_at)
    ]

    stmt = (
      select(
        Project,
        Company.name.label("company_name"),
      )
      .outerjoin(Company, Project.company_id == Company.id)
      .options(selectinload(Project.company))
      .order_by(*order_by_clause)
      .limit(25)
    )

    result = await db.execute(stmt)

    rows = result.all()

    projects = []

    for project, company_name in rows:
      project.company_name = company_name
      projects.append(project)

    return projects

  else:
    company_projects = []
    guest_projects = []

    if current_user.company_id:
      company_result = await db.execute(
        select(Project)
        .options(selectinload(Project.company))
        .where(Project.company_id == current_user.company_id)
        .order_by(desc(Project.updated_at))
        .limit(25)
      )
      company_projects = company_result.scalars().all()

    guest_result = await db.execute(
      select(Project)
      .options(selectinload(Project.company))
      .join(ProjectGuestLink, ProjectGuestLink.project_id == Project.id)
      .where(
        ProjectGuestLink.user_id == current_user.id,
        or_(
          Project.company_id != current_user.company_id,
          current_user.company_id == None,
        ),
      )
      .order_by(desc(Project.updated_at))
    )
    guest_projects = guest_result.scalars().all()

    seen = set()
    all_projects = []
    for project in list(company_projects) + list(guest_projects):
      if project.id not in seen:
        seen.add(project.id)
        all_projects.append(project)

    all_projects = sorted(all_projects, key=lambda p: p.updated_at, reverse=True)[:25]

    return all_projects

@router.get("/{project_id}", response_model=ProjectWithReports)
async def get_project(
  project_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role == "admin":
    stmt = (
      select(
        Project,
        Company.name.label("company_name"),
      )
      .outerjoin(Company, Project.company_id == Company.id)
      .where(Project.id == project_id)
      .options(selectinload(Project.reports))
    )

    result = await db.execute(stmt)

    row = result.first()

    if not row:
      raise HTTPException(status_code=404, detail="Project not found")

    project, company_name = row

    project.company_name = company_name

  else:
    stmt = (
      select(Project)
      .where(Project.id == project_id)
      .options(selectinload(Project.reports))
    )

    result = await db.execute(stmt)
    project = result.scalars().first()

    if not project:
      raise HTTPException(status_code=404, detail="Project not found")

    await require_project_access(current_user, project_id, project.company_id, db)

  return project

@router.post(
  "",
  response_model=ProjectWithReports,
  status_code=status.HTTP_201_CREATED,
)
async def create_project(
  payload: ProjectCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  company = await db.get(Company, payload.company_id)

  if not company:
    raise HTTPException(status_code=404, detail="Company not found")

  if not await has_payment_method(company):
    raise HTTPException(
      status_code=402,
      detail="A payment method is required before creating a project",
    )

  project = Project(
    **payload.model_dump(),
    status=ProjectStatus.active
  )

  db.add(project)
  await db.commit()
  await db.refresh(project)

  await ensure_subscription(company, db)
  await sync_subscription_quantity(company, db)

  return ProjectWithReports(
    id=project.id,
    name=project.name,
    description=project.description,
    status=project.status,
    company_id=project.company_id,
    reports=[]
  )

@router.patch("/{project_id}", response_model=ProjectWithReports)
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
    require_company_manager(current_user, project.company_id)

  previous_status = project.status

  for field, value in payload.model_dump(exclude_unset=True).items():
    setattr(project, field, value)

  await db.commit()

  status_changed = (
    payload.status is not None and payload.status != previous_status
  )

  if status_changed:
    company = await db.get(Company, project.company_id)
    await sync_subscription_quantity(company, db)

  stmt = (
    select(Project)
    .where(Project.id == project_id)
    .options(selectinload(Project.reports))
  )

  result = await db.execute(stmt)
  project = result.scalars().first()

  return project

async def has_payment_method(company: Company) -> bool:
  if company.billing_exempt:
    return True

  if not company.stripe_customer_id:
    return False

  payment_methods = stripe.PaymentMethod.list(
    customer=company.stripe_customer_id,
    type="card",
  )
  return len(payment_methods.data) > 0

async def ensure_subscription(company: Company, db: AsyncSession):
  if company.billing_exempt:
    return
    
  if company.stripe_subscription_id:
    return

  try:
    subscription = stripe.Subscription.create(
      customer=company.stripe_customer_id,
      items=[{"price": settings.STRIPE_PROJECT_PRICE_ID, "quantity": 0}],
      trial_period_days=14,
      trial_settings={"end_behavior": {"missing_payment_method": "cancel"}},
      payment_behavior="default_incomplete",
    )
    company.stripe_subscription_id = subscription.id
    company.stripe_subscription_status = subscription.status
    await db.commit()
  except stripe.error.StripeError:
    logger.exception(
      "Failed to create Stripe subscription for company %s",
      company.id,
    )

async def sync_subscription_quantity(company: Company, db: AsyncSession):
  if company.billing_exempt:
    return

  if not company.stripe_subscription_id:
    return

  result = await db.execute(
    select(func.count())
    .select_from(Project)
    .where(
      Project.company_id == company.id,
      Project.status == ProjectStatus.active,
    )
  )
  active_count = result.scalar_one()

  try:
    subscription = stripe.Subscription.retrieve(company.stripe_subscription_id)
    item_id = subscription["items"]["data"][0]["id"]

    stripe.SubscriptionItem.modify(
      item_id,
      quantity=active_count,
      proration_behavior="create_prorations",
    )
  except stripe.error.StripeError:
    logger.exception(
      "Failed to sync Stripe subscription quantity for company %s",
      company.id,
    )

@router.delete(
  "/{project_id}/guests/{guest_link_id}",
  status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_project_guest(
  project_id: UUID,
  guest_link_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  project = await db.get(Project, project_id)
  if not project:
    raise HTTPException(status_code=404, detail="Project not found")

  result = await db.execute(
    select(ProjectGuestLink).where(
      ProjectGuestLink.id == guest_link_id,
      ProjectGuestLink.project_id == project_id,
    )
  )
  guest_link = result.scalar_one_or_none()

  if not guest_link:
    raise HTTPException(status_code=404, detail="Guest link not found")

  if current_user.role == "admin":
    pass
  elif current_user.role == "manager" and current_user.company_id == project.company_id:
    pass
  elif guest_link.user_id == current_user.id:
    pass
  else:
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Not authorized to remove this guest",
    )

  await db.delete(guest_link)
  await db.commit()

  return None
