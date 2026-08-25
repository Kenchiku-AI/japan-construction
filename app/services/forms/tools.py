from dataclasses import dataclass
from typing import Any
from uuid import UUID

from agents import RunContextWrapper
from agents.decorators import function_tool
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
  Company,
  Project,
  User,
  ProjectUserLink,
  ProjectGuestLink,
  CustomField,
  CustomFieldCompanyLink,
  CustomFieldProjectLink,
  CustomFieldUserLink,
  CustomFieldCustomObjectLink,
  CustomObject,
  CustomObjectDefinition,
  CustomRelationship,
  CustomRelationshipDefinition,
)


@dataclass
class FormAgentContext:
  db: AsyncSession
  company_id: UUID
  project_id: UUID | None


def _enum_value(value: Any) -> Any:
  if hasattr(value, "value"):
    return value.value

  return value


async def _get_company(
  db: AsyncSession,
  company_id,
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


async def _get_project(
  db: AsyncSession,
  company_id,
  project_id,
) -> dict[str, Any] | None:
  result = await db.execute(
    select(Project).where(
      Project.id == project_id,
      Project.company_id == company_id,
    )
  )

  project = result.scalar_one_or_none()

  if not project:
    return None

  return {
    "id": str(project.id),
    "name": project.name,
    "description": project.description,
    "status": _enum_value(project.status),
    "company_id": str(project.company_id),
  }


async def _get_project_users(
  db: AsyncSession,
  company_id,
  project_id,
) -> list[dict[str, Any]]:
  assigned_result = await db.execute(
    select(User)
    .join(
      ProjectUserLink,
      ProjectUserLink.user_id == User.id,
    )
    .where(
      ProjectUserLink.project_id == project_id,
      User.company_id == company_id,
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
      User.company_id == company_id,
    )
  )

  guest_users = guest_result.scalars().all()

  users_by_id = {}

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


async def _get_company_custom_fields(
  db: AsyncSession,
  company_id,
) -> list[dict[str, Any]]:
  result = await db.execute(
    select(CustomField)
    .join(
      CustomFieldCompanyLink,
      CustomFieldCompanyLink.custom_field_id == CustomField.id,
    )
    .where(
      CustomFieldCompanyLink.company_id == company_id,
      CustomField.company_id == company_id,
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


async def _get_project_custom_fields(
  db: AsyncSession,
  company_id,
  project_id,
) -> list[dict[str, Any]]:
  result = await db.execute(
    select(CustomField)
    .join(
      CustomFieldProjectLink,
      CustomFieldProjectLink.custom_field_id == CustomField.id,
    )
    .join(
      Project,
      Project.id == CustomFieldProjectLink.project_id,
    )
    .where(
      CustomFieldProjectLink.project_id == project_id,
      Project.company_id == company_id,
      CustomField.company_id == company_id,
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


async def _get_user_custom_fields(
  db: AsyncSession,
  company_id,
  user_id,
) -> list[dict[str, Any]]:
  result = await db.execute(
    select(CustomField)
    .join(
      CustomFieldUserLink,
      CustomFieldUserLink.custom_field_id == CustomField.id,
    )
    .join(
      User,
      User.id == CustomFieldUserLink.user_id,
    )
    .where(
      CustomFieldUserLink.user_id == user_id,
      User.company_id == company_id,
      CustomField.company_id == company_id,
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


async def _get_custom_object_fields(
  db: AsyncSession,
  company_id,
  custom_object_id,
) -> list[dict[str, Any]]:
  result = await db.execute(
    select(CustomField)
    .join(
      CustomFieldCustomObjectLink,
      CustomFieldCustomObjectLink.custom_field_id == CustomField.id,
    )
    .join(
      CustomObject,
      CustomObject.id == CustomFieldCustomObjectLink.custom_object_id,
    )
    .where(
      CustomFieldCustomObjectLink.custom_object_id == custom_object_id,
      CustomObject.company_id == company_id,
      CustomField.company_id == company_id,
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


async def _get_custom_relationships_for_entity(
  db: AsyncSession,
  company_id,
  entity_type: str,
  entity_id,
) -> list[dict[str, Any]]:
  result = await db.execute(
    select(CustomRelationship)
    .join(
      CustomRelationshipDefinition,
      CustomRelationshipDefinition.id
      == CustomRelationship.custom_relationship_definition_id,
    )
    .where(
      CustomRelationship.company_id == company_id,
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
      ),
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
        "source_entity_type": _enum_value(
          relationship.definition.source_entity_type,
        ),
        "target_entity_type": _enum_value(
          relationship.definition.target_entity_type,
        ),
        "cardinality": _enum_value(
          relationship.definition.cardinality,
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


async def _get_custom_object(
  db: AsyncSession,
  company_id,
  custom_object_id,
) -> dict[str, Any] | None:
  result = await db.execute(
    select(CustomObject)
    .where(
      CustomObject.id == custom_object_id,
      CustomObject.company_id == company_id,
    )
    .options(
      selectinload(CustomObject.definition),
    )
  )

  obj = result.scalar_one_or_none()

  if not obj:
    return None

  fields = await _get_custom_object_fields(
    db,
    company_id,
    custom_object_id,
  )

  relationships = await _get_custom_relationships_for_entity(
    db,
    company_id,
    "custom_object",
    custom_object_id,
  )

  return {
    "id": str(obj.id),
    "company_id": str(obj.company_id),
    "definition": {
      "id": str(obj.definition.id),
      "name": obj.definition.name,
      "description": obj.definition.description,
    },
    "fields": fields,
    "relationships": relationships,
  }


async def _get_company_custom_objects(
  db: AsyncSession,
  company_id,
) -> list[dict[str, Any]]:
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

  result_data = []

  for obj in objects:
    fields = await _get_custom_object_fields(
      db,
      company_id,
      obj.id,
    )

    relationships = await _get_custom_relationships_for_entity(
      db,
      company_id,
      "custom_object",
      obj.id,
    )

    result_data.append(
      {
        "id": str(obj.id),
        "company_id": str(obj.company_id),
        "definition": {
          "id": str(obj.definition.id),
          "name": obj.definition.name,
          "description": obj.definition.description,
        },
        "fields": fields,
        "relationships": relationships,
      }
    )

  return result_data


@function_tool
async def get_company_information(
  ctx: RunContextWrapper[FormAgentContext],
) -> dict[str, Any]:
  """Get information about the company associated with the current form."""

  company = await _get_company(
    ctx.context.db,
    ctx.context.company_id,
  )

  if not company:
    return {
      "error": "Company not found.",
    }

  return company


@function_tool
async def get_project_information(
  ctx: RunContextWrapper[FormAgentContext],
) -> dict[str, Any]:
  """Get information about the project associated with the current form."""

  if ctx.context.project_id is None:
    return {
      "project": None,
      "message": "No project is associated with this form.",
    }

  project = await _get_project(
    ctx.context.db,
    ctx.context.company_id,
    ctx.context.project_id,
  )

  if not project:
    return {
      "error": "Project not found.",
    }

  return project


@function_tool
async def get_project_users(
  ctx: RunContextWrapper[FormAgentContext],
) -> list[dict[str, Any]]:
  """Get the users assigned to the current project, including project guests."""

  if ctx.context.project_id is None:
    return []

  return await _get_project_users(
    ctx.context.db,
    ctx.context.company_id,
    ctx.context.project_id,
  )


@function_tool
async def get_custom_object(
  ctx: RunContextWrapper[FormAgentContext],
  custom_object_id: str,
) -> dict[str, Any]:
  """Get a custom object by ID, including its fields and relationships.

  Args:
    custom_object_id: The ID of the custom object to retrieve.
  """

  from uuid import UUID

  try:
    object_id = UUID(custom_object_id)
  except ValueError:
    return {
      "error": "Invalid custom object ID.",
    }

  obj = await _get_custom_object(
    ctx.context.db,
    ctx.context.company_id,
    object_id,
  )

  if not obj:
    return {
      "error": "Custom object not found.",
    }

  return obj


@function_tool
async def get_form_data(
  ctx: RunContextWrapper[FormAgentContext],
) -> dict[str, Any]:
  """Get all available company and project data that may be relevant to filling out the form.

  This includes company information, project information, project users,
  company custom fields, project custom fields, custom objects, and
  project relationships.

  Project-specific data is omitted when no project is associated with
  the current form.
  """

  db = ctx.context.db
  company_id = ctx.context.company_id
  project_id = ctx.context.project_id

  company = await _get_company(
    db,
    company_id,
  )

  if not company:
    return {
      "error": "Company not found.",
    }

  project = None
  users = []
  project_fields = []
  relationships = []

  if project_id is not None:
    project = await _get_project(
      db,
      company_id,
      project_id,
    )

    if not project:
      return {
        "error": "Project not found.",
      }

    users = await _get_project_users(
      db,
      company_id,
      project_id,
    )

    project_fields = await _get_project_custom_fields(
      db,
      company_id,
      project_id,
    )

    relationships = await _get_custom_relationships_for_entity(
      db,
      company_id,
      "project",
      project_id,
    )

  company_fields = await _get_company_custom_fields(
    db,
    company_id,
  )

  custom_objects = await _get_company_custom_objects(
    db,
    company_id,
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
    "relationships": relationships,
  }