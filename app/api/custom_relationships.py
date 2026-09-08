from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, or_, and_, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import (
  get_current_user,
  require_company_manager,
)
from app.db.session import get_db
from app.db.models import (
  Company,
  Project,
  User,
  CustomObject,
  CustomObjectDefinition,
  CustomFieldDefinition,
  CustomRelationship,
  CustomRelationshipDefinition,
  CustomRelationshipEntityType,
  CustomRelationshipCardinality,
)
from app.schemas.custom_relationship import (
  CustomRelationshipDefinitionCreate,
  CustomRelationshipDefinitionUpdate,
  CustomRelationshipDefinitionRead,
  CustomRelationshipDefinitionSortOrderUpdate,
  CustomRelationshipCreate,
  CustomRelationshipUpdate,
  CustomRelationshipRead,
)


router = APIRouter(
  prefix="/custom-relationships",
  tags=["Custom Relationships"],
)


# ---------------------------------------------------------------------------
# Relationship definitions
# ---------------------------------------------------------------------------

@router.get(
  "/definitions",
  response_model=List[CustomRelationshipDefinitionRead],
)
async def list_custom_relationship_definitions(
  company_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  require_company_manager(
    current_user,
    company_id,
  )

  result = await db.execute(
    select(CustomRelationshipDefinition)
    .where(
      CustomRelationshipDefinition.company_id == company_id,
    )
    .order_by(CustomRelationshipDefinition.sort_order)
  )

  return result.scalars().all()


@router.post(
  "/definitions",
  response_model=CustomRelationshipDefinitionRead,
  status_code=status.HTTP_201_CREATED,
)
async def create_custom_relationship_definition(
  payload: CustomRelationshipDefinitionCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  require_company_manager(
    current_user,
    payload.company_id,
  )

  await _validate_relationship_definition_entities(
    db=db,
    company_id=payload.company_id,
    source_entity_type=payload.source_entity_type,
    source_custom_object_definition_id=(
      payload.source_custom_object_definition_id
    ),
    target_entity_type=payload.target_entity_type,
    target_custom_object_definition_id=(
      payload.target_custom_object_definition_id
    ),
  )

  sort_order = await _get_next_relationship_sort_order(
    db=db,
    company_id=payload.company_id,
    source_entity_type=payload.source_entity_type,
    source_custom_object_definition_id=(
      payload.source_custom_object_definition_id
    ),
  )

  definition = CustomRelationshipDefinition(
    company_id=payload.company_id,
    name=payload.name,
    description=payload.description,
    source_entity_type=payload.source_entity_type,
    source_custom_object_definition_id=(
      payload.source_custom_object_definition_id
    ),
    target_entity_type=payload.target_entity_type,
    target_custom_object_definition_id=(
      payload.target_custom_object_definition_id
    ),
    cardinality=payload.cardinality,
    sort_order=sort_order,
  )

  db.add(definition)
  await db.commit()
  await db.refresh(definition)

  return definition


async def _get_next_relationship_sort_order(
  db: AsyncSession,
  company_id: UUID,
  source_entity_type: CustomRelationshipEntityType,
  source_custom_object_definition_id: UUID | None = None,
) -> int:
  field_query = select(
    func.count(CustomFieldDefinition.id)
  ).where(
    CustomFieldDefinition.company_id == company_id,
    CustomFieldDefinition.entity_type == source_entity_type.value,
  )

  relationship_query = select(
    func.count(CustomRelationshipDefinition.id)
  ).where(
    CustomRelationshipDefinition.company_id == company_id,
    CustomRelationshipDefinition.source_entity_type == source_entity_type,
  )

  if source_entity_type == CustomRelationshipEntityType.custom_object:
    field_query = field_query.where(
      CustomFieldDefinition.custom_object_definition_id
      == source_custom_object_definition_id
    )

    relationship_query = relationship_query.where(
      CustomRelationshipDefinition.source_custom_object_definition_id
      == source_custom_object_definition_id
    )

  field_count = (await db.execute(field_query)).scalar_one()
  relationship_count = (await db.execute(relationship_query)).scalar_one()

  return field_count + relationship_count


@router.get(
  "/definitions/{definition_id}",
  response_model=CustomRelationshipDefinitionRead,
)
async def get_custom_relationship_definition(
  definition_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  definition = await db.get(
    CustomRelationshipDefinition,
    definition_id,
  )

  if not definition:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Custom relationship definition not found",
    )

  if current_user.role != "admin":
    require_company_manager(
      current_user,
      definition.company_id,
    )

  return definition


@router.patch(
  "/definitions/sort-order",
  response_model=List[CustomRelationshipDefinitionRead],
)
async def update_custom_relationship_definition_sort_order(
  payload: List[CustomRelationshipDefinitionSortOrderUpdate],
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  definition_ids = [
    definition.id
    for definition in payload
  ]

  if len(definition_ids) != len(set(definition_ids)):
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail="Duplicate custom relationship definition IDs are not allowed",
    )

  if not definition_ids:
    return []

  result = await db.execute(
    select(CustomRelationshipDefinition)
    .where(
      CustomRelationshipDefinition.id.in_(definition_ids),
    )
  )

  definitions = result.scalars().all()

  if len(definitions) != len(definition_ids):
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail="One or more custom relationship definitions were not found",
    )

  company_ids = {
    definition.company_id
    for definition in definitions
  }

  if len(company_ids) != 1:
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail="All custom relationship definitions must belong to the same company",
    )

  company_id = next(iter(company_ids))

  require_company_manager(
    current_user,
    company_id,
  )

  definitions_by_id = {
    definition.id: definition
    for definition in definitions
  }

  for definition_update in payload:
    definition = definitions_by_id[definition_update.id]
    definition.sort_order = definition_update.sort_order

  await db.commit()

  for definition in definitions:
    await db.refresh(definition)

  return sorted(
    definitions,
    key=lambda definition: definition.sort_order,
  )


