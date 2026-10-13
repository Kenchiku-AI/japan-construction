
from sqlalchemy import (
  Column,
  DateTime,
  ForeignKey,
  Index,
  Integer,
  Numeric,
  String,
  UniqueConstraint,
  func,
)
from sqlalchemy.dialects.postgresql import UUID
from app.db.base import Base

class CatalogItem(Base):
  __tablename__ = "catalog_items"

  __table_args__ = (
    UniqueConstraint(
      "company_id",
      "code",
      name="uq_catalog_items_company_code",
    ),
    Index(
      "ix_catalog_items_company_name",
      "company_id",
      "name",
    ),
  )

  id = Column(
    UUID(as_uuid=True),
    primary_key=True,
    server_default=func.gen_random_uuid(),
  )

  company_id = Column(
    UUID(as_uuid=True),
    ForeignKey("companies.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  code = Column(String(255), nullable=False)
  name = Column(String(1000), nullable=False)
  name_kana = Column(String(1000))
  unit = Column(String(100))

  supplier_code = Column(String(255))
  supplier_name = Column(String(500))

  list_price = Column(Numeric(18, 4))
  unit_cost = Column(Numeric(18, 4))

  source_updated_at = Column(DateTime(timezone=True))

  created_at = Column(
    DateTime(timezone=True),
    nullable=False,
    server_default=func.now(),
  )
  
  updated_at = Column(
    DateTime(timezone=True),
    nullable=False,
    server_default=func.now(),
    onupdate=func.now(),
  )
