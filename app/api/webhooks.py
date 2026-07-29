import base64
import hashlib
import hmac
import logging
import json
from datetime import datetime, timezone

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request, BackgroundTasks

import sqlalchemy as sa
from sqlalchemy import select, delete, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.db.models.company import Company
from app.db.models.user import User
from app.db.models.project import Project
from app.db.models.line_conversation import LineConversation
from app.db.models.conversation_item_type import ConversationItemTypeLink
from app.services.billing import get_company_by_stripe_customer_id, can_use_billed_features
from app.services.email import send_line_group_linked_email
from app.services.images import create_image_from_line_message
from app.services.projects import handle_line_conversation_items
from app.services.reports import handle_line_group_message

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

  logger.info("=== STRIPE WEBHOOK ===")
  logger.info("Headers: %s", dict(request.headers))
  logger.info("Body: %s", payload.decode("utf-8"))

  try:
    event = stripe.Webhook.construct_event(
      payload, stripe_signature, settings.STRIPE_WEBHOOK_SECRET
    )
  except (ValueError, stripe.error.SignatureVerificationError):
    raise HTTPException(status_code=400, detail="Invalid signature")

  event_type = event["type"]
  data = event["data"]["object"]

  logger.info("Stripe event type: %s", event_type)

  if event_type in (
    "customer.subscription.updated",
    "customer.subscription.deleted",
  ):
    customer_id = getattr(data, "customer", None)

    if customer_id:
      company = await get_company_by_stripe_customer_id(db, customer_id)

      if company:
        company.stripe_subscription_id = data["id"]
        await db.commit()

  elif event_type == "setup_intent.succeeded":
    customer_id = getattr(data, "customer", None)
    payment_method_id = getattr(data, "payment_method", None)

    if customer_id and payment_method_id:
      company = await get_company_by_stripe_customer_id(db, customer_id)
      
      if company and company.stripe_subscription_id:
        try:
          stripe.Subscription.modify(
            company.stripe_subscription_id,
            default_payment_method=payment_method_id,
          )
        except stripe.error.StripeError:
          logger.exception(
            "Failed to set default payment method for company %s", company.id
          )

  return {"status": "ok"}

