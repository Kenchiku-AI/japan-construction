from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import (
  get_current_user,
  require_company_manager,
)
from app.db.session import get_db
from app.db.models import (
  Company,
  Project,
  User,
  CustomField,
  CustomFieldDefinition,
  CustomFieldCompanyLink,
  CustomFieldProjectLink,
  CustomFieldUserLink,
  CustomFieldCustomObjectLink,
  CustomObject,
  CustomObjectDefinition,
  CustomFieldEntityType,
  CustomRelationshipDefinition,
  CustomRelationshipEntityType,
)
from app.schemas.custom_field import (
  CustomFieldDefinitionCreate,
  CustomFieldDefinitionUpdate,
  CustomFieldDefinitionRead,
  CustomFieldDefinitionsResponse,
  CustomFieldCreate,
  CustomFieldUpdate,
  CustomFieldRead,
)


router = APIRouter(
  prefix="/custom-fields",
  tags=["Custom Fields"],
)


# ---------------------------------------------------------------------------
# Definitions
# ---------------------------------------------------------------------------

@router.get(
  "/definitions",
  response_model=CustomFieldDefinitionsResponse,
)
async def list_custom_field_definitions(
  company_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  require_company_manager(current_user, company_id)

  # Get all field definitions for the company
  definitions_result = await db.execute(
    select(CustomFieldDefinition)
    .where(
      CustomFieldDefinition.company_id == company_id,
    )
    .order_by(CustomFieldDefinition.sort_order)
  )

  definitions = definitions_result.scalars().all()

  project_fields = [
    definition
    for definition in definitions
    if definition.entity_type == "project"
  ]

  user_fields = [
    definition
    for definition in definitions
    if definition.entity_type == "user"
  ]

  company_fields = [
    definition
    for definition in definitions
    if definition.entity_type == "company"
  ]

  relationships_result = await db.execute(
    select(CustomRelationshipDefinition)
    .where(
      CustomRelationshipDefinition.company_id == company_id,
    )
    .order_by(CustomRelationshipDefinition.sort_order)
  )

  relationships = relationships_result.scalars().all()

  project_relationships = [
    definition
    for definition in relationships
    if definition.source_entity_type
    == CustomRelationshipEntityType.project
  ]

  user_relationships = [
    definition
    for definition in relationships
    if definition.source_entity_type
    == CustomRelationshipEntityType.user
  ]

  company_relationships = [
    definition
    for definition in relationships
    if definition.source_entity_type
    == CustomRelationshipEntityType.company
  ]

  custom_object_definitions_result = await db.execute(
    select(CustomObjectDefinition)
    .where(
      CustomObjectDefinition.company_id == company_id,
    )
    .order_by(CustomObjectDefinition.name)
  )

  custom_objects_response = (
    custom_object_definitions_result.scalars().all()
  )

  return CustomFieldDefinitionsResponse(
    project_fields=project_fields,
    project_relationships=project_relationships,
    user_fields=user_fields,
    user_relationships=user_relationships,
    company_fields=company_fields,
    company_relationships=company_relationships,
    custom_objects=custom_objects_response,
  )


@router.post(
  "/definitions",
  response_model=CustomFieldDefinitionRead,
  status_code=status.HTTP_201_CREATED,
)
async def create_custom_field_definition(
  payload: CustomFieldDefinitionCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  require_company_manager(
    current_user,
    payload.company_id,
  )

  custom_object_definition = None

  if payload.entity_type == CustomFieldEntityType.custom_object:
    custom_object_definition = await db.get(
      CustomObjectDefinition,
      payload.custom_object_definition_id,
    )

    if not custom_object_definition:
      raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Custom object definition not found",
      )

    if custom_object_definition.company_id != payload.company_id:
      raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Custom object definition belongs to a different company",
      )

  sort_order = await _get_next_definition_sort_order(
    db=db,
    company_id=payload.company_id,
    entity_type=payload.entity_type,
    custom_object_definition_id=(
      custom_object_definition.id
      if custom_object_definition
      else None
    ),
  )

  definition = CustomFieldDefinition(
    company_id=payload.company_id,
    name=payload.name,
    description=payload.description,
    data_type=payload.data_type,
    entity_type=payload.entity_type,
    custom_object_definition_id=(
      custom_object_definition.id
      if custom_object_definition
      else None
    ),
    sort_order=sort_order,
  )

  db.add(definition)
  await db.commit()
  await db.refresh(definition)

  return definition


