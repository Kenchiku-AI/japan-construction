import uuid
from sqlalchemy import Column, String, Integer, Boolean
from sqlalchemy.dialects.postgresql import UUID
from app.db.base import Base

class BillingPlan(Base):
  __tablename__ = "billing_plans"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
  name = Column(String, nullable=False)
  description = Column(String, nullable=True)
  stripe_price_id = Column(String, nullable=False, unique=True)
  amount_jpy = Column(Integer, nullable=False) 
  is_hidden = Column(Boolean, default=False, nullable=False)
  is_default = Column(Boolean, default=False, nullable=False)
  sort_order = Column(Integer, default=0)