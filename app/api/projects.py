from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, case, or_, func, and_
from sqlalchemy.orm import selectinload


from app.core.dependencies import get_current_user, require_company_manager, require_project_access
from app.db.session import get_db
from app.db.models import (
  Project, 
  Company, 
  User, 
  ProjectStatus, 
  ProjectGuestLink,
  LineConversation,
  LineMessage,
  ConversationItem,
  ConversationItemType,
  ConversationItemTypeLink,
  ProjectUserLink,
  CustomField,
  CustomFieldProjectLink,
)
from app.schemas.project import (
  ProjectCreate, 
  ProjectUpdate,
  ProjectWithCompanyName,
  ProjectWithLists,
  ProjectSetUsers,
)
from app.schemas.user import UserRead
from app.services.billing import can_use_billed_features

router = APIRouter(prefix="/projects", tags=["Projects"])

async def get_project_conversation_items(
  db: AsyncSession,
  project_id: UUID,
):
  ranked_items = (
    select(
      ConversationItem.id,
      ConversationItem.conversation_item_type_id,
      func.row_number()
      .over(
        partition_by=ConversationItem.conversation_item_type_id,
        order_by=ConversationItem.updated_at.desc(),
      )
      .label("row_num"),
    )
    .where(
      ConversationItem.project_id == project_id,
    )
    .subquery()
  )

  linked_item_types = (
    select(
      ConversationItemTypeLink.item_type_id.label("item_type_id")
    )
    .join(
      LineConversation,
      LineConversation.id == ConversationItemTypeLink.conversation_id,
    )
    .where(
      LineConversation.project_id == project_id,
    )
  )

  existing_item_types = (
    select(
      ConversationItem.conversation_item_type_id.label("item_type_id")
    )
    .where(
      ConversationItem.project_id == project_id,
    )
  )

  relevant_item_types = (
    linked_item_types
    .union(existing_item_types)
    .subquery()
  )

  result = await db.execute(
    select(
      ConversationItemType,
      ConversationItem,
    )
    .join(
      relevant_item_types,
      relevant_item_types.c.item_type_id == ConversationItemType.id,
    )
    .outerjoin(
      ranked_items,
      and_(
        ranked_items.c.conversation_item_type_id == ConversationItemType.id,
        ranked_items.c.row_num <= 6,
      ),
    )
    .outerjoin(
      ConversationItem,
      ConversationItem.id == ranked_items.c.id,
    )
    .options(
      selectinload(ConversationItem.item_type),
      selectinload(ConversationItem.assignee),
    )
    .order_by(
      ConversationItemType.name,
      ConversationItem.updated_at.desc(),
    )
  )

  rows = result.all()

  grouped = {}

  for item_type, item in rows:
    if item_type.id not in grouped:
      grouped[item_type.id] = {
        "conversation_item_type_id": item_type.id,
        "conversation_item_type_name": item_type.name,
        "items": [],
      }

    if item is not None:
      grouped[item_type.id]["items"].append(item)

  return list(grouped.values())

async def get_conversation_last_messages(
  db: AsyncSession,
  conversation_ids: list[UUID],
):
  if not conversation_ids:
    return {}

  latest_messages = (
    select(
      LineMessage.conversation_id,
      LineMessage.text.label("last_message_text"),
    )
    .where(
      LineMessage.conversation_id.in_(conversation_ids)
    )
    .order_by(
      LineMessage.conversation_id,
      LineMessage.line_timestamp.desc(),
    )
    .distinct(
      LineMessage.conversation_id
    )
    .subquery()
  )

  result = await db.execute(
    select(
      latest_messages.c.conversation_id,
      latest_messages.c.last_message_text,
    )
  )

  return dict(result.all())

async def build_project_response(
  db: AsyncSession,
  project: Project,
):
  conversation_items = await get_project_conversation_items(
    db,
    project.id,
  )

  conversation_ids = [
    conversation.id
    for conversation in project.conversations
  ]

  last_messages = await get_conversation_last_messages(
    db,
    conversation_ids,
  )

  conversations = []

  for conversation in project.conversations:
    conversation.last_message_text = last_messages.get(
      conversation.id
    )
    conversations.append(conversation)

  custom_fields = [
    link.custom_field
    for link in project.custom_field_links
  ]

  return ProjectWithLists(
    id=project.id,
    name=project.name,
    description=project.description,
    status=project.status,
    company_id=project.company_id,
    company_name=getattr(project, "company_name", None),
    reports=project.reports,
    conversations=conversations,
    conversation_items=conversation_items,
    custom_fields=custom_fields,
    users=project.users,
  )

