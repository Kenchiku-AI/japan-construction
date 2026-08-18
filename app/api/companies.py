import time
from datetime import datetime, timedelta, timezone
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks
from sqlalchemy import and_, desc, func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.dependencies import get_current_user, require_company_manager
from app.db.models import (
  Company,
  Project,
  ProjectStatus,
  Report,
  User,
  ImageTag,
  ProjectGuestLink,
  BillingPlan,
  ConversationItem
)
from app.db.session import get_db
from app.schemas.company import (
  CompanyCreate,
  CompanyCreateResponse,
  CompanyRead,
  CompanyUserRead,
  CompanyWithMetrics,
  CompanyUpdate,
  CompanyWithLists,
  ImageTagCreate,
  ImageTagUpdate
)
from app.schemas.conversation import (
  ConversationItemTypeCreate,
  ConversationItemTypeUpdate,
  ConversationItemTypeRead,
)
from app.schemas.invitation import CompanyInvitationCreate
from app.services.billing import (
  get_payment_method_display,
  can_use_billed_features,
  get_billing_status,
  create_subscription
)
from app.services.invitations import create_company_invitation
from app.services.email import send_company_created_admin_email
from app.db.models.conversation_item_type import ConversationItemType, ConversationItemTypeLink
from app.db.models.custom_field import CustomField, CustomFieldCompanyLink
from app.schemas.conversation import ConversationItemTypeCreate, ConversationItemTypeUpdate

import stripe
import logging

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/companies", tags=["companies"])

@router.get("", response_model=List[CompanyWithMetrics])
async def list_companies(
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role != "admin":
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Only admins can list all companies",
    )

  thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)

  active_projects_count = (
    select(func.count(Project.id))
    .where(
      Project.company_id == Company.id,
      Project.status == ProjectStatus.active,
    )
    .correlate(Company)
    .scalar_subquery()
  )

  new_projects_count = (
    select(func.count(Project.id))
    .where(
      Project.company_id == Company.id,
      Project.created_at >= thirty_days_ago,
    )
    .correlate(Company)
    .scalar_subquery()
  )

  # NOTE: no dedicated `completed_at` column exists on Project, so this
  # treats "finished in the last 30 days" as status == completed AND the
  # row was last touched in the last 30 days.
  #
  # This can over-count if a completed project is edited later without
  # actually being re-completed.
  #
  # If this needs to be exact in the future, consider adding a
  # `completed_at` column that is only set when the project transitions
  # from active -> completed.
  finished_projects_count = (
    select(func.count(Project.id))
    .where(
      Project.company_id == Company.id,
      Project.status == ProjectStatus.completed,
      Project.updated_at >= thirty_days_ago,
    )
    .correlate(Company)
    .scalar_subquery()
  )

  # This counts ALL reports belonging to the company, regardless of whether
  # they are:
  #   - company-level reports
  #   - project-level reports via ReportProjectLink
  #   - future reports associated with other entities such as orders
  #
  # The important design point is that Report.company_id represents the
  # owning company independently from its other relationships.
  recent_reports_count = (
    select(func.count(Report.id))
    .where(
      Report.company_id == Company.id,
      Report.created_at >= thirty_days_ago,
    )
    .correlate(Company)
    .scalar_subquery()
  )

  active_project_guests_count = (
    select(func.count(func.distinct(ProjectGuestLink.user_id)))
    .select_from(ProjectGuestLink)
    .join(
      Project,
      Project.id == ProjectGuestLink.project_id,
    )
    .where(
      Project.company_id == Company.id,
      Project.status == ProjectStatus.active,
    )
    .correlate(Company)
    .scalar_subquery()
  )

  employees_count = (
    select(func.count(User.id))
    .where(
      User.company_id == Company.id,
    )
    .correlate(Company)
    .scalar_subquery()
  )

  stmt = (
    select(
      Company,
      active_projects_count.label("active_projects_count"),
      new_projects_count.label("new_projects_count"),
      finished_projects_count.label("finished_projects_count"),
      recent_reports_count.label("recent_reports_count"),
      active_project_guests_count.label("active_project_guests_count"),
      employees_count.label("employees_count"),
    )
    .outerjoin(
      Project,
      Project.company_id == Company.id,
    )
    .group_by(Company.id)
    .order_by(desc(func.max(Project.created_at)))
    .limit(25)
  )

  result = await db.execute(stmt)
  rows = result.all()

  companies = []

  for row in rows:
    company = row.Company

    companies.append(
      CompanyWithMetrics(
        id=company.id,
        name=company.name,
        corporate_number=company.corporate_number,
        active_projects_count=row.active_projects_count,
        new_projects_count=row.new_projects_count,
        finished_projects_count=row.finished_projects_count,
        recent_reports_count=row.recent_reports_count,
        active_project_guests_count=row.active_project_guests_count,
        employees_count=row.employees_count,
        billing_plan_id=company.billing_plan_id,
        created_at=company.created_at,
        updated_at=company.updated_at,
      )
    )

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
  response_model=CompanyCreateResponse,
  status_code=status.HTTP_201_CREATED,
)
async def create_company(
  payload: CompanyCreate,
  background_tasks: BackgroundTasks,
  db: AsyncSession = Depends(get_db),
):
  # existing_company = await db.scalar(
  #   select(Company).where(
  #     func.lower(Company.name) == payload.name.lower()
  #   )
  # )

  # if existing_company:
  #   raise HTTPException(
  #     status_code=409,
  #     detail="Company with that name already exists",
  #   )

  if payload.corporate_number:
    existing = await db.scalar(
      select(Company).where(
        Company.corporate_number == payload.corporate_number
      )
    )

    if existing:
      raise HTTPException(
        status_code=409,
        detail="Corporate number already exists",
      )

  if payload.manager_email:
    existing_user = await db.scalar(
      select(User).where(
        func.lower(User.email) == payload.manager_email.lower()
      )
    )

    if existing_user:
      raise HTTPException(
        status_code=409,
        detail="Manager email already belongs to an existing user",
      )

  company = Company(
    name=payload.name.strip(),
    corporate_number=(
      payload.corporate_number.strip()
      if payload.corporate_number
      else None
    )
  )
  db.add(company)

  await db.commit()
  await db.refresh(company)

  try:
    company.stripe_customer_id, company.stripe_test_clock_id = (
      await _create_stripe_customer_for_company(company)
    )
    await db.commit()
    await db.refresh(company)
  except stripe.error.StripeError:
    logger.exception(
      "Failed creating Stripe customer for company %s",
      company.id,
    )

  default_plan = await db.scalar(
    select(BillingPlan).where(
      BillingPlan.is_default == True,
    )
  )

  if default_plan:
    company.billing_plan_id = default_plan.id
    await db.commit()
    await db.refresh(company)

    await create_subscription(company, db)

  invitation = None
  
  if payload.manager_email:
    invitation_payload = CompanyInvitationCreate(
      email=payload.manager_email,
      company_id=company.id,
      role="manager",
    )

    invitation = await create_company_invitation(
      invitation_payload,
      db,
      None,
      background_tasks,
    )

  admin_users = (
    await db.scalars(
      select(User)
      .where(User.role == "admin")
    )
  ).all()

  for admin in admin_users:
    background_tasks.add_task(
      send_company_created_admin_email,
      admin.email,
      company.id,
      company.name,
      payload.manager_email,
    )

  return CompanyCreateResponse(
    company=company,
    invitation_id=invitation.id if invitation else None,
  )

