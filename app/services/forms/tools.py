from dataclasses import dataclass
from typing import Any
from uuid import UUID

from agents import RunContextWrapper
from agents.decorators import function_tool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
  Company,
  Project,
  User,
  CustomField,
  CustomFieldCompanyLink,
  CustomFieldProjectLink,
  CustomFieldUserLink,
  CustomFieldCustomObjectLink,
  CustomObject,
  CustomObjectDefinition,
  CustomFieldDefinition,
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


def _user_name(user: User) -> str:
  name = " ".join(
    part
    for part in [
      user.first_name,
      user.last_name,
    ]
    if part
  )

  return name or user.email


def _field_dict(
  field: CustomField,
  scope: str,
  entity_id: UUID,
) -> dict[str, Any]:
  return {
    "id": str(field.id),
    "name": field.definition.name,
    "description": field.definition.description,
    "data_type": field.definition.data_type,
    "value": field.value,
    "scope": scope,
    "entity_id": str(entity_id),
  }


def _entity_reference(
  entity_type: str,
  entity_id: UUID,
  *,
  companies: dict[UUID, Company] | None = None,
  projects: dict[UUID, Project] | None = None,
  users: dict[UUID, User] | None = None,
  custom_objects: dict[UUID, CustomObject] | None = None,
) -> dict[str, Any]:
  reference: dict[str, Any] = {
    "entity_type": entity_type,
    "id": str(entity_id),
  }

  if entity_type == "company" and companies:
    company = companies.get(entity_id)

    if company:
      reference["name"] = company.name

  elif entity_type == "project" and projects:
    project = projects.get(entity_id)

    if project:
      reference["name"] = project.name

  elif entity_type == "user" and users:
    user = users.get(entity_id)

    if user:
      reference["name"] = _user_name(user)
      reference["email"] = user.email

  elif entity_type == "custom_object" and custom_objects:
    obj = custom_objects.get(entity_id)

    if obj:
      reference["name"] = obj.definition.name
      reference["custom_object_definition_id"] = str(
        obj.custom_object_definition_id
      )

  return reference


async def _get_company(
  db: AsyncSession,
  company_id: UUID,
) -> dict[str, Any] | None:

  result = await db.execute(
    select(Company)
    .where(
      Company.id == company_id,
    )
  )

  company = result.scalar_one_or_none()

  if company is None:
    return None

  return {
    "id": str(company.id),
    "name": company.name,
    "corporate_number": company.corporate_number,
  }


async def _get_company_custom_fields(
  db: AsyncSession,
  company_id: UUID,
) -> list[dict[str, Any]]:

  result = await db.execute(
    select(CustomField)
    .join(
      CustomFieldCompanyLink,
      CustomFieldCompanyLink.custom_field_id == CustomField.id,
    )
    .join(
      CustomFieldDefinition,
      CustomFieldDefinition.id == CustomField.custom_field_definition_id,
    )
    .where(
      CustomFieldCompanyLink.company_id == company_id,
      CustomField.company_id == company_id,
    )
    .order_by(
      CustomFieldDefinition.sort_order,
      CustomFieldDefinition.name,
    )
  )

  fields = result.scalars().all()

  return [
    _field_dict(
      field,
      "company",
      company_id,
    )
    for field in fields
  ]


async def _get_project_custom_fields(
  db: AsyncSession,
  company_id: UUID,
  project_id: UUID,
) -> list[dict[str, Any]]:

  result = await db.execute(
    select(CustomField)
    .join(
      CustomFieldProjectLink,
      CustomFieldProjectLink.custom_field_id == CustomField.id,
    )
    .join(
      CustomFieldDefinition,
      CustomFieldDefinition.id == CustomField.custom_field_definition_id,
    )
    .where(
      CustomFieldProjectLink.project_id == project_id,
      CustomField.company_id == company_id,
    )
    .order_by(
      CustomFieldDefinition.sort_order,
      CustomFieldDefinition.name,
    )
  )

  fields = result.scalars().all()

  return [
    _field_dict(
      field,
      "project",
      project_id,
    )
    for field in fields
  ]


