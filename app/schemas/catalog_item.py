from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

class CatalogItemCreate(BaseModel):
  company_id: UUID
  code: str = Field(min_length=1, max_length=255)
  name: str = Field(min_length=1, max_length=1000)
  name_kana: str | None = Field(default=None, max_length=1000)
  unit: str | None = Field(default=None, max_length=100)

  supplier_code: str | None = Field(default=None, max_length=255)
  supplier_name: str | None = Field(default=None, max_length=500)

  list_price: Decimal | None = None
  unit_cost: Decimal | None = None

  source_updated_at: datetime | None = None


class CatalogItemUpdate(BaseModel):
  code: str | None = Field(default=None, min_length=1, max_length=255)
  name: str | None = Field(default=None, min_length=1, max_length=1000)
  name_kana: str | None = Field(default=None, max_length=1000)
  unit: str | None = Field(default=None, max_length=100)

  supplier_code: str | None = Field(default=None, max_length=255)
  supplier_name: str | None = Field(default=None, max_length=500)

  list_price: Decimal | None = None
  unit_cost: Decimal | None = None

  source_updated_at: datetime | None = None


class CatalogItemResponse(BaseModel):
  model_config = ConfigDict(from_attributes=True)

  id: UUID
  company_id: UUID
  code: str
  name: str
  name_kana: str | None
  unit: str | None

  supplier_code: str | None
  supplier_name: str | None

  list_price: Decimal | None
  unit_cost: Decimal | None

  source_updated_at: datetime | None
  created_at: datetime
  updated_at: datetime