@router.post("/line/{company_id}")
async def line_webhook(
  company_id: str,
  request: Request,
  background_tasks: BackgroundTasks,
  x_line_signature: str = Header(..., alias="X-Line-Signature"),
  db: AsyncSession = Depends(get_db),
):
  body = await request.body()

  logger.info("=== LINE WEBHOOK ===")
  logger.info("Headers: %s", dict(request.headers))
  logger.info("Body: %s", body.decode("utf-8"))

  try:
    logger.info(
      "JSON: %s",
      json.dumps(json.loads(body), indent=2, ensure_ascii=False),
    )
  except Exception:
    logger.exception("Request body was not valid JSON")

  result = await db.execute(
    select(Company).where(Company.id == company_id)
  )
  company = result.scalar_one_or_none()

  if not company or not company.line_channel_secret:
    raise HTTPException(status_code=404)

  if not verify_line_signature(body, x_line_signature, company.line_channel_secret):
    raise HTTPException(status_code=403, detail="Invalid signature")

  can_use_features, reason = await can_use_billed_features(company.id, db)

  if not can_use_features:
    logger.info(
      "Ignoring LINE webhook because billed features are disabled | company_id=%s reason=%s",
      company.id,
      reason,
    )
    return {"status": "billing_disabled"}

  payload = await request.json()

  for event in payload.get("events", []):

    if event.get("type") != "message":
      continue

    message = event.get("message", {})
    message_type = message.get("type")

    if message_type not in [
      "text",
      "image",
      # "audio",
      # "video",
      # "file",
    ]:
      continue

    source = event.get("source", {})
    source_type = source.get("type")
    sender_id = source.get("userId")

    if source_type == "group":
      line_chat_id = source.get("groupId")
    elif source_type == "room":
      line_chat_id = source.get("roomId")
    elif source_type == "user":
      line_chat_id = source.get("userId")
    else:
      continue

    if not line_chat_id:
      continue

    text = (
      message.get("text", "").strip()
      if message_type == "text"
      else None
    )

    candidate_code = text.upper() if text else ""

    line_timestamp_ms = event.get("timestamp")

    line_timestamp = (
      datetime.fromtimestamp(
        line_timestamp_ms / 1000,
        tz=timezone.utc,
      )
      if line_timestamp_ms
      else None
    )

    if message_type == "text" and candidate_code.startswith("K-"):

      conversation_result = await db.execute(
        select(LineConversation).where(
          LineConversation.line_link_code == candidate_code,
          LineConversation.company_id == company.id,
        )
      )

      conversation = conversation_result.scalar_one_or_none()

      if conversation:

        # Remove this LINE chat from any previous conversation
        await db.execute(
          sa.update(LineConversation)
          .where(
            LineConversation.line_chat_type == source_type,
            LineConversation.line_chat_id == line_chat_id,
          )
          .values(
            line_chat_type=None,
            line_chat_id=None,
          )
        )

        conversation.line_chat_id = line_chat_id
        conversation.line_chat_type = source_type

        await db.commit()

        logger.info(
          "LINE chat linked to conversation | company_id=%s conversation_id=%s line_chat_type=%s line_chat_id=%s",
          company.id,
          conversation.id,
          source_type,
          line_chat_id,
        )

      else:
        logger.warning(
          "LINE chat sent unrecognized conversation code | company_id=%s line_chat_id=%s code=%s",
          company.id,
          line_chat_id,
          candidate_code,
        )

      continue

    conversation_result = await db.execute(
      select(LineConversation).where(
        LineConversation.line_chat_type == source_type,
        LineConversation.line_chat_id == line_chat_id,
        LineConversation.company_id == company.id,
      )
    )

    conversation = conversation_result.scalar_one_or_none()

    if not conversation:
      logger.warning(
        "LINE message from unlinked chat | company_id=%s line_chat_id=%s",
        company.id,
        line_chat_id,
      )
      continue

    line_message = LineMessage(
      company_id=company.id,
      conversation_id=conversation.id,
      sender_line_user_id=sender_id,
      line_user_id=sender_id,
      line_chat_id=line_chat_id,
      line_chat_type=source_type,
      line_timestamp=line_timestamp,
      line_platform_message_id=message["id"],
      message_type=message_type,
      text=text,
    )

    db.add(line_message)
    await db.flush()

    if message_type == "text":
      background_tasks.add_task(
        handle_line_conversation_items,
        line_message_id=line_message.id,
      )
      continue
    
    if not company.line_channel_access_token:
      logger.warning(
        "Ignoring LINE image because no channel access token is configured | company_id=%s",
        company.id,
      )
      continue

    if message_type == "image":
      background_tasks.add_task(
        create_image_from_line_message,
        company_id=company.id,
        line_message_id=line_message.id,
        line_platform_message_id=message["id"],
      )
    # elif message_type == "audio":
    #   background_tasks.add_task(
    #     process_line_audio,
    #     line_message_id=line_message.id,
    #   )
    # elif message_type == "video":
    #   background_tasks.add_task(
    #     process_line_video,
    #     company_id=company.id,
    #     line_message_id=line_message.id,
    #     line_message_api_id=message["id"],
    #   )
    # elif message_type == "file":
    #   background_tasks.add_task(
    #     process_line_file,
    #     company_id=company.id,
    #     line_message_id=line_message.id,
    #     line_message_api_id=message["id"],
    #   )

  await db.commit()  

  return {"status": "ok"}

def verify_line_signature(body: bytes, signature: str, channel_secret: str) -> bool:
  expected = base64.b64encode(
    hmac.new(channel_secret.encode(), body, hashlib.sha256).digest()
  ).decode()
  return hmac.compare_digest(expected, signature)