@router.patch(
  "/definitions/{definition_id}",
  response_model=CustomRelationshipDefinitionRead,
)
async def update_custom_relationship_definition(
  definition_id: UUID,
  payload: CustomRelationshipDefinitionUpdate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  definition = await db.get(
    CustomRelationshipDefinition,
    definition_id,
  )

  if not definition:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Custom relationship definition not found",
    )

  if current_user.role != "admin":
    require_company_manager(
      current_user,
      definition.company_id,
    )

  updates = payload.model_dump(
    exclude_unset=True,
  )

  new_source_entity_type = updates.get(
    "source_entity_type",
    definition.source_entity_type,
  )

  new_source_custom_object_definition_id = updates.get(
    "source_custom_object_definition_id",
    definition.source_custom_object_definition_id,
  )

  new_target_entity_type = updates.get(
    "target_entity_type",
    definition.target_entity_type,
  )

  new_target_custom_object_definition_id = updates.get(
    "target_custom_object_definition_id",
    definition.target_custom_object_definition_id,
  )

  await _validate_relationship_definition_entities(
    db=db,
    company_id=definition.company_id,
    source_entity_type=new_source_entity_type,
    source_custom_object_definition_id=(
      new_source_custom_object_definition_id
    ),
    target_entity_type=new_target_entity_type,
    target_custom_object_definition_id=(
      new_target_custom_object_definition_id
    ),
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
async def delete_custom_relationship_definition(
  definition_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  definition = await db.get(
    CustomRelationshipDefinition,
    definition_id,
  )

  if not definition:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Custom relationship definition not found",
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
# Relationships
# ---------------------------------------------------------------------------

@router.post(
  "",
  response_model=CustomRelationshipRead,
  status_code=status.HTTP_201_CREATED,
)
async def create_custom_relationship(
  payload: CustomRelationshipCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  definition = await db.get(
    CustomRelationshipDefinition,
    payload.custom_relationship_definition_id,
  )

  if not definition:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Custom relationship definition not found",
    )

  if current_user.role != "admin":
    require_company_manager(
      current_user,
      definition.company_id,
    )

  await _validate_relationship_entities(
    db=db,
    definition=definition,
    source_entity_id=payload.source_entity_id,
    target_entity_id=payload.target_entity_id,
  )

  await _validate_duplicate_relationship(
    db=db,
    definition=definition,
    source_entity_id=payload.source_entity_id,
    target_entity_id=payload.target_entity_id,
  )

  await _validate_cardinality(
    db=db,
    definition=definition,
    source_entity_id=payload.source_entity_id,
    target_entity_id=payload.target_entity_id,
  )

  relationship = CustomRelationship(
    company_id=definition.company_id,
    custom_relationship_definition_id=definition.id,
    source_entity_type=definition.source_entity_type,
    source_entity_id=payload.source_entity_id,
    target_entity_type=definition.target_entity_type,
    target_entity_id=payload.target_entity_id,
  )

  db.add(relationship)
  await db.commit()
  await db.refresh(relationship)

  return relationship


@router.get(
  "/{relationship_id}",
  response_model=CustomRelationshipRead,
)
async def get_custom_relationship(
  relationship_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  relationship = await db.get(
    CustomRelationship,
    relationship_id,
  )

  if not relationship:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Custom relationship not found",
    )

  if current_user.role != "admin":
    require_company_manager(
      current_user,
      relationship.company_id,
    )

  return relationship


```python
@router.patch(
  "",
  response_model=List[CustomRelationshipRead],
)
async def update_custom_relationships(
  custom_relationship_definition_id: UUID,
  payload: CustomRelationshipUpdate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  definition = await db.get(
    CustomRelationshipDefinition,
    custom_relationship_definition_id,
  )

  if not definition:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Custom relationship definition not found",
    )

  if current_user.role != "admin":
    require_company_manager(
      current_user,
      definition.company_id,
    )

  # A "many" relationship can have multiple targets.
  # A "one" relationship can have at most one.
  if (
    definition.cardinality == CustomRelationshipCardinality.one
    and len(payload.target_entity_ids) > 1
  ):
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail="This relationship only allows one target",
    )

  # Remove duplicate target IDs from the request.
  target_entity_ids = list(
    dict.fromkeys(payload.target_entity_ids)
  )

  # Validate all requested targets.
  # If a target entity no longer exists, silently filter it out.
  valid_target_entity_ids = []

  for target_entity_id in target_entity_ids:
    try:
      await _validate_relationship_entities(
        db=db,
        definition=definition,
        source_entity_id=payload.source_entity_id,
        target_entity_id=target_entity_id,
      )
      valid_target_entity_ids.append(target_entity_id)
    except HTTPException as exc:
      if exc.status_code == status.HTTP_404_NOT_FOUND:
        continue

      raise

  # Get all existing relationships for this source + definition.
  existing_result = await db.execute(
    select(CustomRelationship)
    .where(
      CustomRelationship.custom_relationship_definition_id
      == definition.id,
      CustomRelationship.source_entity_id
      == payload.source_entity_id,
    )
  )

  existing_relationships = existing_result.scalars().all()

  existing_by_target_id = {
    relationship.target_entity_id: relationship
    for relationship in existing_relationships
  }

  requested_target_ids = set(valid_target_entity_ids)
  existing_target_ids = set(existing_by_target_id.keys())

  # Delete relationships whose target is no longer requested.
  for target_entity_id in (
    existing_target_ids - requested_target_ids
  ):
    await db.delete(
      existing_by_target_id[target_entity_id]
    )

  # Create relationships for newly requested targets.
  for target_entity_id in (
    requested_target_ids - existing_target_ids
  ):
    relationship = CustomRelationship(
      company_id=definition.company_id,
      custom_relationship_definition_id=definition.id,
      source_entity_type=definition.source_entity_type,
      source_entity_id=payload.source_entity_id,
      source_custom_object_definition_id=(
        definition.source_custom_object_definition_id
      ),
      target_entity_type=definition.target_entity_type,
      target_entity_id=target_entity_id,
      target_custom_object_definition_id=(
        definition.target_custom_object_definition_id
      ),
    )

    db.add(relationship)

  await db.commit()

  # Return the final set of relationships.
  result = await db.execute(
    select(CustomRelationship)
    .where(
      CustomRelationship.custom_relationship_definition_id
      == definition.id,
      CustomRelationship.source_entity_id
      == payload.source_entity_id,
    )
    .options(
      selectinload(CustomRelationship.definition),
    )
    .order_by(CustomRelationship.created_at)
  )

  return result.scalars().all()
```



@router.delete(
  "/{relationship_id}",
  status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_custom_relationship(
  relationship_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  relationship = await db.get(
    CustomRelationship,
    relationship_id,
  )

  if not relationship:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Custom relationship not found",
    )

  if current_user.role != "admin":
    require_company_manager(
      current_user,
      relationship.company_id,
    )

  await db.delete(relationship)
  await db.commit()

  return None


# ---------------------------------------------------------------------------
# List relationships for an entity
# ---------------------------------------------------------------------------

@router.get(
  "/entity/{entity_type}/{entity_id}",
  response_model=List[CustomRelationshipRead],
)
async def list_entity_relationships(
  entity_type: CustomRelationshipEntityType,
  entity_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  company_id = await _get_entity_company_id(
    db=db,
    entity_type=entity_type,
    entity_id=entity_id,
  )

  if current_user.role != "admin":
    require_company_manager(
      current_user,
      company_id,
    )

  result = await db.execute(
    select(CustomRelationship)
    .where(
      or_(
        and_(
          CustomRelationship.source_entity_type == entity_type,
          CustomRelationship.source_entity_id == entity_id,
        ),
        and_(
          CustomRelationship.target_entity_type == entity_type,
          CustomRelationship.target_entity_id == entity_id,
        ),
      )
    )
    .order_by(CustomRelationship.created_at.desc())
  )

  return result.scalars().all()


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

async def _validate_relationship_definition_entities(
  db: AsyncSession,
  company_id: UUID,
  source_entity_type: CustomRelationshipEntityType,
  source_custom_object_definition_id: UUID | None,
  target_entity_type: CustomRelationshipEntityType,
  target_custom_object_definition_id: UUID | None,
):
  if (
    source_entity_type == CustomRelationshipEntityType.custom_object
    and not source_custom_object_definition_id
  ):
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail=(
        "source_custom_object_definition_id is required "
        "when source_entity_type is custom_object"
      ),
    )

  if (
    source_entity_type != CustomRelationshipEntityType.custom_object
    and source_custom_object_definition_id
  ):
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail=(
        "source_custom_object_definition_id can only be used "
        "when source_entity_type is custom_object"
      ),
    )

  if (
    target_entity_type == CustomRelationshipEntityType.custom_object
    and not target_custom_object_definition_id
  ):
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail=(
        "target_custom_object_definition_id is required "
        "when target_entity_type is custom_object"
      ),
    )

  if (
    target_entity_type != CustomRelationshipEntityType.custom_object
    and target_custom_object_definition_id
  ):
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail=(
        "target_custom_object_definition_id can only be used "
        "when target_entity_type is custom_object"
      ),
    )

  definition_ids = [
    definition_id
    for definition_id in (
      source_custom_object_definition_id,
      target_custom_object_definition_id,
    )
    if definition_id
  ]

  if not definition_ids:
    return

  result = await db.execute(
    select(CustomObjectDefinition)
    .where(
      CustomObjectDefinition.company_id == company_id,
      CustomObjectDefinition.id.in_(definition_ids),
    )
  )

  definitions = result.scalars().all()

  if len(definitions) != len(set(definition_ids)):
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail="Invalid custom object definition",
    )


