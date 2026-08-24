from uuid import UUID

from fastapi import (
  APIRouter,
  Depends,
  File,
  Form,
  HTTPException,
  UploadFile,
  status,
)
from fastapi.responses import RedirectResponse
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
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
from app.schemas.form_job import FormJobResponse
from app.services.forms.queue import enqueue_form_job
from app.services.forms.storage import FormStorage
from app.services.s3 import BUCKET_NAME


router = APIRouter(
  prefix="/form-jobs",
  tags=["Form Jobs"],
)


@router.post(
  "",
  response_model=FormJobResponse,
  status_code=status.HTTP_201_CREATED,
)
async def create_form_job(
  company_id: UUID = Form(...),
  file: UploadFile = File(...),
  instructions: str | None = Form(None),
  project_id: UUID | None = Form(None),
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  require_company_manager(
    current_user,
    company_id,
  )

  if project_id is not None:
    result = await db.execute(
      select(Project).where(
        Project.id == project_id,
        Project.company_id == company_id,
      ),
    )

    project = result.scalar_one_or_none()

    if project is None:
      raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Project not found",
      )

  if not file.filename:
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail="A file is required",
    )

  if file.content_type != "application/pdf":
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail="Only PDF files are supported",
    )

  form_job = FormJob(
    company_id=company_id,
    project_id=project_id,
    instructions=instructions,
    status=FormJobStatus.pending,
    origin=FormJobOrigin.web,
  )

  db.add(form_job)

  await db.flush()

  storage = FormStorage(
    bucket_name=BUCKET_NAME,
  )

  s3_key = (
    f"form-jobs/{form_job.id}/input/"
    f"{Path(file.filename).name}"
  )

  await run_in_threadpool(
    storage.upload_fileobj,
    file.file,
    s3_key,
    file.content_type,
  )

  form_file = FormJobFile(
    form_job_id=form_job.id,
    filename=file.filename,
    content_type=file.content_type,
    s3_key=s3_key,
    is_input=True,
  )

  db.add(form_file)

  await db.commit()

  await db.refresh(form_job)

  enqueue_form_job(
    form_job.id,
  )

  return form_job

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

  return form_job

@router.get(
  "/{form_job_id}/download",
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
    select(FormJobFile).where(
      FormJobFile.form_job_id == form_job.id,
      FormJobFile.is_input.is_(False),
    ),
  )

  output_file = result.scalar_one_or_none()

  if output_file is None:
    raise HTTPException(
      status_code=status.HTTP_404_NOT_FOUND,
      detail="Completed form file not found",
    )

  storage = FormStorage(
    bucket_name=BUCKET_NAME,
  )

  download_url = storage.create_download_url(
    s3_key=output_file.s3_key,
  )

  return RedirectResponse(
    url=download_url,
  )