import logging

import stripe
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.company import Company
from app.db.models.project import Project, ProjectStatus

logger = logging.getLogger(__name__)

HEALTHY_SUBSCRIPTION_STATUSES = {"active", "trialing"}

async def has_payment_method(company: Company) -> bool:
  if not company.billing_plan_id:
    return True

  if not company.stripe_customer_id:
    return False

  try:
    payment_methods = stripe.PaymentMethod.list(
      customer=company.stripe_customer_id,
      type="card",
    )
  except stripe.error.StripeError:
    logger.exception(
      "Failed to check payment methods for company %s", company.id
    )
    return True

  return len(payment_methods.data) > 0

def billing_in_good_standing(company: Company) -> bool:
  if not company.billing_plan_id:
    return True

  if not company.stripe_subscription_id:
    return True

  try:
    subscription = stripe.Subscription.retrieve(company.stripe_subscription_id)
  except stripe.error.StripeError:
    logger.exception(
      "Failed to retrieve subscription for company %s", company.id
    )
    return True

  return subscription.status in HEALTHY_SUBSCRIPTION_STATUSES

async def can_use_billed_features(company_id: str, db: AsyncSession) -> tuple[bool, str | None]:
  company = await db.get(Company, company_id)
  if not company:
    raise HTTPException(status_code=404, detail="Company not found")

  if not await has_payment_method(company):
    return False, "payment_method_required"

  if not billing_in_good_standing(company):
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

async def ensure_subscription(company: Company, db: AsyncSession):
  if not company.billing_plan_id:
    return

  if company.stripe_subscription_id:
    return

  try:
    subscription = stripe.Subscription.create(
      customer=company.stripe_customer_id,
      items=[{"price": "price_xxx", "quantity": 0}],
      trial_period_days=14,
      trial_settings={"end_behavior": {"missing_payment_method": "cancel"}},
      payment_behavior="default_incomplete",
    )
    company.stripe_subscription_id = subscription.id
    company.stripe_subscription_status = subscription.status
    await db.commit()
  except stripe.error.StripeError:
    logger.exception(
      "Failed to create Stripe subscription for company %s", company.id
    )

async def get_company_by_stripe_customer_id(
  db: AsyncSession, customer_id: str
) -> Company | None:
  result = await db.execute(
    select(Company).where(Company.stripe_customer_id == customer_id)
  )
  return result.scalars().first()