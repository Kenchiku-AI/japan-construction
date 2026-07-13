import logging
import math
from datetime import datetime, timezone

import stripe
from fastapi import HTTPException
from dataclasses import dataclass
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.company import Company
from app.db.models.billing_plan import BillingPlan

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

  logger.info(
    "Stripe subscription status for company %s = %s",
    company.id,
    subscription.status,
  )

  if subscription.status == "paused" and _has_valid_payment_method(company):
    try:
      logger.info(
        "Attempting to resume subscription %s",
        subscription.id,
      )

      subscription = stripe.Subscription.resume(subscription.id)

      logger.info(
        "Resume returned: status=%s latest_invoice=%s pending_update=%s default_payment_method=%s",
        subscription.status,
        subscription.latest_invoice,
        subscription.pending_update,
        subscription.default_payment_method,
      )

      if subscription.latest_invoice:
        invoice = stripe.Invoice.retrieve(subscription.latest_invoice)

        logger.info(
          "Invoice after resume, before pay:\n%s",
          invoice,
        )

        logger.info(invoice.to_dict())

        paid_invoice = stripe.Invoice.pay(invoice.id)

        logger.info(
          "Invoice after pay:\n%s",
          paid_invoice,
        )

        logger.info(paid_invoice.to_dict())

        subscription = stripe.Subscription.retrieve(subscription.id)

        logger.info(
          "Subscription after pay: status=%s pending_update=%s",
          subscription.status,
          subscription.pending_update,
        )

      logger.info(
        "Auto-resume finished for company %s",
        company.id,
      )

    except stripe.error.StripeError as e:
      logger.exception(
        "Resume failed for company %s: %s",
        company.id,
        str(e),
      )

  is_valid = subscription.status in HEALTHY_SUBSCRIPTION_STATUSES

  free_trial_days_left = None
  if subscription.status == "trialing" and subscription.trial_end:
    trial_end = datetime.fromtimestamp(
      subscription.trial_end,
      tz=timezone.utc,
    )

    today = datetime.now(timezone.utc).date()
    end_date = trial_end.date()

    free_trial_days_left = max((end_date - today).days, 0)

  return BillingStatus(
    is_payment_method_valid=is_valid,
    free_trial_days_left=free_trial_days_left,
  )

def _has_valid_payment_method(company: Company) -> bool:
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
    return False

  return len(payment_methods.data) > 0

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

async def create_subscription(company: Company, db: AsyncSession) -> None:
  if not company.billing_plan_id or not company.stripe_customer_id:
    return

  if company.stripe_subscription_id:
    return

  plan = await db.get(BillingPlan, company.billing_plan_id)
  if not plan:
    logger.error(
      "BillingPlan %s not found for company %s", company.billing_plan_id, company.id
    )
    return

  try:
    subscription = stripe.Subscription.create(
      customer=company.stripe_customer_id,
      items=[{"price": plan.stripe_price_id, "quantity": 1}],
      trial_end=int(time.time()) + (settings.STRIPE_TRIAL_PERIOD_MINUTES * 60),
      trial_settings={"end_behavior": {"missing_payment_method": "pause"}},
      payment_settings={"save_default_payment_method": "on_subscription"},
    )
    company.stripe_subscription_id = subscription.id
    company.stripe_subscription_status = subscription.status
    await db.commit()
  except stripe.error.StripeError:
    logger.exception(
      "Failed to create Stripe subscription for company %s", company.id
    )