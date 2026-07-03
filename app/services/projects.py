from uuid import UUID
import logging

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import User
from app.db.models.project import Project
from app.db.models.project_guest_link import ProjectGuestLink
from app.db.models.line_message import LineMessage
from app.db.models.work_item import WorkItem, WorkItemStatus
from app.db.session import AsyncSessionLocal
from app.services.openai import extract_work_item

logger = logging.getLogger(__name__)

async def get_project_members(
  project_id: UUID,
  company_id: UUID,
  db: AsyncSession,
) -> list[User]:
  company_members_result = await db.execute(
    select(User).where(User.company_id == company_id)
  )
  company_members = company_members_result.scalars().all()

  guest_members_result = await db.execute(
    select(User)
    .join(ProjectGuestLink, ProjectGuestLink.user_id == User.id)
    .where(ProjectGuestLink.project_id == project_id)
  )
  guest_members = guest_members_result.scalars().all()

  seen = set()
  members = []
  for user in list(company_members) + list(guest_members):
    if user.id not in seen:
      seen.add(user.id)
      members.append(user)

  return members

async def handle_line_group_work_item(
  text: str,
  user: User,
  project: Project,
  sender_line_user_id: str,
  group_id: str,
  company_id: UUID,
) -> None:
  async with AsyncSessionLocal() as db:
    # Save message to history
    current_message = LineMessage(
      company_id=company_id,
      line_group_id=group_id,
      sender_line_user_id=sender_line_user_id,
      text=text,
      triggered_work_item=False,
    )
    db.add(current_message)
    await db.flush()

    # Fetch recent message history for this group (excluding current message)
    history_result = await db.execute(
      select(LineMessage)
      .where(
        LineMessage.company_id == company_id,
        LineMessage.line_group_id == group_id,
        LineMessage.id != current_message.id,
      )
      .order_by(LineMessage.created_at.desc())
      .limit(8)
    )
    recent_messages = list(reversed(history_result.scalars().all()))

    # Attempt work item extraction
    work_item_data = await extract_work_item(
      message_text=text,
      project=project,
      sender=user,
      recent_messages=recent_messages,
    )

    if work_item_data:
      db.add(WorkItem(
        project_id=project.id,
        name=work_item_data["name"],
        description=work_item_data.get("description"),
        status=WorkItemStatus.new,
      ))
      # Mark this message so future history knows not to re-trigger on it
      current_message.triggered_work_item = True

      logger.info(
        "LINE work item created | project_id=%s user_id=%s name=%s",
        project.id, user.id, work_item_data["name"],
      )

    # Prune old messages — keep last 20 per group
    old_messages_result = await db.execute(
      select(LineMessage.id)
      .where(
        LineMessage.company_id == company_id,
        LineMessage.line_group_id == group_id,
      )
      .order_by(LineMessage.created_at.desc())
      .offset(20)
    )
    old_ids = old_messages_result.scalars().all()
    if old_ids:
      await db.execute(
        delete(LineMessage).where(LineMessage.id.in_(old_ids))
      )

    await db.commit()