async def _get_user_custom_fields(
  db: AsyncSession,
  company_id: UUID,
  user_id: UUID,
) -> list[dict[str, Any]]:

  result = await db.execute(
    select(CustomField)
    .join(
      CustomFieldUserLink,
      CustomFieldUserLink.custom_field_id == CustomField.id,
    )
    .join(
      CustomFieldDefinition,
      CustomFieldDefinition.id == CustomField.custom_field_definition_id,
    )
    .where(
      CustomFieldUserLink.user_id == user_id,
      CustomField.company_id == company_id,
    )
    .order_by(
      CustomFieldDefinition.sort_order,
      CustomFieldDefinition.name,
    )
  )

  fields = result.scalars().all()

  return [
    _field_dict(
      field,
      "user",
      user_id,
    )
    for field in fields
  ]


async def _get_custom_object_fields(
  db: AsyncSession,
  company_id: UUID,
  custom_object_id: UUID,
) -> list[dict[str, Any]]:

  result = await db.execute(
    select(CustomField)
    .join(
      CustomFieldCustomObjectLink,
      CustomFieldCustomObjectLink.custom_field_id == CustomField.id,
    )
    .join(
      CustomFieldDefinition,
      CustomFieldDefinition.id == CustomField.custom_field_definition_id,
    )
    .where(
      CustomFieldCustomObjectLink.custom_object_id == custom_object_id,
      CustomField.company_id == company_id,
    )
    .order_by(
      CustomFieldDefinition.sort_order,
      CustomFieldDefinition.name,
    )
  )

  fields = result.scalars().all()

  return [
    _field_dict(
      field,
      "custom_object",
      custom_object_id,
    )
    for field in fields
  ]


async def _get_projects(
  db: AsyncSession,
  company_id: UUID,
) -> list[Project]:

  result = await db.execute(
    select(Project)
    .where(
      Project.company_id == company_id,
    )
    .order_by(
      Project.name,
    )
  )

  return result.scalars().all()


async def _get_users(
  db: AsyncSession,
  company_id: UUID,
) -> list[User]:

  result = await db.execute(
    select(User)
    .where(
      User.company_id == company_id,
    )
    .order_by(
      User.last_name,
      User.first_name,
      User.email,
    )
  )

  return result.scalars().all()


async def _get_custom_objects(
  db: AsyncSession,
  company_id: UUID,
) -> list[CustomObject]:

  result = await db.execute(
    select(CustomObject)
    .where(
      CustomObject.company_id == company_id,
    )
    .options(
      selectinload(CustomObject.definition),
    )
    .order_by(
      CustomObject.created_at,
    )
  )

  return result.scalars().all()


async def _get_relationship_definitions(
  db: AsyncSession,
  company_id: UUID,
) -> list[CustomRelationshipDefinition]:

  result = await db.execute(
    select(CustomRelationshipDefinition)
    .where(
      CustomRelationshipDefinition.company_id == company_id,
    )
    .options(
      selectinload(
        CustomRelationshipDefinition.source_custom_object_definition
      ),
      selectinload(
        CustomRelationshipDefinition.target_custom_object_definition
      ),
    )
    .order_by(
      CustomRelationshipDefinition.sort_order,
      CustomRelationshipDefinition.name,
    )
  )

  return result.scalars().all()


async def _get_relationships(
  db: AsyncSession,
  company_id: UUID,
) -> list[CustomRelationship]:

  result = await db.execute(
    select(CustomRelationship)
    .where(
      CustomRelationship.company_id == company_id,
    )
    .options(
      selectinload(CustomRelationship.definition),
    )
    .order_by(
      CustomRelationship.created_at,
    )
  )

  return result.scalars().all()


def _relationship_definition_dict(
  definition: CustomRelationshipDefinition,
) -> dict[str, Any]:

  source_definition = (
    definition.source_custom_object_definition
  )

  target_definition = (
    definition.target_custom_object_definition
  )

  return {
    "id": str(definition.id),
    "name": definition.name,
    "description": definition.description,
    "source_entity_type": _enum_value(
      definition.source_entity_type
    ),
    "source_custom_object_definition": (
      {
        "id": str(source_definition.id),
        "name": source_definition.name,
      }
      if source_definition
      else None
    ),
    "target_entity_type": _enum_value(
      definition.target_entity_type
    ),
    "target_custom_object_definition": (
      {
        "id": str(target_definition.id),
        "name": target_definition.name,
      }
      if target_definition
      else None
    ),
    "cardinality": _enum_value(
      definition.cardinality
    ),
  }