@router.get("", response_model=List[ProjectWithCompanyName])
async def list_projects(
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user)
):
  if current_user.role == "admin":
    order_by_clause = [
      case((Project.status == ProjectStatus.requested, 0), else_=1),
      desc(Project.updated_at)
    ]

    stmt = (
      select(
        Project,
        Company.name.label("company_name"),
      )
      .outerjoin(Company, Project.company_id == Company.id)
      .options(selectinload(Project.company))
      .order_by(*order_by_clause)
      .limit(25)
    )

    result = await db.execute(stmt)

    rows = result.all()

    projects = []

    for project, company_name in rows:
      project.company_name = company_name
      projects.append(project)

    return projects

  else:
    company_projects = []
    guest_projects = []

    if current_user.company_id:
      company_result = await db.execute(
        select(Project)
        .options(selectinload(Project.company))
        .where(Project.company_id == current_user.company_id)
        .order_by(desc(Project.updated_at))
        .limit(25)
      )
      company_projects = company_result.scalars().all()

    guest_result = await db.execute(
      select(Project)
      .options(selectinload(Project.company))
      .join(ProjectGuestLink, ProjectGuestLink.project_id == Project.id)
      .where(
        ProjectGuestLink.user_id == current_user.id,
        or_(
          Project.company_id != current_user.company_id,
          current_user.company_id == None,
        ),
      )
      .order_by(desc(Project.updated_at))
    )
    guest_projects = guest_result.scalars().all()

    seen = set()
    all_projects = []
    for project in list(company_projects) + list(guest_projects):
      if project.id not in seen:
        seen.add(project.id)
        all_projects.append(project)

    all_projects = sorted(all_projects, key=lambda p: p.updated_at, reverse=True)[:25]

    return all_projects

