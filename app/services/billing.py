import logging
import math
from datetime import datetime, timezone

import stripe
from fastapi import HTTPException
from dataclasses import dataclass
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.company import Company

logger = logging.getLogger(__name__)

HEALTHY_SUBSCRIPTION_STATUSES = {"active", "trialing"}

@dataclass
class BillingStatus:
  is_payment_method_valid: bool
  free_trial_days_left: int | None

def get_billing_status(company: Company) -> BillingStatus:
  if not company.billing_plan_id or not company.stripe_subscription_id:
    return BillingStatus(is_payment_method_valid=True, free_trial_days_left=None)

  try:
    subscription = stripe.Subscription.retrieve(company.stripe_subscription_id)
  except stripe.error.StripeError:
    logger.exception(
      "Failed to retrieve subscription for company %s", company.id
    )
    return BillingStatus(is_payment_method_valid=True, free_trial_days_left=None)

  is_valid = subscription.status in HEALTHY_SUBSCRIPTION_STATUSES

  free_trial_days_left = None
  if subscription.status == "trialing" and subscription.trial_end:
    trial_end = datetime.fromtimestamp(subscription.trial_end, tz=timezone.utc)
    seconds_remaining = (trial_end - datetime.now(timezone.utc)).total_seconds()
    free_trial_days_left = max(math.ceil(seconds_remaining / 86400), 0)

  return BillingStatus(
    is_payment_method_valid=is_valid,
    free_trial_days_left=free_trial_days_left,
  )

async def can_use_billed_features(company_id: str, db: AsyncSession) -> tuple[bool, str | None]:
  company = await db.get(Company, company_id)
  if not company:
    raise HTTPException(status_code=404, detail="Company not found")

  if not get_billing_status(company).is_payment_method_valid:
    return False, "subscription_past_due"

  return True, None

async def get_payment_method_display(company: Company) -> str | None:
  if not company.stripe_customer_id:
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

  return f"{brand} ••••{pm.card.last4}"

async def get_company_by_stripe_customer_id(
  db: AsyncSession, customer_id: str
) -> Company | None:
  result = await db.execute(
    select(Company).where(Company.stripe_customer_id == customer_id)
  )
  return result.scalars().first()