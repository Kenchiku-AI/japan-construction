from typing import Optional
from uuid import UUID
from pydantic import BaseModel, field_validator

class BillingPlanCreate(BaseModel):
  name: str
  description: Optional[str] = None
  stripe_price_id: str
  amount_jpy: int
  is_hidden: bool = False
  is_default: bool = False
  sort_order: int = 0

  @field_validator("stripe_price_id")
  @classmethod
  def validate_price_id(cls, v: str) -> str:
    if not v.startswith("price_"):
      raise ValueError("stripe_price_id must start with 'price_' — check you didn't paste a product ID (prod_...)")
    return v

class BillingPlanUpdate(BaseModel):
  name: Optional[str] = None
  description: Optional[str] = None
  stripe_price_id: Optional[str] = None
  amount_jpy: Optional[int] = None
  is_hidden: Optional[bool] = None
  is_default: Optional[bool] = None
  sort_order: Optional[int] = None

  @field_validator("stripe_price_id")
  @classmethod
  def validate_price_id(cls, v: str | None) -> str | None:
    if v is not None and not v.startswith("price_"):
      raise ValueError("stripe_price_id must start with 'price_' — check you didn't paste a product ID (prod_...)")
    return v

class BillingPlanRead(BaseModel):
  id: UUID
  name: str
  description: Optional[str]
  stripe_price_id: str
  amount_jpy: int
  is_hidden: bool
  is_default: bool
  sort_order: int

  model_config = {"from_attributes": True}