@router.get("/{project_id}", response_model=ProjectWithLists)
async def get_project(
  project_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role == "admin":
    stmt = (
      select(
        Project,
        Company.name.label("company_name"),
      )
      .outerjoin(Company, Project.company_id == Company.id)
      .where(Project.id == project_id)
      .options(
        selectinload(Project.reports),
        selectinload(Project.users),
        selectinload(Project.conversations)
          .selectinload(LineConversation.item_type_links)
          .selectinload(ConversationItemTypeLink.item_type), 
        selectinload(Project.custom_field_links)
          .selectinload(CustomFieldProjectLink.custom_field)
          .selectinload(CustomField.definition),
      )
    )

    result = await db.execute(stmt)
    row = result.first()

    if not row:
      raise HTTPException(status_code=404, detail="Project not found")

    project, company_name = row

    project.company_name = company_name

  else:
    stmt = (
      select(Project)
      .where(Project.id == project_id)
      .options(
        selectinload(Project.reports),
        selectinload(Project.users),
        selectinload(Project.conversations)
          .selectinload(LineConversation.item_type_links)
          .selectinload(ConversationItemTypeLink.item_type),
        selectinload(Project.custom_field_links)
          .selectinload(CustomFieldProjectLink.custom_field)
          .selectinload(CustomField.definition),
      )
    )

    result = await db.execute(stmt)
    project = result.scalars().first()

    if not project:
      raise HTTPException(status_code=404, detail="Project not found")

    await require_project_access(current_user, project_id, project.company_id, db)

  return await build_project_response(
    db,
    project,
  )

@router.post(
  "",
  response_model=ProjectWithLists,
  status_code=status.HTTP_201_CREATED,
)
async def create_project(
  payload: ProjectCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  allowed, reason = await can_use_billed_features(payload.company_id, db)
  if not allowed and current_user.role != "admin":
    raise HTTPException(status_code=402, detail=reason)

  company = await db.get(Company, payload.company_id)

  project = Project(
    **payload.model_dump(),
    status=ProjectStatus.active,
  )

  db.add(project)
  await db.commit()
  await db.refresh(project)

  return ProjectWithLists(
    id=project.id,
    name=project.name,
    description=project.description,
    status=project.status,
    company_id=project.company_id,
    reports=[],
    conversations=[],
    conversation_items=[],
    users=[],
    custom_fields=[],
  )

@router.patch("/{project_id}", response_model=ProjectWithLists)
async def update_project(
  project_id: UUID,
  payload: ProjectUpdate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  project = await db.get(Project, project_id)
  if not project:
    raise HTTPException(status_code=404, detail="Project not found")

  if current_user.role != "admin":
    require_company_manager(current_user, project.company_id)

  for field, value in payload.model_dump(exclude_unset=True).items():
    setattr(project, field, value)

  await db.commit()

  stmt = (
    select(Project)
    .where(Project.id == project_id)
    .options(
      selectinload(Project.reports),
      selectinload(Project.users),
      selectinload(Project.conversations)
        .selectinload(LineConversation.item_type_links)
        .selectinload(ConversationItemTypeLink.item_type),
      selectinload(Project.custom_field_links)
        .selectinload(CustomFieldProjectLink.custom_field)
        .selectinload(CustomField.definition),
    )
  )

  result = await db.execute(stmt)
  project = result.scalars().first()
  if not project:
    raise HTTPException(status_code=404, detail="Project not found")

  return await build_project_response(
    db,
    project,
  )

@router.delete(
  "/{project_id}/guests/{guest_link_id}",
  status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_project_guest(
  project_id: UUID,
  guest_link_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  project = await db.get(Project, project_id)
  if not project:
    raise HTTPException(status_code=404, detail="Project not found")

  result = await db.execute(
    select(ProjectGuestLink).where(
      ProjectGuestLink.id == guest_link_id,
      ProjectGuestLink.project_id == project_id,
    )
  )
  guest_link = result.scalar_one_or_none()

  if not guest_link:
    raise HTTPException(status_code=404, detail="Guest link not found")

  if current_user.role == "admin":
    pass
  elif current_user.role == "manager" and current_user.company_id == project.company_id:
    pass
  elif guest_link.user_id == current_user.id:
    pass
  else:
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Not authorized to remove this guest",
    )

  await db.delete(guest_link)
  await db.commit()

  return None

@router.delete(
  "/{project_id}",
  status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_project(
  project_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role != "admin":
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Only admins can delete projects",
    )

  project = await db.get(Project, project_id)

  if not project:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Project not found",
    )

  await db.delete(project)
  await db.commit()

  return None

@router.put(
  "/{project_id}/users",
  response_model=List[UserRead],
)
async def set_project_users(
  project_id: UUID,
  payload: ProjectSetUsers,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  project = await db.get(Project, project_id)

  if not project:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Project not found",
    )

  if current_user.role != "admin":
    require_company_manager(
      current_user,
      project.company_id,
    )

  user_ids = list(set(payload.user_ids))

  if user_ids:
    result = await db.execute(
      select(User).where(
        User.id.in_(user_ids),
        User.company_id == project.company_id,
      )
    )

    users = result.scalars().all()

    found_user_ids = {
      user.id
      for user in users
    }

    missing_user_ids = [
      user_id
      for user_id in user_ids
      if user_id not in found_user_ids
    ]

    if missing_user_ids:
      raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="One or more users do not belong to the project company",
      )
  else:
    users = []

  result = await db.execute(
    select(ProjectUserLink).where(
      ProjectUserLink.project_id == project_id
    )
  )

  existing_links = result.scalars().all()

  existing_user_ids = {
    link.user_id
    for link in existing_links
  }

  requested_user_ids = set(user_ids)

  links_to_delete = [
    link
    for link in existing_links
    if link.user_id not in requested_user_ids
  ]

  links_to_create = [
    user_id
    for user_id in requested_user_ids
    if user_id not in existing_user_ids
  ]

  for link in links_to_delete:
    await db.delete(link)

  for user_id in links_to_create:
    db.add(
      ProjectUserLink(
        project_id=project_id,
        user_id=user_id,
      )
    )

  await db.commit()

  result = await db.execute(
    select(User)
    .join(
      ProjectUserLink,
      ProjectUserLink.user_id == User.id,
    )
    .where(
      ProjectUserLink.project_id == project_id,
    )
    .order_by(
      User.first_name,
      User.last_name,
      User.email,
    )
  )

  return result.scalars().all()