async def _get_next_definition_sort_order(
  db: AsyncSession,
  company_id: UUID,
  entity_type: CustomFieldEntityType,
  custom_object_definition_id: UUID | None = None,
) -> int:
  field_query = select(
    func.count(CustomFieldDefinition.id)
  ).where(
    CustomFieldDefinition.company_id == company_id,
    CustomFieldDefinition.entity_type == entity_type.value,
  )

  relationship_query = select(
    func.count(CustomRelationshipDefinition.id)
  ).where(
    CustomRelationshipDefinition.company_id == company_id,
    CustomRelationshipDefinition.source_entity_type == entity_type.value,
  )

  if entity_type == CustomFieldEntityType.custom_object:
    field_query = field_query.where(
      CustomFieldDefinition.custom_object_definition_id
      == custom_object_definition_id
    )

    relationship_query = relationship_query.where(
      CustomRelationshipDefinition.source_custom_object_definition_id
      == custom_object_definition_id
    )

  field_count = (await db.execute(field_query)).scalar_one()
  relationship_count = (await db.execute(relationship_query)).scalar_one()

  return field_count + relationship_count


@router.get(
  "/definitions/{definition_id}",
  response_model=CustomFieldDefinitionRead,
)
async def get_custom_field_definition(
  definition_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  result = await db.execute(
    select(CustomFieldDefinition)
    .where(
      CustomFieldDefinition.id == definition_id,
    )
  )

  definition = result.scalar_one_or_none()

  if not definition:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Custom field definition not found",
    )

  if current_user.role != "admin":
    require_company_manager(
      current_user,
      definition.company_id,
    )

  return definition


@router.patch(
  "/definitions/{definition_id}",
  response_model=CustomFieldDefinitionRead,
)
async def update_custom_field_definition(
  definition_id: UUID,
  payload: CustomFieldDefinitionUpdate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  definition = await db.get(
    CustomFieldDefinition,
    definition_id,
  )

  if not definition:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Custom field definition not found",
    )

  if current_user.role != "admin":
    require_company_manager(
      current_user,
      definition.company_id,
    )

  updates = payload.model_dump(
    exclude_unset=True,
  )

  for field, value in updates.items():
    setattr(definition, field, value)

  await db.commit()
  await db.refresh(definition)

  return definition


