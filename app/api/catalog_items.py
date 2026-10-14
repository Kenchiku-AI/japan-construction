from uuid import UUID

from fastapi import (
  APIRouter,
  Depends,
  HTTPException,
  Query,
  Response,
  status,
)
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user, require_company_manager
from app.db.models.user import User
from app.db.session import get_db
from app.db.models.catalog_item import CatalogItem
from app.schemas.catalog_item import (
  CatalogItemCreate,
  CatalogItemResponse,
  CatalogItemUpdate,
)


router = APIRouter(
  prefix="/catalog-items",
  tags=["Catalog Items"],
)


@router.get(
  "",
  response_model=list[CatalogItemResponse],
)
async def list_catalog_items(
  company_id: UUID,
  search: str | None = Query(default=None, min_length=1),
  limit: int = Query(default=100, ge=1, le=500),
  offset: int = Query(default=0, ge=0),
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  require_company_manager(
    current_user,
    company_id,
  )

  query = select(CatalogItem).where(
    CatalogItem.company_id == company_id,
  )

  if search:
    search_term = f"%{search.strip()}%"
    query = query.where(
      or_(
        CatalogItem.code.ilike(search_term),
        CatalogItem.name.ilike(search_term),
        CatalogItem.name_kana.ilike(search_term),
      ),
    )

  query = query.order_by(
    CatalogItem.name.asc(),
    CatalogItem.code.asc(),
  ).offset(offset).limit(limit)

  result = await db.execute(query)

  return result.scalars().all()


@router.post(
  "",
  response_model=CatalogItemResponse,
  status_code=status.HTTP_201_CREATED,
)
async def create_catalog_item(
  payload: CatalogItemCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  require_company_manager(
    current_user,
    payload.company_id,
  )

  code = payload.code.strip()
  name = payload.name.strip()

  if not code:
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail="A catalog item code is required",
    )

  if not name:
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail="A catalog item name is required",
    )

  existing_result = await db.execute(
    select(CatalogItem.id).where(
      CatalogItem.company_id == payload.company_id,
      CatalogItem.code == code,
    ),
  )

  if existing_result.scalar_one_or_none() is not None:
    raise HTTPException(
      status_code=status.HTTP_409_CONFLICT,
      detail="A catalog item with this code already exists",
    )

  values = payload.model_dump(exclude={"company_id"})
  values["code"] = code
  values["name"] = name

  item = CatalogItem(
    company_id=payload.company_id,
    **values,
  )

  db.add(item)

  try:
    await db.commit()
    await db.refresh(item)
  except IntegrityError:
    await db.rollback()
    raise HTTPException(
      status_code=status.HTTP_409_CONFLICT,
      detail="A catalog item with this code already exists",
    )

  return item


@router.patch(
  "/{item_id}",
  response_model=CatalogItemResponse,
)
async def update_catalog_item(
  item_id: UUID,
  company_id: UUID,
  payload: CatalogItemUpdate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  require_company_manager(
    current_user,
    company_id,
  )

  result = await db.execute(
    select(CatalogItem).where(
      CatalogItem.id == item_id,
      CatalogItem.company_id == company_id,
    ),
  )

  item = result.scalar_one_or_none()

  if item is None:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Catalog item not found",
    )

  values = payload.model_dump(exclude_unset=True)

  if "code" in values and values["code"] is not None:
    values["code"] = values["code"].strip()

    if not values["code"]:
      raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="A catalog item code is required",
      )

  if "name" in values and values["name"] is not None:
    values["name"] = values["name"].strip()

    if not values["name"]:
      raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="A catalog item name is required",
      )

  if "code" in values and values["code"] != item.code:
    existing_result = await db.execute(
      select(CatalogItem.id).where(
        CatalogItem.company_id == company_id,
        CatalogItem.code == values["code"],
        CatalogItem.id != item_id,
      ),
    )

    if existing_result.scalar_one_or_none() is not None:
      raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="A catalog item with this code already exists",
      )

  for field, value in values.items():
    setattr(item, field, value)

  try:
    await db.commit()
    await db.refresh(item)
  except IntegrityError:
    await db.rollback()
    raise HTTPException(
      status_code=status.HTTP_409_CONFLICT,
      detail="A catalog item with this code already exists",
    )

  return item


@router.delete(
  "/{item_id}",
  status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_catalog_item(
  item_id: UUID,
  company_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  require_company_manager(
    current_user,
    company_id,
  )

  result = await db.execute(
    select(CatalogItem).where(
      CatalogItem.id == item_id,
      CatalogItem.company_id == company_id,
    ),
  )

  item = result.scalar_one_or_none()

  if item is None:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Catalog item not found",
    )

  await db.delete(item)
  await db.commit()

  return Response(status_code=status.HTTP_204_NO_CONTENT)