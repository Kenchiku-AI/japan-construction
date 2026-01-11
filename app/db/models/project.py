from datetime import datetime

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship

from app.db.base import Base

class Project(Base):
  __tablename__ = "projects"

  id = Column(Integer, primary_key=True, index=True)

  name = Column(String, nullable=False)
  description = Column(String, nullable=True)

  company_id = Column(
    Integer,
    ForeignKey("companies.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  company = relationship("Company", back_populates="projects")

  created_at = Column(DateTime, default=datetime.utcnow)
  updated_at = Column(
    DateTime,
    default=datetime.utcnow,
    onupdate=datetime.utcnow,
  )