async def _validate_relationship_entities(
  db: AsyncSession,
  definition: CustomRelationshipDefinition,
  source_entity_id: UUID,
  target_entity_id: UUID,
):
  source_company_id = await _get_entity_company_id(
    db=db,
    entity_type=definition.source_entity_type,
    entity_id=source_entity_id,
  )

  target_company_id = await _get_entity_company_id(
    db=db,
    entity_type=definition.target_entity_type,
    entity_id=target_entity_id,
  )

  if source_company_id != definition.company_id:
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail="Source entity belongs to a different company",
    )

  if target_company_id != definition.company_id:
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail="Target entity belongs to a different company",
    )

  if (
    definition.source_entity_type
    == CustomRelationshipEntityType.custom_object
  ):
    source_object = await db.get(
      CustomObject,
      source_entity_id,
    )

    if (
      not source_object
      or source_object.custom_object_definition_id
      != definition.source_custom_object_definition_id
    ):
      raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Source custom object has the wrong object type",
      )

  if (
    definition.target_entity_type
    == CustomRelationshipEntityType.custom_object
  ):
    target_object = await db.get(
      CustomObject,
      target_entity_id,
    )

    if (
      not target_object
      or target_object.custom_object_definition_id
      != definition.target_custom_object_definition_id
    ):
      raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Target custom object has the wrong object type",
      )


