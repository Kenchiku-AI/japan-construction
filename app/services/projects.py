from uuid import UUID
import logging

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.user import User
from app.db.models.project import Project
from app.db.models.project_guest_link import ProjectGuestLink
from app.db.models.line_message import LineMessage
from app.db.models.action_item import ActionItem, ActionItemStatus
from app.db.session import AsyncSessionLocal
from app.services.openai import extract_action_item

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

async def handle_line_group_action_item(
  text: str,
  project: Project,
  sender_line_user_id: str,
  group_id: str,
  company_id: UUID,
  line_timestamp: datetime | None,
) -> None:
  async with AsyncSessionLocal() as db:
    # Save message to history
    current_message = LineMessage(
      company_id=company_id,
      line_group_id=group_id,
      sender_line_user_id=sender_line_user_id,
      text=text,
      triggered_action_item_id=None,
      line_timestamp=line_timestamp,
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

    # Fetch recent action items for this project for update context
    recent_action_items_result = await db.execute(
      select(ActionItem)
      .where(ActionItem.project_id == project.id)
      .order_by(ActionItem.created_at.desc())
      .limit(5)
    )
    recent_action_items = recent_action_items_result.scalars().all()

    # Attempt action item extraction
    action_item_data = await extract_action_item(
      message_text=text,
      project=project,
      recent_messages=recent_messages,
      recent_action_items=recent_action_items,
    )

    if action_item_data:
      action = action_item_data.get("action")

      if action == "create":
        new_action_item = ActionItem(
          project_id=project.id,
          name=action_item_data["name"],
          description=action_item_data.get("description"),
          status=ActionItemStatus.new,
          source_message_text=text,
        )
        db.add(new_action_item)
        await db.flush()

        # Link this message to the action item it created
        current_message.triggered_action_item_id = new_action_item.id

        logger.info(
          "LINE action item created | project_id=%s name=%s",
          project.id, action_item_data["name"],
        )

      elif action == "update":
        action_item_id = action_item_data.get("action_item_id")
        if action_item_id:
          update_result = await db.execute(
            select(ActionItem).where(
              ActionItem.id == action_item_id,
              ActionItem.project_id == project.id,
            )
          )
          action_item = update_result.scalar_one_or_none()

          if action_item:
            action_item.description = action_item_data["description"]

            # Also link this message to the action item it updated
            current_message.triggered_action_item_id = UUID(action_item_id)

            logger.info(
              "LINE action item updated | action_item_id=%s",
              action_item_id,
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