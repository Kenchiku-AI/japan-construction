import base64
import hashlib
import hmac
import logging

import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request, BackgroundTasks

import sqlalchemy as sa
from sqlalchemy import select, delete, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.db.models.company import Company
from app.db.models.user import User, UserLineLink
from app.db.models.project import Project
from app.services.billing import get_company_by_stripe_customer_id
from app.services.email import send_line_link_confirmation_email, send_line_group_linked_email
from app.services.reports import handle_line_message, handle_line_group_message
from app.services.projects import handle_line_group_work_item
from app.services.users import link_line_user

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
  background_tasks: BackgroundTasks,
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
    group_id = event.get("source", {}).get("groupId")
    text = message.get("text", "").strip()
    candidate_code = text.upper()

    # --- Group message ---
    if source_type == "group" and group_id:

      # P- code: link or re-link project to this group
      if candidate_code.startswith("P-"):
        project_result = await db.execute(
          select(Project).where(Project.line_link_code == candidate_code)
        )
        project = project_result.scalar_one_or_none()

        if project:
          await db.execute(
            sa.update(Project)
            .where(Project.line_group_id == group_id)
            .values(line_group_id=None)
          )
          project.line_group_id = group_id
          await db.commit()

          logger.info(
            "LINE group linked to project | company_id=%s project_id=%s group_id=%s",
            company.id, project.id, group_id,
          )

          user_link_result = await db.execute(
            select(UserLineLink).where(
              UserLineLink.company_id == company.id,
              UserLineLink.line_user_id == sender_id,
            )
          )
          user_link = user_link_result.scalar_one_or_none()

          if user_link:
            user_result = await db.execute(
              select(User).where(User.id == user_link.user_id)
            )
            user = user_result.scalar_one_or_none()
            if user:
              background_tasks.add_task(
                send_line_group_linked_email,
                email=user.email,
                company_name=company.name,
                project_name=project.name,
              )
        else:
          logger.warning(
            "LINE group sent unrecognized project code | company_id=%s group_id=%s code=%s",
            company.id, group_id, candidate_code,
          )
        continue

      # U- code: link or re-link user account (works from group or DM)
      if await link_line_user(sender_id, candidate_code, company, background_tasks, db, group_id):
        continue

      # Regular group message: look up project by group_id
      project_result = await db.execute(
        select(Project).where(
          Project.line_group_id == group_id,
          Project.company_id == company.id,
        )
      )
      project = project_result.scalar_one_or_none()

      if not project:
        logger.warning(
          "LINE message from unlinked group | company_id=%s group_id=%s",
          company.id, group_id,
        )
        continue

      user_link_result = await db.execute(
        select(UserLineLink).where(
          UserLineLink.company_id == company.id,
          UserLineLink.line_user_id == sender_id,
        )
      )
      user_link = user_link_result.scalar_one_or_none()

      if not user_link:
        logger.warning(
          "LINE group message from unlinked user | company_id=%s group_id=%s sender_id=%s",
          company.id, group_id, sender_id,
        )
        continue

      user_result = await db.execute(
        select(User).where(User.id == user_link.user_id)
      )
      user = user_result.scalar_one_or_none()

      if user:
        background_tasks.add_task(
          handle_line_group_message,
          text=text,
          user=user,
          project_id=project.id,
        )

        background_tasks.add_task(
          handle_line_group_work_item,
          text=text,
          user=user,
          project=project,
          sender_line_user_id=sender_id,
          group_id=group_id,
          company_id=company.id,
        )

    # --- DM message ---
    else:
      # U- code always takes priority — handles both initial linking and re-linking
      if await link_line_user(sender_id, candidate_code, company, background_tasks, db):
        continue

      link_result = await db.execute(
        select(UserLineLink).where(
          UserLineLink.company_id == company.id,
          UserLineLink.line_user_id == sender_id,
        )
      )
      link = link_result.scalar_one_or_none()

      if link:
        logger.info(
          "LINE DM received | company_id=%s user_id=%s sender_id=%s text=%s",
          company.id, link.user_id, sender_id, text,
        )

        user_result = await db.execute(
          select(User).where(User.id == link.user_id)
        )
        user = user_result.scalar_one_or_none()

        if user:
          background_tasks.add_task(
            handle_line_message,
            text=text,
            user=user,
            company_id=company.id,
          )

      else:
        logger.warning(
          "LINE DM from unrecognized sender | company_id=%s sender_id=%s text=%s",
          company.id, sender_id, text,
        )

  return {"status": "ok"}

def verify_line_signature(body: bytes, signature: str, channel_secret: str) -> bool:
  expected = base64.b64encode(
    hmac.new(channel_secret.encode(), body, hashlib.sha256).digest()
  ).decode()
  return hmac.compare_digest(expected, signature)