def _relationship_dict(
  relationship: CustomRelationship,
  *,
  companies: dict[UUID, Company],
  projects: dict[UUID, Project],
  users: dict[UUID, User],
  custom_objects: dict[UUID, CustomObject],
) -> dict[str, Any]:

  definition = relationship.definition

  source = _entity_reference(
    relationship.source_entity_type,
    relationship.source_entity_id,
    companies=companies,
    projects=projects,
    users=users,
    custom_objects=custom_objects,
  )

  target = _entity_reference(
    relationship.target_entity_type,
    relationship.target_entity_id,
    companies=companies,
    projects=projects,
    users=users,
    custom_objects=custom_objects,
  )

  return {
    "id": str(relationship.id),
    "name": definition.name,
    "description": definition.description,
    "cardinality": _enum_value(
      definition.cardinality
    ),
    "source": source,
    "target": target,
  }


async def _build_all_company_data(
  db: AsyncSession,
  company_id: UUID,
) -> dict[str, Any]:

  company_result = await db.execute(
    select(Company)
    .where(
      Company.id == company_id,
    )
  )

  company = company_result.scalar_one_or_none()

  if company is None:
    return {
      "error": "Company not found."
    }

  projects = await _get_projects(
    db,
    company_id,
  )

  users = await _get_users(
    db,
    company_id,
  )

  custom_objects = await _get_custom_objects(
    db,
    company_id,
  )

  relationship_definitions = (
    await _get_relationship_definitions(
      db,
      company_id,
    )
  )

  relationships = await _get_relationships(
    db,
    company_id,
  )

  companies_by_id = {
    company.id: company,
  }

  projects_by_id = {
    project.id: project
    for project in projects
  }

  users_by_id = {
    user.id: user
    for user in users
  }

  custom_objects_by_id = {
    obj.id: obj
    for obj in custom_objects
  }

  relationship_data = [
    _relationship_dict(
      relationship,
      companies=companies_by_id,
      projects=projects_by_id,
      users=users_by_id,
      custom_objects=custom_objects_by_id,
    )
    for relationship in relationships
  ]

  company_fields = await _get_company_custom_fields(
    db,
    company_id,
  )

  project_data = []

  for project in projects:
    fields = await _get_project_custom_fields(
      db,
      company_id,
      project.id,
    )

    project_relationships = [
      relationship
      for relationship in relationship_data
      if (
        (
          relationship["source"]["entity_type"] == "project"
          and relationship["source"]["id"] == str(project.id)
        )
        or (
          relationship["target"]["entity_type"] == "project"
          and relationship["target"]["id"] == str(project.id)
        )
      )
    ]

    project_data.append(
      {
        "id": str(project.id),
        "name": project.name,
        "description": project.description,
        "status": _enum_value(project.status),
        "company_id": str(project.company_id),
        "custom_fields": fields,
        "relationships": project_relationships,
      }
    )

  user_data = []

  for user in users:
    fields = await _get_user_custom_fields(
      db,
      company_id,
      user.id,
    )

    user_relationships = [
      relationship
      for relationship in relationship_data
      if (
        (
          relationship["source"]["entity_type"] == "user"
          and relationship["source"]["id"] == str(user.id)
        )
        or (
          relationship["target"]["entity_type"] == "user"
          and relationship["target"]["id"] == str(user.id)
        )
      )
    ]

    user_data.append(
      {
        "id": str(user.id),
        "name": _user_name(user),
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "company_id": (
          str(user.company_id)
          if user.company_id
          else None
        ),
        "custom_fields": fields,
        "relationships": user_relationships,
      }
    )

  custom_object_data = []

  for obj in custom_objects:
    fields = await _get_custom_object_fields(
      db,
      company_id,
      obj.id,
    )

    object_relationships = [
      relationship
      for relationship in relationship_data
      if (
        (
          relationship["source"]["entity_type"]
          == "custom_object"
          and relationship["source"]["id"]
          == str(obj.id)
        )
        or (
          relationship["target"]["entity_type"]
          == "custom_object"
          and relationship["target"]["id"]
          == str(obj.id)
        )
      )
    ]

    custom_object_data.append(
      {
        "id": str(obj.id),
        "company_id": str(obj.company_id),
        "definition": {
          "id": str(obj.definition.id),
          "name": obj.definition.name,
          "description": obj.definition.description,
        },
        "fields": fields,
        "relationships": object_relationships,
      }
    )

  return {
    "company": {
      "id": str(company.id),
      "name": company.name,
      "corporate_number": company.corporate_number,
      "custom_fields": company_fields,
      "relationships": [
        relationship
        for relationship in relationship_data
        if (
          (
            relationship["source"]["entity_type"]
            == "company"
            and relationship["source"]["id"]
            == str(company.id)
          )
          or (
            relationship["target"]["entity_type"]
            == "company"
            and relationship["target"]["id"]
            == str(company.id)
          )
        )
      ],
    },
    "projects": project_data,
    "users": user_data,
    "custom_objects": custom_object_data,
    "relationship_definitions": [
      _relationship_definition_dict(definition)
      for definition in relationship_definitions
    ],
  }