@router.get(
  "/{company_id}",
  response_model=CompanyWithLists,
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
      selectinload(
        Company.custom_field_links
      ).selectinload(
        CustomFieldCompanyLink.custom_field
      ).selectinload(
        CustomField.definition
      ),
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

  custom_fields = [
    link.custom_field
    for link in company.custom_field_links
    if link.custom_field is not None
  ]

  payment_method_name = await get_payment_method_display(company)
  billing_status = get_billing_status(company)

  return CompanyWithLists(
    id=company.id,
    name=company.name,
    corporate_number=company.corporate_number,
    payment_method_name=payment_method_name,
    is_payment_method_valid=billing_status.is_payment_method_valid,
    free_trial_days_left=billing_status.free_trial_days_left,
    billing_plan_id=company.billing_plan_id,
    paid_features_force_disabled=company.paid_features_force_disabled,
    line_channel_secret_last4=company.line_channel_secret_last4,
    line_channel_access_token_last5=company.line_channel_access_token_last5,
    line_channel_access_token_invalid=company.line_channel_access_token_invalid,
    created_at=company.created_at,
    updated_at=company.updated_at,
    users=company.users,
    projects=projects,
    custom_fields=custom_fields,
  )

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
    require_company_manager(current_user, company_id)

  if (
    payload.paid_features_force_disabled is not None
    and current_user.role != "admin"
  ):
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Only admins can change the paid features force-disable setting",
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

  if payload.line_channel_secret is not None:
    company.line_channel_secret = payload.line_channel_secret

  if payload.line_channel_access_token is not None:
    company.line_channel_access_token = payload.line_channel_access_token
    company.line_channel_access_token_invalid = False

  if payload.paid_features_force_disabled is not None:
    company.paid_features_force_disabled = payload.paid_features_force_disabled

  if "billing_plan_id" in payload.model_fields_set:
    if not company.stripe_customer_id:
      needs_customer = True
    else:
      try:
        stripe.Customer.retrieve(company.stripe_customer_id)
        needs_customer = False
      except stripe.error.InvalidRequestError as e:
        if e.code == "resource_missing":
          logger.warning(
            "Stripe customer %s not found for company %s. Creating a new customer.",
            company.stripe_customer_id,
            company.id,
          )
          needs_customer = True
        else:
          raise
    
    if needs_customer:
      try:
        company.stripe_customer_id, company.stripe_test_clock_id = (
          await _create_stripe_customer_for_company(company)
        )
        await db.commit()
        await db.refresh(company)
      except stripe.error.StripeError:
        logger.exception(
          "Failed creating Stripe customer for company %s",
          company.id,
        )
        raise HTTPException(
          status_code=502,
          detail="Unable to create Stripe customer.",
        )

    if payload.billing_plan_id is None:
      if company.stripe_subscription_id:
        try:
          stripe.Subscription.modify(
            company.stripe_subscription_id,
            cancel_at_period_end=True,
          )
        except stripe.error.StripeError:
          logger.exception(
            "Failed to cancel Stripe subscription for company %s",
            company.id,
          )
          raise HTTPException(
            status_code=500,
            detail="Failed to cancel Stripe subscription",
          )

      company.billing_plan_id = None
    else:
      plan = await db.get(BillingPlan, payload.billing_plan_id)

      if not plan:
        raise HTTPException(status_code=404, detail="Billing plan not found")
      
      company.billing_plan_id = plan.id

      if not company.stripe_subscription_id:
        await create_subscription(company, db)
      else:
        try:
          try:
            subscription = stripe.Subscription.retrieve(
              company.stripe_subscription_id
            )
          except stripe.error.InvalidRequestError as e:
            if e.code == "resource_missing":
              logger.info(
                "Stripe subscription %s no longer exists for company %s. Creating a new subscription.",
                company.stripe_subscription_id,
                company.id,
              )

              company.stripe_subscription_id = None
              await db.commit()

              await create_subscription(company, db)
              subscription = None
            else:
              raise

          if subscription:
            if subscription.status == "canceled":
              logger.info(
                "Stripe subscription %s is canceled for company %s. Creating a new subscription.",
                subscription.id,
                company.id,
              )

              company.stripe_subscription_id = None
              await db.commit()
              await create_subscription(company, db)

            else:
              if subscription.cancel_at_period_end:
                subscription = stripe.Subscription.modify(
                  subscription.id,
                  cancel_at_period_end=False,
                )

              item_id = subscription["items"]["data"][0]["id"]

              stripe.SubscriptionItem.modify(
                item_id,
                price=plan.stripe_price_id,
                quantity=1,
                proration_behavior="create_prorations",
              )

        except stripe.error.StripeError:
          logger.exception(
            "Failed to update Stripe subscription plan for company %s",
            company.id,
          )
          raise HTTPException(
            status_code=500,
            detail="Failed to update Stripe subscription.",
          )

  await db.commit()
  await db.refresh(company)

  return company

@router.get(
  "/{company_id}/users",
  response_model=list[CompanyUserRead],
)
async def get_company_users(
  company_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role != "admin":
    has_company_access = current_user.company_id == company_id

    if not has_company_access:
      guest_access_result = await db.execute(
        select(ProjectGuestLink.id)
        .join(Project, Project.id == ProjectGuestLink.project_id)
        .where(
          ProjectGuestLink.user_id == current_user.id,
          Project.company_id == company_id,
        )
        .limit(1)
      )

      has_company_access = guest_access_result.scalar_one_or_none() is not None

    if not has_company_access:
      raise HTTPException(
        status_code=403,
        detail="Not authorized",
      )

  result = await db.execute(
    select(User)
    .where(User.company_id == company_id)
    .order_by(User.first_name.asc(), User.last_name.asc())
  )

  return result.scalars().all()

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
    select(ImageTag)
    .where(ImageTag.company_id == company_id)
    .order_by(ImageTag.name.asc())
  )

  result = await db.execute(stmt)
  tags = result.scalars().all()

  return tags

@router.post("/{company_id}/tags")
async def create_tag(
  company_id: UUID,
  payload: ImageTagCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role != "admin":
    require_company_manager(current_user, company_id)

  tag = ImageTag(
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
  payload: ImageTagUpdate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role != "admin":
    require_company_manager(current_user, company_id)

  stmt = (
    select(ImageTag)
    .where(
      ImageTag.id == tag_id,
      ImageTag.company_id == company_id
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
    select(ImageTag)
    .where(
      ImageTag.id == tag_id,
      ImageTag.company_id == company_id
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
    try:
      company.stripe_customer_id, company.stripe_test_clock_id = (
        await _create_stripe_customer_for_company(company)
      )
      await db.commit()
      await db.refresh(company)
    except stripe.error.StripeError:
      logger.exception(
        "Failed creating Stripe customer for company %s",
        company.id,
      )
      raise HTTPException(
        status_code=502,
        detail="Unable to set up billing right now. Please try again shortly.",
      )

  intent = stripe.SetupIntent.create(
    customer=company.stripe_customer_id,
    payment_method_types=["card"],
  )

  return {"client_secret": intent.client_secret}

@router.get("/{company_id}/billing/check")
async def check_billing_status(
  company_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  allowed, reason = await can_use_billed_features(company_id, db)
  return {"success": allowed, "reason": reason}

async def _create_stripe_customer_for_company(company: Company) -> tuple[str, str | None]:
  if settings.STRIPE_SECRET_KEY.startswith("sk_test_"):
    clock = stripe.test_helpers.TestClock.create(
      frozen_time=int(time.time()),
      name=company.name,
    )
    customer = stripe.Customer.create(
      name=company.name, test_clock=clock.id,
      metadata={"company_id": str(company.id)},
    )
    return customer.id, clock.id
    
  customer = stripe.Customer.create(
    name=company.name, metadata={"company_id": str(company.id)},
  )
  return customer.id, None

@router.get("/{company_id}/conversation-item-types")
async def list_conversation_item_types(
  company_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role != "admin" and current_user.company_id != company_id:
    raise HTTPException(status_code=403, detail="Not authorized")

  result = await db.execute(
    select(ConversationItemType)
    .where(
      ConversationItemType.company_id == company_id,
      ConversationItemType.is_active == True
    )
    .order_by(ConversationItemType.name.asc())
  )
  return result.scalars().all()

@router.post(
  "/{company_id}/conversation-item-types",
  response_model=list[ConversationItemTypeRead],
  status_code=status.HTTP_201_CREATED,
)
async def create_conversation_item_type(
  company_id: UUID,
  payload: ConversationItemTypeCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role != "admin":
    require_company_manager(current_user, company_id)

  existing = await db.scalar(
    select(ConversationItemType).where(
      ConversationItemType.company_id == company_id,
      ConversationItemType.name == payload.name,
      ConversationItemType.is_active == True,
    )
  )

  if existing:
    raise HTTPException(
      status_code=409,
      detail="An active conversation item type with this name already exists.",
    )

  item_type = ConversationItemType(
    company_id=company_id,
    name=payload.name,
    description=payload.description,
  )

  db.add(item_type)
  await db.commit()

  result = await db.execute(
    select(ConversationItemType)
    .where(
      ConversationItemType.company_id == company_id,
      ConversationItemType.is_active == True
    )
    .order_by(ConversationItemType.name)
  )

  return result.scalars().all()

@router.patch(
  "/{company_id}/conversation-item-types/{item_type_id}",
  response_model=list[ConversationItemTypeRead],
)
async def update_conversation_item_type(
  company_id: UUID,
  item_type_id: UUID,
  payload: ConversationItemTypeUpdate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role != "admin":
    require_company_manager(current_user, company_id)

  result = await db.execute(
    select(ConversationItemType).where(
      ConversationItemType.id == item_type_id,
      ConversationItemType.company_id == company_id,
    )
  )
  item_type = result.scalar_one_or_none()

  if not item_type:
    raise HTTPException(status_code=404, detail="Conversation item type not found")

  for field, value in payload.model_dump(exclude_unset=True).items():
    setattr(item_type, field, value)

  await db.commit()

  result = await db.execute(
    select(ConversationItemType)
    .where(
      ConversationItemType.company_id == company_id,
      ConversationItemType.is_active == True
    )
    .order_by(ConversationItemType.name)
  )

  return result.scalars().all()

@router.delete(
  "/{company_id}/conversation-item-types/{item_type_id}",
  status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_conversation_item_type(
  company_id: UUID,
  item_type_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role != "admin":
    require_company_manager(current_user, company_id)

  result = await db.execute(
    select(ConversationItemType).where(
      ConversationItemType.id == item_type_id,
      ConversationItemType.company_id == company_id,
    )
  )
  item_type = result.scalar_one_or_none()

  if not item_type:
    raise HTTPException(
      status_code=404,
      detail="Conversation item type not found",
    )

  item_type.is_active = False

  links_result = await db.execute(
    select(ConversationItemTypeLink)
    .where(
      ConversationItemTypeLink.item_type_id == item_type.id,
    )
  )
  links = links_result.scalars().all()

  for link in links:
    items_result = await db.execute(
      select(ConversationItem.id)
      .where(
        ConversationItem.conversation_id == link.conversation_id,
        ConversationItem.conversation_item_type_id == item_type.id,
      )
      .limit(1)
    )

    if items_result.scalar_one_or_none() is None:
      await db.delete(link)

  await db.commit()

  return None