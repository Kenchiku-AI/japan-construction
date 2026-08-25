from uuid import UUID

from fastapi import (
  APIRouter,
  Depends,
  Form,
  HTTPException,
  status,
)
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path

from app.core.dependencies import (
  get_current_user,
  require_company_manager,
)
from app.db.session import get_db
from app.db.models.form_job import (
  FormJob,
  FormJobFile,
  FormJobOrigin,
  FormJobStatus,
)
from app.db.models.project import Project
from app.db.models.user import User
from app.schemas.form_job import (
  FormJobCreate,
  FormJobCreateResponse,
  FormJobResponse,
  FormJobDownloadResponse,
)
from app.services.forms.storage import FormStorage
from app.services.s3 import BUCKET_NAME


router = APIRouter(
  prefix="/form-jobs",
  tags=["Form Jobs"],
)


@router.post(
  "",
  response_model=FormJobCreateResponse,
  status_code=status.HTTP_201_CREATED,
)
async def create_form_job(
  payload: FormJobCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  require_company_manager(
    current_user,
    payload.company_id,
  )

  if payload.project_id is not None:
    result = await db.execute(
      select(Project).where(
        Project.id == payload.project_id,
        Project.company_id == payload.company_id,
      ),
    )

    project = result.scalar_one_or_none()

    if project is None:
      raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Project not found",
      )

  if not payload.name.strip():
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail="A form name is required",
    )

  if not payload.filename:
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail="A filename is required",
    )

  safe_filename = Path(payload.filename).name

  form_job = FormJob(
    company_id=payload.company_id,
    project_id=payload.project_id,
    name=payload.name.strip(),
    description=payload.description,
    status=FormJobStatus.pending,
    origin=FormJobOrigin.web,
  )

  db.add(form_job)

  await db.flush()

  s3_key = (
    f"form-job-inputs/{form_job.id}/"
    f"{safe_filename}"
  )

  storage = FormStorage(
    bucket_name=BUCKET_NAME,
  )

  upload_url = storage.create_upload_url(
    s3_key=s3_key,
    content_type=payload.content_type,
  )

  form_file = FormJobFile(
    form_job_id=form_job.id,
    filename=safe_filename,
    content_type=payload.content_type,
    s3_key=s3_key,
    is_input=True,
  )

  db.add(form_file)

  await db.commit()

  await db.refresh(form_job)

  return FormJobCreateResponse(
    id=form_job.id,
    name=form_job.name,
    status=form_job.status,
    upload_url=upload_url,
    filename=safe_filename,
    created_at=form_job.created_at,
  )

@router.get(
  "",
  response_model=list[FormJobResponse],
)
async def list_form_jobs(
  company_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  require_company_manager(
    current_user,
    company_id,
  )

  result = await db.execute(
    select(FormJob)
    .options(
      selectinload(FormJob.files),
    )
    .where(
      FormJob.company_id == company_id,
    )
    .order_by(
      FormJob.created_at.desc(),
    ),
  )

  return result.scalars().unique().all()

@router.get(
  "/{form_job_id}",
  response_model=FormJobResponse,
)
async def get_form_job(
  form_job_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  result = await db.execute(
    select(FormJob)
    .options(
      selectinload(FormJob.files),
    )
    .where(
      FormJob.id == form_job_id,
    ),
  )

  form_job = result.scalar_one_or_none()

  if form_job is None:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Form job not found",
    )

  require_company_manager(
    current_user,
    form_job.company_id,
  )

  return form_job

@router.get(
  "/{form_job_id}/download",
  response_model=FormJobDownloadResponse,
)
async def download_form_job(
  form_job_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  result = await db.execute(
    select(FormJob).where(
      FormJob.id == form_job_id,
    ),
  )

  form_job = result.scalar_one_or_none()

  if form_job is None:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Form job not found",
    )

  require_company_manager(
    current_user,
    form_job.company_id,
  )

  if form_job.status != FormJobStatus.completed:
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail="The completed form is not available",
    )

  result = await db.execute(
    select(FormJobFile)
    .where(
      FormJobFile.form_job_id == form_job.id,
      FormJobFile.is_input.is_(False),
    )
    .order_by(
      FormJobFile.filename,
    ),
  )

  output_files = result.scalars().all()

  if not output_files:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="No completed form files found",
    )

  storage = FormStorage(
    bucket_name=BUCKET_NAME,
  )

  files = []

  for output_file in output_files:
    download_url = storage.create_download_url(
      s3_key=output_file.s3_key,
    )

    files.append(
      {
        "id": str(output_file.id),
        "filename": output_file.filename,
        "content_type": output_file.content_type,
        "download_url": download_url,
      }
    )

  return FormJobDownloadResponse(
    form_job_id=str(form_job.id),
    files=files,
  )