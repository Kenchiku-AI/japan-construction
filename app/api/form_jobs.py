import uuid

import boto3
from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.db.session import get_db
from app.db.models.form_job import (
  FormJob,
  FormJobFile,
  FormJobOrigin,
  FormJobStatus,
)
from app.services.s3 import BUCKET_NAME

router = APIRouter(
  prefix="/form-jobs",
  tags=["form-jobs"],
)


s3 = boto3.client("s3")
sqs = boto3.client("sqs")


@router.post("")
async def create_form_job(
  project_id: uuid.UUID | None = Form(None),
  instructions: str | None = Form(None),
  files: list[UploadFile] = File(...),
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  company_id = current_user.company_id

  job = FormJob(
    company_id=company_id,
    project_id=project_id,
    instructions=instructions,
    status=FormJobStatus.pending,
    origin=FormJobOrigin.web,
  )

  db.add(job)

  await db.flush()

  for upload in files:

    filename = upload.filename or "file"

    s3_key = (
      f"form-jobs/"
      f"{job.id}/"
      f"input/"
      f"{filename}"
    )

    s3.upload_fileobj(
      upload.file,
      BUCKET_NAME,
      s3_key,
    )

    job_file = FormJobFile(
      form_job_id=job.id,
      filename=filename,
      content_type=upload.content_type,
      s3_key=s3_key,
      is_input=True,
    )

    db.add(job_file)

  await db.commit()

  sqs.send_message(
    QueueUrl="YOUR_QUEUE_URL",
    MessageBody=str(
      {
        "form_job_id": str(job.id),
      }
    ),
  )

  return {
    "id": str(job.id),
    "status": job.status.value,
  }

@router.get("/{form_job_id}")
async def get_form_job(
  form_job_id: uuid.UUID,
  db: AsyncSession = Depends(get_db),
):
  job = await db.scalar(
    select(FormJob)
    .where(FormJob.id == form_job_id)
  )

  if job is None:
    raise HTTPException(
      status_code=404,
      detail="Form job not found",
    )

  return {
    "id": str(job.id),
    "status": job.status.value,
    "instructions": job.instructions,
    "files": [
      {
        "id": str(file.id),
        "filename": file.filename,
        "is_input": file.is_input,
      }
      for file in job.files
    ],
  }