async def _get_project(
  db: AsyncSession,
  company_id: UUID,
  project_id: UUID,
) -> dict[str, Any] | None:

  result = await db.execute(
    select(Project)
    .where(
      Project.id == project_id,
      Project.company_id == company_id,
    )
  )

  project = result.scalar_one_or_none()

  if project is None:
    return None

  fields = await _get_project_custom_fields(
    db,
    company_id,
    project_id,
  )

  return {
    "id": str(project.id),
    "name": project.name,
    "description": project.description,
    "status": _enum_value(project.status),
    "company_id": str(project.company_id),
    "custom_fields": fields,
  }


async def _get_custom_object(
  db: AsyncSession,
  company_id: UUID,
  custom_object_id: UUID,
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

  if obj is None:
    return None

  fields = await _get_custom_object_fields(
    db,
    company_id,
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
  }


@function_tool
async def get_company_information(
  ctx: RunContextWrapper[FormAgentContext],
) -> dict[str, Any]:
  """Get the current company and its custom fields."""

  company = await _get_company(
    ctx.context.db,
    ctx.context.company_id,
  )

  if company is None:
    return {
      "error": "Company not found."
    }

  company["custom_fields"] = (
    await _get_company_custom_fields(
      ctx.context.db,
      ctx.context.company_id,
    )
  )

  return company


@function_tool
async def get_project_information(
  ctx: RunContextWrapper[FormAgentContext],
) -> dict[str, Any]:
  """Get the project associated with the current form job.

  Use this when the form job has a project_id.
  """

  if ctx.context.project_id is None:
    return {
      "project": None,
      "message": "No project is associated with this form."
    }

  project = await _get_project(
    ctx.context.db,
    ctx.context.company_id,
    ctx.context.project_id,
  )

  if project is None:
    return {
      "error": "Project not found."
    }

  return project


@function_tool
async def get_custom_object(
  ctx: RunContextWrapper[FormAgentContext],
  custom_object_id: str,
) -> dict[str, Any]:
  """Get a specific custom object, including its definition and custom fields."""

  try:
    object_id = UUID(custom_object_id)
  except ValueError:
    return {
      "error": "Invalid custom object ID."
    }

  obj = await _get_custom_object(
    ctx.context.db,
    ctx.context.company_id,
    object_id,
  )

  if obj is None:
    return {
      "error": "Custom object not found."
    }

  return obj


@function_tool
async def get_form_data(
  ctx: RunContextWrapper[FormAgentContext],
) -> dict[str, Any]:
  """Get the complete Kenchiku data available to this form job.

  Returns all information for the current company, including:

  - Company information and custom fields.
  - Every project belonging to the company.
  - Every company user.
  - Every custom object belonging to the company.
  - Custom fields on every project, user, and custom object.
  - All actual custom relationship instances.
  - Human-readable information about the entities on both sides of
    every relationship.
  - All custom relationship definitions, including their names,
    descriptions, entity types, custom object types, and cardinality.

  IMPORTANT:

  Project/user associations are represented by custom relationships.
  Do not rely on ProjectUserLink or ProjectGuestLink.

  If a form job has a project_id, that project is the project that the
  form is associated with. Use its relationships to identify users,
  companies, and other entities associated with that specific project.
  """

  return await _build_all_company_data(
    ctx.context.db,
    ctx.context.company_id,
  )