@router.delete(
  "/definitions/{definition_id}",
  status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_custom_field_definition(
  definition_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  definition = await db.get(
    CustomFieldDefinition,
    definition_id,
  )

  if not definition:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Custom field definition not found",
    )

  if current_user.role != "admin":
    require_company_manager(
      current_user,
      definition.company_id,
    )

  await db.delete(definition)
  await db.commit()

  return None


# ---------------------------------------------------------------------------
# Generic field
# ---------------------------------------------------------------------------

@router.get(
  "/{field_id}",
  response_model=CustomFieldRead,
)
async def get_custom_field(
  field_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  result = await db.execute(
    select(CustomField)
    .where(
      CustomField.id == field_id,
    )
    .options(
      selectinload(CustomField.definition),
    )
  )

  field = result.scalar_one_or_none()

  if not field:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Custom field not found",
    )

  if current_user.role != "admin":
    require_company_manager(
      current_user,
      field.company_id,
    )

  return field


@router.patch(
  "/{field_id}",
  response_model=CustomFieldRead,
)
async def update_custom_field(
  field_id: UUID,
  payload: CustomFieldUpdate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  field = await db.get(
    CustomField,
    field_id,
  )

  if not field:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Custom field not found",
    )

  if current_user.role != "admin":
    require_company_manager(
      current_user,
      field.company_id,
    )

  field.value = payload.value

  await db.commit()

  result = await db.execute(
    select(CustomField)
    .where(
      CustomField.id == field.id,
    )
    .options(
      selectinload(CustomField.definition),
    )
  )

  return result.scalar_one()


@router.delete(
  "/{field_id}",
  status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_custom_field(
  field_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  field = await db.get(
    CustomField,
    field_id,
  )

  if not field:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Custom field not found",
    )

  if current_user.role != "admin":
    require_company_manager(
      current_user,
      field.company_id,
    )

  await db.delete(field)
  await db.commit()

  return None


# ---------------------------------------------------------------------------
# Company fields
# ---------------------------------------------------------------------------

@router.get(
  "/company/{company_id}",
  response_model=List[CustomFieldRead],
)
async def list_company_custom_fields(
  company_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role != "admin":
    if current_user.company_id != company_id:
      raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Not authorized to access this company",
      )

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

  return result.scalars().all()


@router.post(
  "/company/{company_id}",
  response_model=CustomFieldRead,
  status_code=status.HTTP_201_CREATED,
)
async def create_company_custom_field(
  company_id: UUID,
  payload: CustomFieldCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  require_company_manager(
    current_user,
    company_id,
  )

  return await _create_field(
    db=db,
    company_id=company_id,
    definition_id=payload.custom_field_definition_id,
    value=payload.value,
    entity_type="company",
    entity_id=company_id,
  )


# ---------------------------------------------------------------------------
# Project fields
# ---------------------------------------------------------------------------

@router.get(
  "/project/{project_id}",
  response_model=List[CustomFieldRead],
)
async def list_project_custom_fields(
  project_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  project = await db.get(
    Project,
    project_id,
  )

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

  return result.scalars().all()


@router.post(
  "/project/{project_id}",
  response_model=CustomFieldRead,
  status_code=status.HTTP_201_CREATED,
)
async def create_project_custom_field(
  project_id: UUID,
  payload: CustomFieldCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  project = await db.get(
    Project,
    project_id,
  )

  if not project:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Project not found",
    )

  require_company_manager(
    current_user,
    project.company_id,
  )

  return await _create_field(
    db=db,
    company_id=project.company_id,
    definition_id=payload.custom_field_definition_id,
    value=payload.value,
    entity_type="project",
    entity_id=project_id,
  )


# ---------------------------------------------------------------------------
# User fields
# ---------------------------------------------------------------------------

@router.get(
  "/user/{user_id}",
  response_model=List[CustomFieldRead],
)
async def list_user_custom_fields(
  user_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  user = await db.get(
    User,
    user_id,
  )

  if not user:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="User not found",
    )

  if current_user.role != "admin":
    if current_user.company_id != user.company_id:
      raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Not authorized to access this user",
      )

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

  return result.scalars().all()


@router.post(
  "/user/{user_id}",
  response_model=CustomFieldRead,
  status_code=status.HTTP_201_CREATED,
)
async def create_user_custom_field(
  user_id: UUID,
  payload: CustomFieldCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  user = await db.get(
    User,
    user_id,
  )

  if not user:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="User not found",
    )

  if not user.company_id:
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail="User does not belong to a company",
    )

  require_company_manager(
    current_user,
    user.company_id,
  )

  return await _create_field(
    db=db,
    company_id=user.company_id,
    definition_id=payload.custom_field_definition_id,
    value=payload.value,
    entity_type="user",
    entity_id=user_id,
  )


# ---------------------------------------------------------------------------
# Custom object fields
# ---------------------------------------------------------------------------

@router.get(
  "/custom-object/{custom_object_id}",
  response_model=List[CustomFieldRead],
)
async def list_custom_object_fields(
  custom_object_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  custom_object = await db.get(
    CustomObject,
    custom_object_id,
  )

  if not custom_object:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Custom object not found",
    )

  if current_user.role != "admin":
    require_company_manager(
      current_user,
      custom_object.company_id,
    )

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

  return result.scalars().all()


@router.post(
  "/custom-object/{custom_object_id}",
  response_model=CustomFieldRead,
  status_code=status.HTTP_201_CREATED,
)
async def create_custom_object_field(
  custom_object_id: UUID,
  payload: CustomFieldCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  custom_object = await db.get(
    CustomObject,
    custom_object_id,
  )

  if not custom_object:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Custom object not found",
    )

  require_company_manager(
    current_user,
    custom_object.company_id,
  )

  return await _create_field(
    db=db,
    company_id=custom_object.company_id,
    definition_id=payload.custom_field_definition_id,
    value=payload.value,
    entity_type="custom_object",
    entity_id=custom_object_id,
  )


# ---------------------------------------------------------------------------
# Internal creation helper
# ---------------------------------------------------------------------------

async def _create_field(
  db: AsyncSession,
  company_id: UUID,
  definition_id: UUID,
  value: str | None,
  entity_type: str,
  entity_id: UUID,
) -> CustomField:
  definition = await db.get(
    CustomFieldDefinition,
    definition_id,
  )

  if not definition:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Custom field definition not found",
    )

  if definition.company_id != company_id:
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail="Custom field definition belongs to a different company",
    )

  if definition.entity_type != entity_type:
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail=(
        f"This field definition is for '{definition.entity_type}' "
        f"and cannot be used for '{entity_type}'"
      ),
    )

  if entity_type == "custom_object":
    custom_object = await db.get(
      CustomObject,
      entity_id,
    )

    if not custom_object:
      raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Custom object not found",
      )

    if custom_object.company_id != company_id:
      raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Custom object belongs to a different company",
      )

    if custom_object.custom_object_definition_id != definition.custom_object_definition_id:
      raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
          "This field definition belongs to a different "
          "custom object type"
        ),
      )

  field = CustomField(
    company_id=company_id,
    custom_field_definition_id=definition_id,
    value=value,
  )

  db.add(field)
  await db.flush()

  if entity_type == "company":
    link = CustomFieldCompanyLink(
      custom_field_id=field.id,
      company_id=entity_id,
    )

  elif entity_type == "project":
    link = CustomFieldProjectLink(
      custom_field_id=field.id,
      project_id=entity_id,
    )

  elif entity_type == "user":
    link = CustomFieldUserLink(
      custom_field_id=field.id,
      user_id=entity_id,
    )

  elif entity_type == "custom_object":
    link = CustomFieldCustomObjectLink(
      custom_field_id=field.id,
      custom_object_id=entity_id,
    )

  else:
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail="Invalid custom field entity type",
    )

  db.add(link)
  await db.commit()

  result = await db.execute(
    select(CustomField)
    .where(
      CustomField.id == field.id,
    )
    .options(
      selectinload(CustomField.definition),
    )
  )

  return result.scalar_one()