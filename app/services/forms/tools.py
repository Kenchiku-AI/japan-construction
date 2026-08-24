from typing import Any
from uuid import UUID

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
  Company,
  Project,
  User,
  ProjectUserLink,
  ProjectGuestLink,
  CustomField,
  CustomFieldDefinition,
  CustomFieldCompanyLink,
  CustomFieldProjectLink,
  CustomFieldUserLink,
  CustomFieldCustomObjectLink,
  CustomObject,
  CustomObjectDefinition,
  CustomRelationship,
  CustomRelationshipDefinition,
)


async def get_company(
  db: AsyncSession,
  company_id: UUID,
) -> dict[str, Any] | None:
  result = await db.execute(
    select(Company).where(
      Company.id == company_id,
    )
  )

  company = result.scalar_one_or_none()

  if not company:
    return None

  return {
    "id": str(company.id),
    "name": company.name,
    "corporate_number": company.corporate_number,
  }


async def get_project(
  db: AsyncSession,
  project_id: UUID,
) -> dict[str, Any] | None:
  result = await db.execute(
    select(Project).where(
      Project.id == project_id,
    )
  )

  project = result.scalar_one_or_none()

  if not project:
    return None

  return {
    "id": str(project.id),
    "name": project.name,
    "description": project.description,
    "status": project.status.value if hasattr(project.status, "value") else project.status,
    "company_id": str(project.company_id),
  }


async def get_user(
  db: AsyncSession,
  user_id: UUID,
) -> dict[str, Any] | None:
  result = await db.execute(
    select(User).where(
      User.id == user_id,
    )
  )

  user = result.scalar_one_or_none()

  if not user:
    return None

  return {
    "id": str(user.id),
    "email": user.email,
    "first_name": user.first_name,
    "last_name": user.last_name,
    "company_id": str(user.company_id) if user.company_id else None,
  }


async def get_project_users(
  db: AsyncSession,
  project_id: UUID,
) -> list[dict[str, Any]]:
  """
  Returns users who should be considered members of a project for
  form-population purposes.

  This includes:

  1. Users explicitly assigned to the project through ProjectUserLink.
  2. Guests linked to the project through ProjectGuestLink.

  Company users who are not explicitly assigned to the project are
  intentionally excluded.

  Duplicate users are removed.
  """

  assigned_result = await db.execute(
    select(User)
    .join(
      ProjectUserLink,
      ProjectUserLink.user_id == User.id,
    )
    .where(
      ProjectUserLink.project_id == project_id,
    )
  )

  assigned_users = assigned_result.scalars().all()

  guest_result = await db.execute(
    select(User)
    .join(
      ProjectGuestLink,
      ProjectGuestLink.user_id == User.id,
    )
    .where(
      ProjectGuestLink.project_id == project_id,
    )
  )

  guest_users = guest_result.scalars().all()

  users_by_id: dict[UUID, User] = {}

  for user in assigned_users:
    users_by_id[user.id] = user

  for user in guest_users:
    users_by_id[user.id] = user

  return [
    {
      "id": str(user.id),
      "email": user.email,
      "first_name": user.first_name,
      "last_name": user.last_name,
      "company_id": str(user.company_id) if user.company_id else None,
    }
    for user in users_by_id.values()
  ]


async def get_company_custom_fields(
  db: AsyncSession,
  company_id: UUID,
) -> list[dict[str, Any]]:
  """
  Returns custom fields attached to the company.
  """

  result = await db.execute(
    select(CustomField)
    .join(
      CustomFieldCompanyLink,
      CustomFieldCompanyLink.custom_field_id == CustomField.id,
    )
    .where(
      CustomFieldCompanyLink.company_id == company_id,
    )
    .options(
      selectinload(CustomField.definition),
    )
  )

  fields = result.scalars().all()

  return [
    {
      "id": str(field.id),
      "name": field.definition.name,
      "description": field.definition.description,
      "value": field.value,
      "scope": "company",
      "company_id": str(company_id),
    }
    for field in fields
  ]


async def get_project_custom_fields(
  db: AsyncSession,
  project_id: UUID,
) -> list[dict[str, Any]]:
  """
  Returns custom fields attached directly to a project.
  """

  result = await db.execute(
    select(CustomField)
    .join(
      CustomFieldProjectLink,
      CustomFieldProjectLink.custom_field_id == CustomField.id,
    )
    .where(
      CustomFieldProjectLink.project_id == project_id,
    )
    .options(
      selectinload(CustomField.definition),
    )
  )

  fields = result.scalars().all()

  return [
    {
      "id": str(field.id),
      "name": field.definition.name,
      "description": field.definition.description,
      "value": field.value,
      "scope": "project",
      "project_id": str(project_id),
    }
    for field in fields
  ]


async def get_user_custom_fields(
  db: AsyncSession,
  user_id: UUID,
) -> list[dict[str, Any]]:
  """
  Returns custom fields attached directly to a user.
  """

  result = await db.execute(
    select(CustomField)
    .join(
      CustomFieldUserLink,
      CustomFieldUserLink.custom_field_id == CustomField.id,
    )
    .where(
      CustomFieldUserLink.user_id == user_id,
    )
    .options(
      selectinload(CustomField.definition),
    )
  )

  fields = result.scalars().all()

  return [
    {
      "id": str(field.id),
      "name": field.definition.name,
      "description": field.definition.description,
      "value": field.value,
      "scope": "user",
      "user_id": str(user_id),
    }
    for field in fields
  ]


