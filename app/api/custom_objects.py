from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
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
  CustomObject,
  CustomObjectDefinition,
)
from app.schemas.custom_object import (
  CustomObjectDefinitionCreate,
  CustomObjectDefinitionUpdate,
  CustomObjectDefinitionRead,
  CustomObjectCreate,
  CustomObjectUpdate,
  CustomObjectRead,
)


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

  existing_result = await db.execute(
    select(CustomObjectDefinition)
    .where(
      CustomObjectDefinition.company_id == payload.company_id,
      CustomObjectDefinition.key == payload.key,
    )
  )

  if existing_result.scalar_one_or_none():
    raise HTTPException(
      status_code=status.HTTP_409_CONFLICT,
      detail="A custom object definition with this key already exists",
    )

  definition = CustomObjectDefinition(
    company_id=payload.company_id,
    name=payload.name,
    key=payload.key,
    description=payload.description,
  )

  db.add(definition)
  await db.commit()
  await db.refresh(definition)

  return definition


@router.get(
  "/definitions/{definition_id}",
  response_model=CustomObjectDefinitionRead,
)
async def get_custom_object_definition(
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

  return definition


@router.patch(
  "/definitions/{definition_id}",
  response_model=CustomObjectDefinitionRead,
)
async def update_custom_object_definition(
  definition_id: UUID,
  payload: CustomObjectDefinitionUpdate,
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

  updates = payload.model_dump(
    exclude_unset=True,
  )

  if "key" in updates:
    existing_result = await db.execute(
      select(CustomObjectDefinition)
      .where(
        CustomObjectDefinition.company_id == definition.company_id,
        CustomObjectDefinition.key == updates["key"],
        CustomObjectDefinition.id != definition.id,
      )
    )

    if existing_result.scalar_one_or_none():
      raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="A custom object definition with this key already exists",
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

@router.get(
  "",
  response_model=List[CustomObjectRead],
)
async def list_custom_objects(
  company_id: UUID,
  custom_object_definition_id: UUID | None = None,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  require_company_manager(
    current_user,
    company_id,
  )

  query = (
    select(CustomObject)
    .where(
      CustomObject.company_id == company_id,
    )
    .options(
      selectinload(CustomObject.definition),
    )
    .order_by(CustomObject.name)
  )

  if custom_object_definition_id:
    query = query.where(
      CustomObject.custom_object_definition_id
      == custom_object_definition_id,
    )

  result = await db.execute(query)

  return result.scalars().all()


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

  definition = await db.get(
    CustomObjectDefinition,
    payload.custom_object_definition_id,
  )

  if not definition:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Custom object definition not found",
    )

  if definition.company_id != payload.company_id:
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail="Custom object definition belongs to a different company",
    )

  custom_object = CustomObject(
    company_id=payload.company_id,
    custom_object_definition_id=payload.custom_object_definition_id,
    name=payload.name,
    description=payload.description,
  )

  db.add(custom_object)
  await db.commit()

  result = await db.execute(
    select(CustomObject)
    .where(
      CustomObject.id == custom_object.id,
    )
    .options(
      selectinload(CustomObject.definition),
    )
  )

  return result.scalar_one()


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

  return custom_object


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

  if "custom_object_definition_id" in updates:
    definition = await db.get(
      CustomObjectDefinition,
      updates["custom_object_definition_id"],
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

  for field, value in updates.items():
    setattr(custom_object, field, value)

  await db.commit()

  result = await db.execute(
    select(CustomObject)
    .where(
      CustomObject.id == custom_object.id,
    )
    .options(
      selectinload(CustomObject.definition),
    )
  )

  return result.scalar_one()


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