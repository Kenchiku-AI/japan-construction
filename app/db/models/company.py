import uuid

from sqlalchemy import Column, String, DateTime, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from datetime import datetime

from app.db.base import Base

class Company(Base):
  __tablename__ = "companies"

  id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
  name = Column(String, nullable=False, index=True)
  corporate_number = Column(String, nullable=True, index=True)
  image_descriptions_enabled = Column(Boolean, default=True, nullable=False)

  users = relationship("User", back_populates="company")

  projects = relationship(
    "Project",
    back_populates="company",
    cascade="all, delete-orphan",
  )

  image_tags = relationship(
    "ReportImageTag",
    back_populates="company",
    cascade="all, delete-orphan",
  )

  company_report_templates = relationship(
    "CompanyReportTemplate",
    back_populates="company",
    cascade="all, delete-orphan",
  )

  stripe_customer_id = Column(String, nullable=True, index=True)
  stripe_subscription_id = Column(String, nullable=True, index=True)
  stripe_subscription_status = Column(String, nullable=True)
  has_payment_method = Column(Boolean, default=False, nullable=False)
  billing_exempt = Column(Boolean, default=False, nullable=False)

  created_at = Column(DateTime, default=datetime.utcnow)
  updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)