async def get_custom_object(
  db: AsyncSession,
  custom_object_id: UUID,
) -> dict[str, Any] | None:
  result = await db.execute(
    select(CustomObject)
    .where(
      CustomObject.id == custom_object_id,
    )
    .options(
      selectinload(CustomObject.definition),
    )
  )

  obj = result.scalar_one_or_none()

  if not obj:
    return None

  return {
    "id": str(obj.id),
    "name": obj.name,
    "description": obj.description,
    "company_id": str(obj.company_id),
    "definition": {
      "id": str(obj.definition.id),
      "name": obj.definition.name,
      "description": obj.definition.description,
    },
  }


async def get_company_custom_objects(
  db: AsyncSession,
  company_id: UUID,
) -> list[dict[str, Any]]:
  """
  Returns all custom objects belonging to a company.
  """

  result = await db.execute(
    select(CustomObject)
    .where(
      CustomObject.company_id == company_id,
    )
    .options(
      selectinload(CustomObject.definition),
    )
  )

  objects = result.scalars().all()

  return [
    {
      "id": str(obj.id),
      "name": obj.name,
      "description": obj.description,
      "definition": {
        "id": str(obj.definition.id),
        "name": obj.definition.name,
        "description": obj.definition.description,
      },
    }
    for obj in objects
  ]


async def get_custom_object_fields(
  db: AsyncSession,
  custom_object_id: UUID,
) -> list[dict[str, Any]]:
  """
  Returns fields attached to a custom object.
  """

  result = await db.execute(
    select(CustomField)
    .join(
      CustomFieldCustomObjectLink,
      CustomFieldCustomObjectLink.custom_field_id == CustomField.id,
    )
    .where(
      CustomFieldCustomObjectLink.custom_object_id == custom_object_id,
    )
    .options(
      selectinload(CustomField.definition),
    )
  )

  fields = result.scalars().all()

  return [
    {
      "id": str(field.id),
      "name": field.definition.name,
      "description": field.definition.description,
      "value": field.value,
      "scope": "custom_object",
      "custom_object_id": str(custom_object_id),
    }
    for field in fields
  ]


async def get_custom_relationships_for_entity(
  db: AsyncSession,
  entity_type: str,
  entity_id: UUID,
) -> list[dict[str, Any]]:
  """
  Returns custom relationships where the specified entity is either
  the source or target.

  entity_type must be one of:
    company
    user
    project
    custom_object
  """

  result = await db.execute(
    select(CustomRelationship)
    .join(
      CustomRelationshipDefinition,
      CustomRelationship.definition.id == CustomRelationshipDefinition.id,
    )
    .where(
      or_(
        (
          CustomRelationship.source_entity_type == entity_type
        )
        & (
          CustomRelationship.source_entity_id == entity_id
        ),
        (
          CustomRelationship.target_entity_type == entity_type
        )
        & (
          CustomRelationship.target_entity_id == entity_id
        ),
      )
    )
    .options(
      selectinload(CustomRelationship.definition),
    )
  )

  relationships = result.scalars().all()

  return [
    {
      "id": str(relationship.id),
      "definition": {
        "id": str(relationship.definition.id),
        "name": relationship.definition.name,
        "description": relationship.definition.description,
        "source_entity_type": (
          relationship.definition.source_entity_type.value
          if hasattr(
            relationship.definition.source_entity_type,
            "value",
          )
          else relationship.definition.source_entity_type
        ),
        "target_entity_type": (
          relationship.definition.target_entity_type.value
          if hasattr(
            relationship.definition.target_entity_type,
            "value",
          )
          else relationship.definition.target_entity_type
        ),
        "cardinality": (
          relationship.definition.cardinality.value
          if hasattr(
            relationship.definition.cardinality,
            "value",
          )
          else relationship.definition.cardinality
        ),
      },
      "source": {
        "entity_type": relationship.source_entity_type,
        "entity_id": str(relationship.source_entity_id),
      },
      "target": {
        "entity_type": relationship.target_entity_type,
        "entity_id": str(relationship.target_entity_id),
      },
    }
    for relationship in relationships
  ]


async def get_all_project_form_data(
  db: AsyncSession,
  project_id: UUID,
) -> dict[str, Any] | None:
  """
  Convenience function for gathering the core information that the
  form agent will generally need for a project.

  This is intentionally a deterministic data retrieval function.
  The LLM/agent decides which pieces of this information are relevant
  to a particular form.
  """

  project = await get_project(
    db,
    project_id,
  )

  if not project:
    return None

  company_id = UUID(project["company_id"])

  company = await get_company(
    db,
    company_id,
  )

  users = await get_project_users(
    db,
    project_id,
  )

  company_fields = await get_company_custom_fields(
    db,
    company_id,
  )

  project_fields = await get_project_custom_fields(
    db,
    project_id,
  )

  custom_objects = await get_company_custom_objects(
    db,
    company_id,
  )

  project_relationships = await get_custom_relationships_for_entity(
    db,
    "project",
    project_id,
  )

  return {
    "company": company,
    "project": project,
    "users": users,
    "custom_fields": {
      "company": company_fields,
      "project": project_fields,
    },
    "custom_objects": custom_objects,
    "relationships": project_relationships,
  }