import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
  Boolean,
  Column,
  DateTime,
  Enum,
  ForeignKey,
  String,
  Text,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.db.base import Base


class FormJobStatus(str, enum.Enum):
  pending = "pending"
  processing = "processing"
  completed = "completed"
  needs_review = "needs_review"
  failed = "failed"


class FormJobOrigin(str, enum.Enum):
  web = "web"
  email = "email"
  api = "api"


class FormJob(Base):
  __tablename__ = "form_jobs"

  id = Column(
    UUID(as_uuid=True),
    primary_key=True,
    default=uuid.uuid4,
    index=True,
  )

  company_id = Column(
    UUID(as_uuid=True),
    ForeignKey("companies.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  project_id = Column(
    UUID(as_uuid=True),
    ForeignKey("projects.id", ondelete="SET NULL"),
    nullable=True,
    index=True,
  )

  name = Column(
    String,
    nullable=False,
  )

  description = Column(
    Text,
    nullable=True,
  )

  status = Column(
    Enum(
      FormJobStatus,
      name="form_job_status",
    ),
    nullable=False,
    default=FormJobStatus.pending,
    index=True,
  )

  origin = Column(
    Enum(
      FormJobOrigin,
      name="form_job_origin",
    ),
    nullable=False,
  )

  origin_id = Column(
    String,
    nullable=True,
  )

  error = Column(
    Text,
    nullable=True,
  )

  result_json = Column(
    JSONB,
    nullable=True,
  )

  company = relationship(
    "Company",
  )

  project = relationship(
    "Project",
  )

  files = relationship(
    "FormJobFile",
    back_populates="form_job",
    cascade="all, delete-orphan",
  )

  created_at = Column(
    DateTime(timezone=True),
    default=lambda: datetime.now(timezone.utc),
  )

  updated_at = Column(
    DateTime(timezone=True),
    default=lambda: datetime.now(timezone.utc),
    onupdate=lambda: datetime.now(timezone.utc),
  )


class FormJobFile(Base):
  __tablename__ = "form_job_files"

  id = Column(
    UUID(as_uuid=True),
    primary_key=True,
    default=uuid.uuid4,
    index=True,
  )

  form_job_id = Column(
    UUID(as_uuid=True),
    ForeignKey("form_jobs.id", ondelete="CASCADE"),
    nullable=False,
    index=True,
  )

  filename = Column(
    String,
    nullable=False,
  )

  content_type = Column(
    String,
    nullable=True,
  )

  s3_key = Column(
    String,
    nullable=False,
  )

  is_input = Column(
    Boolean,
    nullable=False,
    default=True,
  )

  form_job = relationship(
    "FormJob",
    back_populates="files",
  )

  created_at = Column(
    DateTime(timezone=True),
    default=lambda: datetime.now(timezone.utc),
  )