import logging

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.db.models.company import Company

logger = logging.getLogger(__name__)

router = APIRouter(
  prefix="/webhooks",
  tags=["webhooks"]
)

async def get_company_by_stripe_customer_id(
  db: AsyncSession, customer_id: str
) -> Company | None:
  result = await db.execute(
    select(Company).where(Company.stripe_customer_id == customer_id)
  )
  return result.scalars().first()

@router.post("/webhooks/stripe")
async def stripe_webhook(
  request: Request,
  stripe_signature: str = Header(None),
  db: AsyncSession = Depends(get_db),
):
  payload = await request.body()

  try:
    event = stripe.Webhook.construct_event(
      payload, stripe_signature, settings.STRIPE_WEBHOOK_SECRET
    )
  except (ValueError, stripe.error.SignatureVerificationError):
    raise HTTPException(status_code=400, detail="Invalid signature")

  event_type = event["type"]
  data = event["data"]["object"]

  if event_type in (
    "customer.subscription.updated",
    "customer.subscription.deleted",
  ):
    customer_id = data.get("customer")

    if customer_id:
        company = await get_company_by_stripe_customer_id(db, customer_id)

        if company:
          company.stripe_subscription_id = data["id"]
          company.stripe_subscription_status = data["status"]
          await db.commit()

  elif event_type == "invoice.payment_failed":
    customer_id = data.get("customer")

    if customer_id:
      company = await get_company_by_stripe_customer_id(db, customer_id)

      if company:
        company.stripe_subscription_status = "past_due"
        await db.commit()

  elif event_type == "invoice.payment_succeeded":
    customer_id = data.get("customer")
    if customer_id:
      company = await get_company_by_stripe_customer_id(db, customer_id)
      if company and company.stripe_subscription_status == "past_due":
        company.stripe_subscription_status = "active"
        await db.commit()

  else:
    logger.debug("Unhandled Stripe event type: %s", event_type)

  return {"status": "ok"}