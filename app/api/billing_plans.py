import logging
from typing import List
from uuid import UUID

import stripe
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.db.models import User
from app.db.models.billing_plan import BillingPlan
from app.db.session import get_db
from app.schemas.billing_plan import BillingPlanCreate, BillingPlanRead, BillingPlanUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing-plans", tags=["billing plans"])

async def clear_default(db: AsyncSession, exclude_id: UUID | None = None):
  stmt = select(BillingPlan).where(BillingPlan.is_default == True)
  if exclude_id:
    stmt = stmt.where(BillingPlan.id != exclude_id)
  result = await db.execute(stmt)
  plans = result.scalars().all()
  for plan in plans:
    plan.is_default = False

@router.get("", response_model=List[BillingPlanRead])
async def list_billing_plans(
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = select(BillingPlan).order_by(BillingPlan.sort_order.asc())

  if current_user.role != "admin":
    stmt = stmt.where(BillingPlan.is_hidden == False)

  result = await db.execute(stmt)
  return result.scalars().all()

@router.post("", response_model=BillingPlanRead, status_code=status.HTTP_201_CREATED)
async def create_billing_plan(
  payload: BillingPlanCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role != "admin":
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Only admins can create billing plans",
    )

  if payload.is_default:
    await clear_default(db)

  plan = BillingPlan(**payload.model_dump())
  db.add(plan)
  await db.commit()
  await db.refresh(plan)

  return plan

@router.patch("/{plan_id}", response_model=BillingPlanRead)
async def update_billing_plan(
  plan_id: UUID,
  payload: BillingPlanUpdate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role != "admin":
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Only admins can update billing plans",
    )

  plan = await db.get(BillingPlan, plan_id)
  if not plan:
    raise HTTPException(status_code=404, detail="Billing plan not found")

  if payload.is_default is True:
    await clear_default(db, exclude_id=plan_id)

  for field, value in payload.model_dump(exclude_unset=True).items():
    setattr(plan, field, value)

  await db.commit()
  await db.refresh(plan)

  return plan

@router.delete("/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_billing_plan(
  plan_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role != "admin":
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Only admins can delete billing plans",
    )

  plan = await db.get(BillingPlan, plan_id)
  if not plan:
    raise HTTPException(status_code=404, detail="Billing plan not found")

  await db.delete(plan)
  await db.commit()

  return None