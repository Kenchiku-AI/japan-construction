from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.orm import relationship
from datetime import datetime

from app.db.base import Base

class Company(Base):
  __tablename__ = "companies"

  id = Column(Integer, primary_key=True, index=True)
  name = Column(String, nullable=False, index=True)

  company_users = relationship(
    "CompanyUser",
    back_populates="company",
    cascade="all, delete-orphan",
  )

  projects = relationship(
    "Project",
    back_populates="company",
    cascade="all, delete-orphan",
  )

  created_at = Column(DateTime, default=datetime.utcnow)
  updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)