async def _validate_duplicate_relationship(
  db: AsyncSession,
  definition: CustomRelationshipDefinition,
  source_entity_id: UUID,
  target_entity_id: UUID,
  exclude_relationship_id: UUID | None = None,
):
  query = select(CustomRelationship).where(
    CustomRelationship.custom_relationship_definition_id == definition.id,
    CustomRelationship.source_entity_id == source_entity_id,
    CustomRelationship.target_entity_id == target_entity_id,
  )

  if exclude_relationship_id:
    query = query.where(
      CustomRelationship.id != exclude_relationship_id,
    )

  result = await db.execute(query)

  if result.scalar_one_or_none():
    raise HTTPException(
      status_code=status.HTTP_409_CONFLICT,
      detail="This relationship already exists",
    )


async def _validate_cardinality(
  db: AsyncSession,
  definition: CustomRelationshipDefinition,
  source_entity_id: UUID,
  target_entity_id: UUID,
  exclude_relationship_id: UUID | None = None,
):
  if definition.cardinality.value == "many":
    return

  query = select(CustomRelationship).where(
    CustomRelationship.custom_relationship_definition_id == definition.id,
    CustomRelationship.source_entity_id == source_entity_id,
  )

  if exclude_relationship_id:
    query = query.where(
      CustomRelationship.id != exclude_relationship_id,
    )

  result = await db.execute(query)

  existing = result.scalar_one_or_none()

  if existing:
    raise HTTPException(
      status_code=status.HTTP_409_CONFLICT,
      detail="This relationship only allows one target",
    )


async def _get_entity_company_id(
  db: AsyncSession,
  entity_type: CustomRelationshipEntityType,
  entity_id: UUID,
) -> UUID:
  if entity_type == CustomRelationshipEntityType.company:
    return entity_id

  elif entity_type == CustomRelationshipEntityType.user:
    entity = await db.get(
      User,
      entity_id,
    )

  elif entity_type == CustomRelationshipEntityType.project:
    entity = await db.get(
      Project,
      entity_id,
    )

  elif entity_type == CustomRelationshipEntityType.custom_object:
    entity = await db.get(
      CustomObject,
      entity_id,
    )

  else:
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail="Invalid relationship entity type",
    )

  if not entity:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Relationship entity not found",
    )

  return entity.company_id