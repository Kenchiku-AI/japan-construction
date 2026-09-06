from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.dependencies import (
  get_current_user,
  require_company_manager,
)
from app.db.session import get_db
from app.db.models import (
  Company,
  User,
  CustomField,
  CustomObject,
  CustomObjectDefinition,
  CustomFieldDefinition,
  CustomFieldEntityType,
  CustomFieldCustomObjectLink,
  CustomRelationship,
  CustomRelationshipDefinition,
  CustomRelationshipEntityType,
  CustomRelationshipCardinality,
  CustomFieldDataType,
)
from app.schemas.custom_object import (
  CustomObjectDefinitionCreate,
  CustomObjectDefinitionUpdate,
  CustomObjectDefinitionRead,
  CustomObjectDefinitionDetailRead,
  CustomObjectCreate,
  CustomObjectUpdate,
  CustomObjectRead,
  CustomObjectsByDefinitionsRequest,
  CustomObjectListItemRead,
  CustomObjectsByDefinitionRead,
)
from app.schemas.custom_field import CustomFieldDefinitionRead, CustomFieldRead
from app.schemas.custom_relationship import CustomRelationshipDefinitionRead, CustomRelationshipRead


router = APIRouter(
  prefix="/custom-objects",
  tags=["Custom Objects"],
)


# ---------------------------------------------------------------------------
# Custom object definitions
# ---------------------------------------------------------------------------

