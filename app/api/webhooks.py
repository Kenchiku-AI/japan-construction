import base64
import hashlib
import hmac
import logging

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.db.models.company import Company
from app.services.billing import get_company_by_stripe_customer_id

logger = logging.getLogger(__name__)

router = APIRouter(
  prefix="/webhooks",
  tags=["webhooks"]
)

@router.post("/stripe")
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

@router.post("/line/{company_id}")
async def line_webhook(
  company_id: str,
  request: Request,
  x_line_signature: str = Header(..., alias="X-Line-Signature"),
  db: AsyncSession = Depends(get_db),
):
  result = await db.execute(select(Company).where(Company.id == company_id))
  company = result.scalar_one_or_none()

  if not company or not company.line_channel_secret:
    raise HTTPException(status_code=404)

  body = await request.body()

  if not verify_line_signature(body, x_line_signature, company.line_channel_secret):
    raise HTTPException(status_code=403, detail="Invalid signature")

  payload = await request.json()

  for event in payload.get("events", []):
    if event.get("type") != "message":
      continue

    message = event.get("message", {})
    if message.get("type") != "text":
      continue

    sender_id = event.get("source", {}).get("userId")
    source_type = event.get("source", {}).get("type")
    text = message.get("text")

    logger.info(
      "LINE message received",
      extra={"company_id": str(company.id), "source_type": source_type, "sender_id": sender_id},
    )

    # TODO: persist/process the message

  return {"status": "ok"}

def verify_line_signature(body: bytes, signature: str, channel_secret: str) -> bool:
  expected = base64.b64encode(
    hmac.new(channel_secret.encode(), body, hashlib.sha256).digest()
  ).decode()

  return hmac.compare_digest(expected, signature)