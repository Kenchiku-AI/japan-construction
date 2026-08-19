import logging

from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.custom_field import CustomFieldRead
from app.schemas.user import (
  UserCompanyRead, 
  UserProjectRead, 
  UserWithCompanyAndProjects,
)
from app.db.models.custom_field import CustomField, CustomFieldUserLink
from app.db.models.custom_field_definition import CustomFieldDefinition
from app.db.models.user import User
from app.db.models.project import Project
from app.db.models.project_guest_link import ProjectGuestLink

logger = logging.getLogger(__name__)

async def build_user_with_company_and_projects(
  user: User,
  db: AsyncSession,
) -> UserWithCompanyAndProjects:
  user_result = await db.execute(
    select(User)
    .options(
      selectinload(User.company),
      selectinload(User.custom_field_links)
        .selectinload(CustomFieldUserLink.custom_field)
        .selectinload(CustomField.definition),
    )
    .where(User.id == user.id)
  )
  user = user_result.scalar_one()

  projects_data: list[UserProjectRead] = []
  company = None
  guest_projects = []

  if user.role == "admin":
    projects_result = await db.execute(
      select(Project).order_by(Project.updated_at.desc()).limit(25)
    )
    projects = projects_result.scalars().all()

  elif user.company_id:
    company = user.company
    projects_result = await db.execute(
      select(Project)
      .where(Project.company_id == company.id)
      .order_by(Project.updated_at.desc())
      .limit(25)
    )
    projects = projects_result.scalars().all()

    guest_result = await db.execute(
      select(Project)
      .join(ProjectGuestLink, ProjectGuestLink.project_id == Project.id)
      .where(ProjectGuestLink.user_id == user.id)
      .order_by(Project.updated_at.desc())
    )
    guest_projects = guest_result.scalars().all()

  else:
    projects = []

    guest_result = await db.execute(
      select(Project)
      .join(ProjectGuestLink, ProjectGuestLink.project_id == Project.id)
      .where(ProjectGuestLink.user_id == user.id)
      .order_by(Project.updated_at.desc())
    )
    guest_projects = guest_result.scalars().all()

  seen: set = set()
  
  for project in list(projects) + list(guest_projects):
    if project.id not in seen:
      seen.add(project.id)
      projects_data.append(UserProjectRead(
        id=project.id,
        name=project.name,
        description=project.description,
        status=project.status,
      ))

  custom_fields = await get_user_custom_fields(
    user,
    db,
  )

  return UserWithCompanyAndProjects(
    id=user.id,
    first_name=user.first_name,
    last_name=user.last_name,
    email=user.email,
    role=user.role,
    created_at=user.created_at,
    updated_at=user.updated_at,
    company=(
      UserCompanyRead(
        id=company.id,
        name=company.name,
        corporate_number=company.corporate_number,
      )
      if company
      else None
    ),
    projects=projects_data,
    custom_fields=custom_fields,
  )

async def get_user_custom_fields(
  user: User,
  db: AsyncSession,
) -> list[CustomFieldRead]:
  if user.company_id is None:
    return []

  definitions_result = await db.execute(
    select(CustomFieldDefinition)
    .where(
      CustomFieldDefinition.company_id == user.company_id,
      CustomFieldDefinition.entity_type == "user",
    )
    .order_by(
      CustomFieldDefinition.sort_order,
      CustomFieldDefinition.name,
    )
  )

  definitions = definitions_result.scalars().all()

  existing_fields = {
    link.custom_field.custom_field_definition_id: link.custom_field
    for link in user.custom_field_links
  }

  return [
    CustomFieldRead(
      id=existing_fields[definition.id].id
        if definition.id in existing_fields
        else None,
      value=existing_fields[definition.id].value
        if definition.id in existing_fields
        else None,
      definition=definition,
    )
    for definition in definitions
  ]