@router.get(
  "/definitions",
  response_model=List[CustomObjectDefinitionRead],
)
async def list_custom_object_definitions(
  company_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  require_company_manager(
    current_user,
    company_id,
  )

  result = await db.execute(
    select(CustomObjectDefinition)
    .where(
      CustomObjectDefinition.company_id == company_id,
    )
    .order_by(CustomObjectDefinition.name)
  )

  return result.scalars().all()


@router.post(
  "/definitions",
  response_model=CustomObjectDefinitionRead,
  status_code=status.HTTP_201_CREATED,
)
async def create_custom_object_definition(
  payload: CustomObjectDefinitionCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  require_company_manager(
    current_user,
    payload.company_id,
  )

  definition = CustomObjectDefinition(
    company_id=payload.company_id,
    name=payload.name,
    description=payload.description,
  )

  db.add(definition)
  await db.commit()
  await db.refresh(definition)

  return definition


@router.get(
  "/definitions/{definition_id}",
  response_model=CustomObjectDefinitionDetailRead,
)
async def get_custom_object_definition(
  definition_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  result = await db.execute(
    select(CustomObjectDefinition)
    .where(
      CustomObjectDefinition.id == definition_id,
    )
    .options(
      selectinload(
        CustomObjectDefinition.custom_field_definitions,
      ),
      selectinload(
        CustomObjectDefinition.custom_relationship_definitions_as_source,
      ),
    )
  )

  definition = result.scalar_one_or_none()

  if not definition:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Custom object definition not found",
    )

  if current_user.role != "admin":
    require_company_manager(
      current_user,
      definition.company_id,
    )

  relationships = sorted(
    definition.custom_relationship_definitions_as_source,
    key=lambda relationship: relationship.sort_order,
  )

  return CustomObjectDefinitionDetailRead(
    id=definition.id,
    company_id=definition.company_id,
    name=definition.name,
    description=definition.description,
    fields=definition.custom_field_definitions,
    relationships=relationships,
    created_at=definition.created_at,
    updated_at=definition.updated_at,
  )

@router.patch(
  "/definitions/{definition_id}",
  response_model=CustomObjectDefinitionDetailRead,
)
async def update_custom_object_definition(
  definition_id: UUID,
  payload: CustomObjectDefinitionUpdate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  result = await db.execute(
    select(CustomObjectDefinition)
    .where(
      CustomObjectDefinition.id == definition_id,
    )
    .options(
      selectinload(
        CustomObjectDefinition.custom_field_definitions,
      ),
      selectinload(
        CustomObjectDefinition.custom_relationship_definitions_as_source,
      ),
    )
  )

  definition = result.scalar_one_or_none()

  if not definition:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Custom object definition not found",
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

  relationships = sorted(
    definition.custom_relationship_definitions_as_source,
    key=lambda relationship: relationship.sort_order,
  )

  return CustomObjectDefinitionDetailRead(
    id=definition.id,
    company_id=definition.company_id,
    name=definition.name,
    description=definition.description,
    fields=definition.custom_field_definitions,
    relationships=relationships,
    created_at=definition.created_at,
    updated_at=definition.updated_at,
  )


@router.delete(
  "/definitions/{definition_id}",
  status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_custom_object_definition(
  definition_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  definition = await db.get(
    CustomObjectDefinition,
    definition_id,
  )

  if not definition:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Custom object definition not found",
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
# Custom objects
# ---------------------------------------------------------------------------

async def build_custom_object_response(
  db: AsyncSession,
  custom_object: CustomObject,
) -> CustomObjectRead:

  field_definitions_result = await db.execute(
    select(CustomFieldDefinition)
    .where(
      CustomFieldDefinition.custom_object_definition_id
      == custom_object.custom_object_definition_id,
      CustomFieldDefinition.entity_type
      == CustomFieldEntityType.custom_object,
    )
    .order_by(
      CustomFieldDefinition.sort_order,
      CustomFieldDefinition.name,
    )
  )

  field_definitions = field_definitions_result.scalars().all()

  existing_fields = {
    link.custom_field.custom_field_definition_id: link.custom_field
    for link in custom_object.custom_field_links
  }

  fields = [
    CustomFieldRead(
      id=existing_fields[definition.id].id
        if definition.id in existing_fields
        else None,
      value=existing_fields[definition.id].value
        if definition.id in existing_fields
        else None,
      definition=definition,
    )
    for definition in field_definitions
  ]

  relationship_definitions_result = await db.execute(
    select(CustomRelationshipDefinition)
    .where(
      CustomRelationshipDefinition.company_id
      == custom_object.company_id,
      CustomRelationshipDefinition.source_entity_type
      == CustomRelationshipEntityType.custom_object,
      CustomRelationshipDefinition.source_custom_object_definition_id
      == custom_object.custom_object_definition_id,
    )
    .order_by(
      CustomRelationshipDefinition.sort_order,
      CustomRelationshipDefinition.name,
    )
  )

  relationship_definitions = (
    relationship_definitions_result.scalars().all()
  )

  existing_relationships = {}

  for relationship in custom_object.custom_relationships:
    if relationship.custom_relationship_definition_id not in existing_relationships:
      existing_relationships[
        relationship.custom_relationship_definition_id
      ] = []

    existing_relationships[
      relationship.custom_relationship_definition_id
    ].append(relationship)

  relationships = []

  for definition in relationship_definitions:

    definition_relationships = existing_relationships.get(
      definition.id,
      [],
    )

    for relationship in definition_relationships:
      relationships.append(
        CustomRelationshipRead(
          id=relationship.id,
          source_entity_id=custom_object.id,
          target_entity_id=relationship.target_entity_id,
          definition=definition,
        )
      )

  return CustomObjectRead(
    id=custom_object.id,
    company_id=custom_object.company_id,
    definition=custom_object.definition,
    fields=fields,
    relationships=relationships,
    created_at=custom_object.created_at,
    updated_at=custom_object.updated_at,
  )


@router.get(
  "",
  response_model=List[CustomObjectRead],
)
async def list_custom_objects(
  custom_object_definition_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  definition = await db.get(
    CustomObjectDefinition,
    custom_object_definition_id,
  )

  if not definition:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Custom object definition not found",
    )

  if current_user.role != "admin":
    require_company_manager(
      current_user,
      definition.company_id,
    )

  result = await db.execute(
    select(CustomObject)
    .where(
      CustomObject.custom_object_definition_id
      == custom_object_definition_id,
    )
    .options(
      selectinload(CustomObject.definition),
      selectinload(CustomObject.custom_field_links)
        .selectinload(CustomFieldCustomObjectLink.custom_field),
      selectinload(CustomObject.custom_relationships),
    )
    .order_by(CustomObject.created_at.desc())
  )

  custom_objects = result.scalars().all()

  return [
    await build_custom_object_response(
      db,
      custom_object,
    )
    for custom_object in custom_objects
  ]


@router.post(
  "/by-definitions",
  response_model=dict[UUID, CustomObjectsByDefinitionRead],
)
async def list_custom_objects_by_definitions(
  payload: CustomObjectsByDefinitionsRequest,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  require_company_manager(
    current_user,
    payload.company_id,
  )

  # ---------------------------------------------------------
  # Get the requested definitions
  # ---------------------------------------------------------

  definitions_result = await db.execute(
    select(CustomObjectDefinition)
    .where(
      CustomObjectDefinition.company_id == payload.company_id,
      CustomObjectDefinition.id.in_(payload.definition_ids),
    )
  )

  definitions = definitions_result.scalars().all()

  definitions_by_id = {
    definition.id: definition
    for definition in definitions
  }

  invalid_definition_ids = (
    set(payload.definition_ids) - set(definitions_by_id.keys())
  )

  if invalid_definition_ids:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="One or more custom object definitions not found",
    )

  # ---------------------------------------------------------
  # Get all requested custom objects
  # ---------------------------------------------------------

  result = await db.execute(
    select(CustomObject)
    .where(
      CustomObject.company_id == payload.company_id,
      CustomObject.custom_object_definition_id.in_(
        payload.definition_ids
      ),
    )
    .options(
      selectinload(CustomObject.custom_field_links)
        .selectinload(CustomFieldCustomObjectLink.custom_field)
        .selectinload(CustomField.definition),

      selectinload(CustomObject.custom_relationships)
        .selectinload(CustomRelationship.definition),
    )
    .order_by(
      CustomObject.custom_object_definition_id,
      CustomObject.id,
    )
  )

  custom_objects = result.scalars().unique().all()

  # ---------------------------------------------------------
  # Index requested custom objects by ID
  # ---------------------------------------------------------

  custom_objects_by_id = {
    custom_object.id: custom_object
    for custom_object in custom_objects
  }

  # ---------------------------------------------------------
  # Get all project IDs referenced by relationships
  #
  # We need both target and source entities because:
  #
  # name     -> target entity
  # subtitle -> source entity
  # ---------------------------------------------------------

  project_ids = set()

  user_ids = set()

  for custom_object in custom_objects:
    for relationship in custom_object.custom_relationships:
      relationship_definition = relationship.definition

      if (
        relationship_definition.target_entity_type
        == CustomRelationshipEntityType.project
      ):
        project_ids.add(
          relationship.target_entity_id
        )

      if (
        relationship_definition.target_entity_type
        == CustomRelationshipEntityType.user
      ):
        user_ids.add(
          relationship.target_entity_id
        )

      if (
        relationship_definition.source_entity_type
        == CustomRelationshipEntityType.project
      ):
        project_ids.add(
          relationship.source_entity_id
        )

      if (
        relationship_definition.source_entity_type
        == CustomRelationshipEntityType.user
      ):
        user_ids.add(
          relationship.source_entity_id
        )

  # ---------------------------------------------------------
  # Load projects
  # ---------------------------------------------------------

  projects_by_id = {}

  if project_ids:
    projects_result = await db.execute(
      select(Project)
      .where(
        Project.company_id == payload.company_id,
        Project.id.in_(project_ids),
      )
    )

    projects_by_id = {
      project.id: project
      for project in projects_result.scalars().all()
    }

  # ---------------------------------------------------------
  # Load users
  # ---------------------------------------------------------

  users_by_id = {}

  if user_ids:
    users_result = await db.execute(
      select(User)
      .where(
        User.company_id == payload.company_id,
        User.id.in_(user_ids),
      )
    )

    users_by_id = {
      user.id: user
      for user in users_result.scalars().all()
    }

  # ---------------------------------------------------------
  # Resolve a CustomObject's label
  #
  # Returns:
  #
  #   (label, came_from_relationship)
  #
  # The boolean lets us determine whether to populate
  # subtitle.
  # ---------------------------------------------------------

  def get_label_value(
    custom_object: CustomObject,
    visited: set[UUID] | None = None,
  ) -> tuple[str, bool]:

    if visited is None:
      visited = set()

    # Prevent circular CustomObject relationships.
    if custom_object.id in visited:
      return "", False

    visited = visited | {custom_object.id}

    # -------------------------------------------------------
    # Find the first non-Boolean field
    # -------------------------------------------------------

    label_fields = sorted(
      [
        link.custom_field
        for link in custom_object.custom_field_links
        if (
          link.custom_field.definition.data_type
          != CustomFieldDataType.boolean.value
        )
      ],
      key=lambda field: field.definition.sort_order,
    )

    field_index = (
      label_fields[0].definition.sort_order
      if label_fields
      else 99999
    )

    # -------------------------------------------------------
    # Find the first relationship
    # -------------------------------------------------------

    label_relationships = sorted(
      custom_object.custom_relationships,
      key=lambda relationship: relationship.definition.sort_order,
    )

    relationship_index = (
      label_relationships[0].definition.sort_order
      if label_relationships
      else 99999
    )

    # -------------------------------------------------------
    # Field wins
    #
    # This exactly matches:
    #
    # if (fieldIndex < relationshipIndex)
    # -------------------------------------------------------

    if field_index < relationship_index:
      return (
        label_fields[0].value or "",
        False,
      )

    # -------------------------------------------------------
    # No relationship
    # -------------------------------------------------------

    if not label_relationships:
      return "", False

    relationship = label_relationships[0]

    # -------------------------------------------------------
    # Resolve the TARGET entity for the name
    # -------------------------------------------------------

    entity_type = relationship.definition.target_entity_type

    if entity_type == CustomRelationshipEntityType.project:
      project = projects_by_id.get(
        relationship.target_entity_id
      )

      return (
        project.name if project else "",
        True,
      )

    if entity_type == CustomRelationshipEntityType.user:
      user = users_by_id.get(
        relationship.target_entity_id
      )

      if not user:
        return "", True

      return (
        f"{user.last_name} {user.first_name}",
        True,
      )

    if entity_type == CustomRelationshipEntityType.custom_object:
      related_custom_object = custom_objects_by_id.get(
        relationship.target_entity_id
      )

      if not related_custom_object:
        return "", True

      label, _ = get_label_value(
        related_custom_object,
        visited,
      )

      return label, True

    return "", True

  # ---------------------------------------------------------
  # Resolve an arbitrary relationship endpoint
  #
  # Used for the subtitle, which represents the SOURCE
  # entity of the relationship.
  # ---------------------------------------------------------

  def get_relationship_entity_label(
    relationship: CustomRelationship,
    *,
    source: bool,
    visited: set[UUID] | None = None,
  ) -> str:

    if visited is None:
      visited = set()

    if source:
      entity_type = relationship.definition.source_entity_type
      entity_id = relationship.source_entity_id
    else:
      entity_type = relationship.definition.target_entity_type
      entity_id = relationship.target_entity_id

    # -------------------------------------------------------
    # Project
    # -------------------------------------------------------

    if entity_type == CustomRelationshipEntityType.project:
      project = projects_by_id.get(entity_id)

      return project.name if project else ""

    # -------------------------------------------------------
    # User
    # -------------------------------------------------------

    if entity_type == CustomRelationshipEntityType.user:
      user = users_by_id.get(entity_id)

      if not user:
        return ""

      return f"{user.last_name} {user.first_name}"

    # -------------------------------------------------------
    # Custom Object
    # -------------------------------------------------------

    if entity_type == CustomRelationshipEntityType.custom_object:
      custom_object = custom_objects_by_id.get(entity_id)

      if not custom_object:
        return ""

      label, _ = get_label_value(
        custom_object,
        visited,
      )

      return label

    return ""

  # ---------------------------------------------------------
  # Build response
  # ---------------------------------------------------------

  objects_by_definition = {
    definition_id: CustomObjectsByDefinitionRead(
      name=definitions_by_id[definition_id].name,
      objects=[],
    )
    for definition_id in payload.definition_ids
  }

  for custom_object in custom_objects:
    object_name, name_from_relationship = get_label_value(
      custom_object
    )

    subtitle = None

    if name_from_relationship:
      label_relationships = sorted(
        custom_object.custom_relationships,
        key=lambda relationship: relationship.definition.sort_order,
      )

      if label_relationships:
        subtitle = get_relationship_entity_label(
          label_relationships[0],
          source=True,
        )

    objects_by_definition[
      custom_object.custom_object_definition_id
    ].objects.append(
      CustomObjectListItemRead(
        id=custom_object.id,
        name=object_name,
        subtitle=subtitle,
      )
    )

  return objects_by_definition


@router.get(
  "/{custom_object_id}",
  response_model=CustomObjectRead,
)
async def get_custom_object(
  custom_object_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  result = await db.execute(
    select(CustomObject)
    .where(
      CustomObject.id == custom_object_id,
    )
    .options(
      selectinload(CustomObject.definition),
      selectinload(CustomObject.custom_field_links)
        .selectinload(CustomFieldCustomObjectLink.custom_field),
      selectinload(CustomObject.custom_relationships),
    )
  )

  custom_object = result.scalar_one_or_none()

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

  return await build_custom_object_response(
    db,
    custom_object,
  )

@router.post(
  "",
  response_model=CustomObjectRead,
  status_code=status.HTTP_201_CREATED,
)
async def create_custom_object(
  payload: CustomObjectCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  require_company_manager(
    current_user,
    payload.company_id,
  )

  definition_result = await db.execute(
    select(CustomObjectDefinition)
    .where(
      CustomObjectDefinition.id
      == payload.custom_object_definition_id,
      CustomObjectDefinition.company_id
      == payload.company_id,
    )
  )

  definition = definition_result.scalar_one_or_none()

  if not definition:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Custom object definition not found",
    )

  custom_object = CustomObject(
    company_id=payload.company_id,
    custom_object_definition_id=payload.custom_object_definition_id,
  )

  db.add(custom_object)

  await db.flush()

  # Get all field definitions for this custom object definition
  field_definitions_result = await db.execute(
    select(CustomFieldDefinition)
    .where(
      CustomFieldDefinition.company_id
      == payload.company_id,
      CustomFieldDefinition.entity_type
      == CustomFieldEntityType.custom_object,
      CustomFieldDefinition.custom_object_definition_id
      == payload.custom_object_definition_id,
    )
  )

  field_definitions = field_definitions_result.scalars().all()

  field_definitions_by_id = {
    field_definition.id: field_definition
    for field_definition in field_definitions
  }

  # Create the supplied field values
  for definition_id, value in payload.fields.items():
    if definition_id not in field_definitions_by_id:
      raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
          f"Field definition {definition_id} does not belong "
          "to this custom object definition"
        ),
      )

    custom_field = CustomField(
      company_id=payload.company_id,
      custom_field_definition_id=definition_id,
      value=value,
    )

    db.add(custom_field)

    await db.flush()

    db.add(
      CustomFieldCustomObjectLink(
        custom_object_id=custom_object.id,
        custom_field_id=custom_field.id,
      )
    )

  # Get relationship definitions where this custom object
  # definition is the source
  relationship_definitions_result = await db.execute(
    select(CustomRelationshipDefinition)
    .where(
      CustomRelationshipDefinition.company_id
      == payload.company_id,
      CustomRelationshipDefinition.source_entity_type
      == CustomRelationshipEntityType.custom_object,
      CustomRelationshipDefinition.source_custom_object_definition_id
      == payload.custom_object_definition_id,
    )
  )

  relationship_definitions = (
    relationship_definitions_result.scalars().all()
  )

  relationship_definitions_by_id = {
    relationship_definition.id: relationship_definition
    for relationship_definition in relationship_definitions
  }

  # Create the supplied relationship values
  for definition_id, target_entity_ids in payload.relationships.items():
    relationship_definition = relationship_definitions_by_id.get(
      definition_id
    )

    if not relationship_definition:
      raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=(
          f"Relationship definition {definition_id} does not belong "
          "to this custom object definition"
        ),
      )

    for target_entity_id in target_entity_ids:
      custom_relationship = CustomRelationship(
        company_id=payload.company_id,
        custom_relationship_definition_id=definition_id,
        source_entity_type=(
          CustomRelationshipEntityType.custom_object.value
        ),
        source_entity_id=custom_object.id,
        source_custom_object_definition_id=(
          payload.custom_object_definition_id
        ),
        target_entity_type=(
          relationship_definition.target_entity_type.value
        ),
        target_entity_id=target_entity_id,
        target_custom_object_definition_id=(
          relationship_definition.target_custom_object_definition_id
        ),
      )

      db.add(custom_relationship)
  await db.commit()

  result = await db.execute(
    select(CustomObject)
    .where(
      CustomObject.id == custom_object.id,
    )
    .options(
      selectinload(CustomObject.definition),
      selectinload(CustomObject.custom_field_links)
        .selectinload(CustomFieldCustomObjectLink.custom_field),
      selectinload(CustomObject.custom_relationships),
    )
  )

  custom_object = result.scalar_one()

  return await build_custom_object_response(
    db,
    custom_object,
  )


@router.patch(
  "/{custom_object_id}",
  response_model=CustomObjectRead,
)
async def update_custom_object(
  custom_object_id: UUID,
  payload: CustomObjectUpdate,
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

  updates = payload.model_dump(
    exclude_unset=True,
  )

  # Determine which definition the object will use after the update
  definition_id = updates.get(
    "custom_object_definition_id",
    custom_object.custom_object_definition_id,
  )

  definition = await db.get(
    CustomObjectDefinition,
    definition_id,
  )

  if not definition:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Custom object definition not found",
    )

  if definition.company_id != custom_object.company_id:
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail="Custom object definition belongs to a different company",
    )

  # Get all field definitions for this custom object definition
  field_definitions_result = await db.execute(
    select(CustomFieldDefinition)
    .where(
      CustomFieldDefinition.company_id
      == custom_object.company_id,
      CustomFieldDefinition.entity_type
      == CustomFieldEntityType.custom_object,
      CustomFieldDefinition.custom_object_definition_id
      == definition_id,
    )
  )

  field_definitions = field_definitions_result.scalars().all()

  field_definitions_by_id = {
    field_definition.id: field_definition
    for field_definition in field_definitions
  }

  # Update supplied fields
  if "fields" in updates:
    for field_definition_id, value in updates["fields"].items():
      if field_definition_id not in field_definitions_by_id:
        raise HTTPException(
          status_code=status.HTTP_400_BAD_REQUEST,
          detail=(
            f"Field definition {field_definition_id} does not belong "
            "to this custom object definition"
          ),
        )

      field_result = await db.execute(
        select(CustomField)
        .join(
          CustomFieldCustomObjectLink,
          CustomFieldCustomObjectLink.custom_field_id
          == CustomField.id,
        )
        .where(
          CustomFieldCustomObjectLink.custom_object_id
          == custom_object.id,
          CustomField.custom_field_definition_id
          == field_definition_id,
        )
      )

      custom_field = field_result.scalar_one_or_none()

      if custom_field:
        custom_field.value = value
      else:
        custom_field = CustomField(
          company_id=custom_object.company_id,
          custom_field_definition_id=field_definition_id,
          value=value,
        )

        db.add(custom_field)

        await db.flush()

        db.add(
          CustomFieldCustomObjectLink(
            custom_object_id=custom_object.id,
            custom_field_id=custom_field.id,
          )
        )

  # Get relationship definitions for this custom object definition
  relationship_definitions_result = await db.execute(
    select(CustomRelationshipDefinition)
    .where(
      CustomRelationshipDefinition.company_id
      == custom_object.company_id,
      CustomRelationshipDefinition.source_entity_type
      == CustomRelationshipEntityType.custom_object,
      CustomRelationshipDefinition.source_custom_object_definition_id
      == definition_id,
    )
  )

  relationship_definitions = (
    relationship_definitions_result.scalars().all()
  )

  relationship_definitions_by_id = {
    relationship_definition.id: relationship_definition
    for relationship_definition in relationship_definitions
  }

  # Update supplied relationships
  if "relationships" in updates:

    for relationship_definition_id, target_entity_ids in (
      updates["relationships"].items()
    ):

      relationship_definition = (
        relationship_definitions_by_id.get(
          relationship_definition_id
        )
      )

      if not relationship_definition:
        raise HTTPException(
          status_code=status.HTTP_400_BAD_REQUEST,
          detail=(
            f"Relationship definition "
            f"{relationship_definition_id} does not belong "
            "to this custom object definition"
          ),
        )

      if (
        relationship_definition.cardinality
        == CustomRelationshipCardinality.one
        and len(target_entity_ids) > 1
      ):
        raise HTTPException(
          status_code=status.HTTP_400_BAD_REQUEST,
          detail=(
            f"Relationship definition "
            f"{relationship_definition_id} only allows one target"
          ),
        )

      # Remove existing relationships for this definition
      await db.execute(
        delete(CustomRelationship)
        .where(
          CustomRelationship.custom_relationship_definition_id
          == relationship_definition_id,
          CustomRelationship.source_entity_id
          == custom_object.id,
        )
      )

      # Create new relationships
      for target_entity_id in target_entity_ids:

        custom_relationship = CustomRelationship(
          company_id=custom_object.company_id,
          custom_relationship_definition_id=(
            relationship_definition_id
          ),
          source_entity_type=(
            CustomRelationshipEntityType.custom_object.value
          ),
          source_entity_id=custom_object.id,
          source_custom_object_definition_id=definition_id,
          target_entity_type=(
            relationship_definition.target_entity_type.value
          ),
          target_entity_id=target_entity_id,
          target_custom_object_definition_id=(
            relationship_definition.target_custom_object_definition_id
          ),
        )

        db.add(custom_relationship)

  # Update normal CustomObject columns
  if "custom_object_definition_id" in updates:
    custom_object.custom_object_definition_id = definition_id

  await db.commit()

  result = await db.execute(
    select(CustomObject)
    .where(
      CustomObject.id == custom_object.id,
    )
    .options(
      selectinload(CustomObject.definition),
      selectinload(CustomObject.custom_field_links)
        .selectinload(
          CustomFieldCustomObjectLink.custom_field
        ),
      selectinload(CustomObject.custom_relationships),
    )
  )

  custom_object = result.scalar_one()

  return await build_custom_object_response(
    db,
    custom_object,
  )

@router.delete(
  "/{custom_object_id}",
  status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_custom_object(
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

  await db.delete(custom_object)
  await db.commit()

  return None