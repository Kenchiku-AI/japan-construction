from datetime import datetime, time
from uuid import UUID, uuid4
from typing import List
import logging

from fastapi import APIRouter, Depends, HTTPException, status, WebSocket, WebSocketDisconnect, Query, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.db.models import (
  Company,
  Project,
  User,
  Report,
  ReportField,
  ReportTemplate,
  ReportTemplateField,
  ReportParentType,
  ReportImage,
  ReportImageTag,
  ReportImageTagLink,
  CompanyReportTemplate,
)
from app.schemas.report import (
  ReportCreate, 
  ReportRead, 
  ReportTemplateCreate, 
  ReportTemplateRead, 
  ReportUpdate,
  ReportTemplateUpdate,
  ReportSpeechRequest,
  ReportSpeechResponse,
  ShareReportTemplateRequest,
  ReportImageCreate,
  ReportImageUpdate,
  ReportImageTagCreate
)
from app.core.dependencies import (
  get_current_user,
  get_current_user_ws,
  require_company_manager,
  require_company_member,
)
from app.services.reports import get_company_id
from app.services.openai import transcribe_and_extract_json
from app.services.s3 import s3_client, BUCKET_NAME
from app.services.ws_manager import manager

logger = logging.getLogger(__name__)

router = APIRouter(
  prefix="/reports",
  tags=["reports"]
)

@router.get(
  "",
  response_model=list[ReportRead],
)
async def list_reports(
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role == "admin":
    stmt = (
      select(Report)
      .order_by(Report.updated_at.desc())
      .limit(25)
      .options(selectinload(Report.fields))
    )

    result = await db.execute(stmt)
    return result.scalars().all()

  if not current_user.company_id:
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="User is not associated with a company",
    )

  require_company_manager(current_user, current_user.company_id)

  stmt = (
    select(Report)
    .outerjoin(
      Project,
      (Report.parent_type == ReportParentType.project)
      & (Report.parent_id == Project.id),
    )
    .where(
      or_(
        (
          (Report.parent_type == ReportParentType.company)
          & (Report.parent_id == current_user.company_id)
        ),
        (
          (Report.parent_type == ReportParentType.project)
          & (Project.company_id == current_user.company_id)
        ),
      )
    )
    .order_by(Report.updated_at.desc())
    .limit(25)
    .options(selectinload(Report.fields))
  )

  result = await db.execute(stmt)
  return result.scalars().all()

@router.post("", response_model=ReportRead, status_code=status.HTTP_201_CREATED)
async def create_report(
  payload: ReportCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = (
    select(ReportTemplate)
    .where(ReportTemplate.id == payload.template_id)
    .options(selectinload(ReportTemplate.fields))
  )
  result = await db.execute(stmt)
  template: ReportTemplate = result.scalar_one_or_none()

  if not template:
    raise HTTPException(status_code=404, detail="Report template not found")

  parent_type = template.parent_type if hasattr(template, "parent_type") else ReportParentType.project

  company_id = await get_company_id(
    parent_type=parent_type,
    parent_id=payload.parent_id,
    db=db
  )
  require_company_manager(current_user, company_id)

  report = Report(
    name=payload.name,
    template_id=template.id,
    parent_id=payload.parent_id,
    parent_type=parent_type,
    created_at=datetime.utcnow(),
    updated_at=datetime.utcnow(),
  )

  db.add(report)
  await db.flush()

  for template_field in template.fields:
    report_field = ReportField(
      name=template_field.name,
      description=template_field.description,
      value="",
      report_id=report.id,
      order=template_field.order,
    )
    db.add(report_field)

  await db.commit()
  
  stmt = (
    select(Report)
    .where(Report.id == report.id)
    .options(selectinload(Report.fields))
  )
  result = await db.execute(stmt)
  report = result.scalar_one()

  report.company_id = company_id
  report.fields.sort(key=lambda f: f.order)
  report.photo_count = 0

  return report

@router.get(
  "/templates",
  response_model=list[ReportTemplateRead],
)
async def list_report_templates(
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role == "admin":
    stmt = (
      select(ReportTemplate)
      .where(ReportTemplate.is_global.is_(True))
      .options(selectinload(ReportTemplate.fields))
      .order_by(ReportTemplate.updated_at.desc())
    )

    result = await db.execute(stmt)
    return result.scalars().all()

  if not current_user.company_id:
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="User is not associated with a company",
    )

  require_company_manager(current_user, current_user.company_id)

  stmt = (
    select(ReportTemplate)
    .join(CompanyReportTemplate)
    .where(CompanyReportTemplate.company_id == current_user.company_id)
    .options(selectinload(ReportTemplate.fields))
    .order_by(ReportTemplate.updated_at.desc())
  )

  result = await db.execute(stmt)
  return result.scalars().all()

@router.post(
  "/templates",
  response_model=ReportTemplateRead,
  status_code=status.HTTP_201_CREATED,
)
async def create_report_template(
  payload: ReportTemplateCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role not in {"admin", "manager"}:
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Only admins or managers can create templates",
    )
  
  if current_user.role == "manager" and not current_user.company_id:
    raise HTTPException(
      status_code=status.HTTP_400_BAD_REQUEST,
      detail="Manager must belong to a company",
    )

  template = ReportTemplate(
    name=payload.name,
    description=payload.description,
    is_global=current_user.role == "admin",
    parent_type=payload.parent_type,
    fields=[],
  )

  for field in payload.fields:
    template.fields.append(
      ReportTemplateField(
        name=field.name,
        description=field.description,
        order=field.order,
      )
    )

  db.add(template)
  await db.flush()

  if current_user.role == "manager":
    db.add(
      CompanyReportTemplate(
        company_id=current_user.company_id,
        report_template_id=template.id,
      )
    )

  await db.commit()
  
  stmt = (
    select(ReportTemplate)
    .options(selectinload(ReportTemplate.fields))
    .where(ReportTemplate.id == template.id)
  )

  result = await db.execute(stmt)
  template = result.scalar_one()

  template.fields.sort(key=lambda f: f.order)

  return template

@router.get(
  "/{report_id}",
  response_model=ReportRead,
)
async def get_report(
  report_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = (
    select(Report)
    .where(Report.id == report_id)
    .options(
      selectinload(Report.fields),
      selectinload(Report.images),
    )
  )

  result = await db.execute(stmt)
  report: Report | None = result.scalar_one_or_none()

  if report is None:
    raise HTTPException(404, "Report not found")

  company_id = await get_company_id(
    parent_type=report.parent_type,
    parent_id=report.parent_id,
    db=db,
  )

  if current_user.role != "admin":
    require_company_manager(current_user, company_id)

  report.company_id = company_id
  report.fields.sort(key=lambda f: f.order)
  report.photo_count = len(report.images)

  return report
  
@router.patch(
  "/{report_id}",
  response_model=ReportRead,
)
async def update_report(
  report_id: UUID,
  payload: ReportUpdate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = (
    select(Report)
    .where(Report.id == report_id)
    .options(
      selectinload(Report.fields),
      selectinload(Report.images),
    )
  )
  result = await db.execute(stmt)
  report: Report | None = result.scalar_one_or_none()

  if not report:
    raise HTTPException(status_code=404, detail="Report not found")

  company_id = await get_company_id(
    parent_type=report.parent_type,
    parent_id=report.parent_id,
    db=db,
  )

  if current_user.role != "admin":
    require_company_manager(current_user, company_id)

  if payload.name is not None:
    report.name = payload.name

  report.updated_at = datetime.utcnow()

  if payload.field_values:
    field_map = {f.id: f for f in report.fields}

    for field_id, value in payload.field_values.items():
      if field_id not in field_map:
        raise HTTPException(
          status_code=400,
          detail=f"Field {field_id} does not belong to this report",
        )

      field_map[field_id].value = value

  await db.commit()

  stmt = (
    select(Report)
    .where(Report.id == report.id)
    .options(
      selectinload(Report.fields),
      selectinload(Report.images),
    )
  )
  result = await db.execute(stmt)
  report = result.scalar_one()

  report.company_id = company_id
  report.fields.sort(key=lambda f: f.order)
  report.photo_count = len(report.images)

  return report

@router.get("/{report_id}/images")
async def list_report_images(
  report_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = select(Report).where(Report.id == report_id)
  result = await db.execute(stmt)
  report: Report | None = result.scalar_one_or_none()
  if not report:
    raise HTTPException(404, "Report not found")

  company_id = await get_company_id(
    parent_type=report.parent_type,
    parent_id=report.parent_id,
    db=db,
  )

  if not company_id:
    raise HTTPException(500, "Report does not have an associated company")
  if current_user.role != "admin":
    require_company_manager(current_user, company_id)

  stmt_images = (
    select(ReportImage)
    .where(ReportImage.report_id == report_id)
    .options(
      selectinload(ReportImage.tag_links)
      .selectinload(ReportImageTagLink.tag)
    )
  )
  result = await db.execute(stmt_images)
  images: list[ReportImage] = result.scalars().all()

  image_list = []

  for img in images:
    download_url = s3_client.generate_presigned_url(
      "get_object",
      Params={"Bucket": BUCKET_NAME, "Key": img.image_url},
      ExpiresIn=86400,
    )

    tags = [
      {
        "tag_id": link.tag.id,
        "link_id": link.id,
        "name": link.tag.name
      }
      for link in img.tag_links
    ]

    image_list.append({
      "id": img.id,
      "report_id": report_id,
      "status": img.status,
      "download_url": download_url,
      "created_at": img.created_at,
      "created_by": img.created_by,
      "width": img.width,
      "height": img.height,
      "description": img.description,
      "tags": tags
    })

  return image_list

@router.post("/{report_id}/images")
async def create_report_image(
  report_id: UUID,
  payload: ReportImageCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = select(Report).where(Report.id == report_id)
  result = await db.execute(stmt)
  report = result.scalar_one_or_none()

  if report is None:
    raise HTTPException(404, "Report not found")

  company_id = await get_company_id(
    parent_type=report.parent_type,
    parent_id=report.parent_id,
    db=db,
  )

  if current_user.role != "admin":
    require_company_manager(current_user, company_id)

  image_id = uuid4()
  key = f"reports/{report_id}/{image_id}.jpg"

  upload_url = s3_client.generate_presigned_url(
    "put_object",
    Params={
      "Bucket": BUCKET_NAME,
      "Key": key,
      "ContentType": "image/jpeg",
      "CacheControl": "public, max-age=31536000, immutable",
    },
    ExpiresIn=300,
  )

  download_url = s3_client.generate_presigned_url(
    "get_object",
    Params={"Bucket": BUCKET_NAME, "Key": key},
    ExpiresIn=86400,
  )

  report_image = ReportImage(
    id=image_id,
    report_id=report_id,
    created_by=current_user.id,
    image_url=key,
    status="pending",
    description="",
    width=payload.width,
    height=payload.height
  )

  db.add(report_image)
  await db.commit()

  return {
    "id": image_id,
    "report_id": report_id,
    "status": "pending",
    "upload_url": upload_url,
    "download_url": download_url,
    "created_at": report_image.created_at,
    "width": payload.width,
    "height": payload.height,
    "tags": []
  }

@router.get("/{report_id}/images/{image_id}/status")
async def get_report_image_status(
  report_id: UUID,
  image_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = select(Report).where(Report.id == report_id)
  result = await db.execute(stmt)
  report: Report | None = result.scalar_one_or_none()

  if not report:
    raise HTTPException(404, "Report not found")

  company_id = await get_company_id(
    parent_type=report.parent_type,
    parent_id=report.parent_id,
    db=db,
  )

  if not company_id:
    raise HTTPException(500, "Report does not have an associated company")

  if current_user.role != "admin":
    require_company_manager(current_user, company_id)

  stmt = (
    select(ReportImage)
    .where(
      ReportImage.id == image_id,
      ReportImage.report_id == report_id,
    )
  )

  result = await db.execute(stmt)
  image: ReportImage | None = result.scalar_one_or_none()

  if not image:
    raise HTTPException(404, "Image not found")

  if image.status != "complete":
    return {
      "id": image.id,
      "status": image.status,
      "description": image.description,
      "tags": [],
    }

  stmt_tags = (
    select(ReportImageTagLink)
    .where(ReportImageTagLink.report_image_id == image_id)
    .options(selectinload(ReportImageTagLink.tag))
  )

  result = await db.execute(stmt_tags)
  links = result.scalars().all()

  tags = [
    {
      "tag_id": link.tag.id,
      "link_id": link.id,
      "name": link.tag.name,
    }
    for link in links
  ]

  return {
    "id": image.id,
    "status": image.status,
    "description": image.description,
    "tags": tags,
  }

@router.patch(
  "/{report_id}/images/{image_id}",
)
async def update_report_image(
  report_id: UUID,
  image_id: UUID,
  payload: ReportImageUpdate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = select(Report).where(Report.id == report_id)
  result = await db.execute(stmt)
  report: Report | None = result.scalar_one_or_none()

  if not report:
    raise HTTPException(404, "Report not found")

  company_id = await get_company_id(
    parent_type=report.parent_type,
    parent_id=report.parent_id,
    db=db,
  )

  if current_user.role != "admin":
    require_company_manager(current_user, company_id)

  stmt = (
    select(ReportImage)
    .where(
      ReportImage.id == image_id,
      ReportImage.report_id == report_id,
    )
    .options(
      selectinload(ReportImage.tag_links).selectinload(ReportImageTagLink.tag)
    )
  )
  result = await db.execute(stmt)
  image: ReportImage | None = result.scalar_one_or_none()

  if not image:
    raise HTTPException(404, "Image not found")

  if payload.description is not None:
    image.description = payload.description

  await db.commit()

  tags = [
    {
      "tag_id": link.tag.id,
      "link_id": link.id, 
      "name": link.tag.name
    }
    for link in image.tag_links
  ]

  return {
    "id": image.id,
    "report_id": report_id,
    "description": image.description,
    "status": image.status,
    "width": image.width,
    "height": image.height,
    "created_at": image.created_at,
    "tags": tags,
  }

@router.post(
  "/{report_id}/images/{image_id}/tags",
  status_code=status.HTTP_201_CREATED,
)
async def create_report_image_tag(
  report_id: UUID,
  image_id: UUID,
  payload: ReportImageTagCreate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = select(Report).where(Report.id == report_id)
  result = await db.execute(stmt)
  report: Report | None = result.scalar_one_or_none()

  if not report:
    raise HTTPException(404, "Report not found")

  company_id = await get_company_id(
    parent_type=report.parent_type,
    parent_id=report.parent_id,
    db=db,
  )

  if current_user.role != "admin":
    require_company_manager(current_user, company_id)

  stmt = select(ReportImage).where(
    ReportImage.id == image_id,
    ReportImage.report_id == report_id,
  )
  result = await db.execute(stmt)
  image = result.scalar_one_or_none()

  if not image:
    raise HTTPException(404, "Image not found")

  stmt = select(ReportImageTag).where(
    ReportImageTag.id == payload.tag_id,
    ReportImageTag.company_id == company_id,
  )
  result = await db.execute(stmt)
  tag = result.scalar_one_or_none()

  if not tag:
    raise HTTPException(404, "Tag not found")

  stmt = select(ReportImageTagLink).where(
    ReportImageTagLink.report_image_id == image_id,
    ReportImageTagLink.tag_id == payload.tag_id,
  )
  result = await db.execute(stmt)
  existing = result.scalar_one_or_none()

  if existing:
    raise HTTPException(
      status_code=409,
      detail="Tag already attached to this image",
    )

  link = ReportImageTagLink(
    id=uuid4(),
    report_image_id=image_id,
    tag_id=payload.tag_id,
  )

  db.add(link)
  await db.commit()

  return {
    "tag_id": tag.id,
    "link_id": link.id,
    "name": tag.name
  }

@router.delete(
  "/{report_id}/images/{image_id}/tags/{link_id}",
  status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_report_image_tag(
  report_id: UUID,
  image_id: UUID,
  link_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = select(Report).where(Report.id == report_id)
  result = await db.execute(stmt)
  report: Report | None = result.scalar_one_or_none()

  if not report:
    raise HTTPException(404, "Report not found")

  company_id = await get_company_id(
    parent_type=report.parent_type,
    parent_id=report.parent_id,
    db=db,
  )

  if current_user.role != "admin":
    require_company_manager(current_user, company_id)

  stmt = select(ReportImage).where(
    ReportImage.id == image_id,
    ReportImage.report_id == report_id,
  )
  result = await db.execute(stmt)
  image = result.scalar_one_or_none()

  if not image:
    raise HTTPException(404, "Image not found")

  stmt = select(ReportImageTagLink).where(
    ReportImageTagLink.id == link_id,
    ReportImageTagLink.report_image_id == image_id,
  )
  result = await db.execute(stmt)
  link = result.scalar_one_or_none()

  if not link:
    raise HTTPException(404, "Tag link not found")

  await db.delete(link)
  await db.commit()

  return None

@router.post(
  "/{report_id}/speech",
  response_model=ReportSpeechResponse,
)
async def report_speech(
  report_id: UUID,
  payload: ReportSpeechRequest,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = (
    select(Report)
    .where(Report.id == report_id)
    .options(selectinload(Report.fields))
  )

  result = await db.execute(stmt)
  report: Report | None = result.scalar_one_or_none()

  if report is None:
    raise HTTPException(404, "Report not found")

  company_id = await get_company_id(
    parent_type=report.parent_type,
    parent_id=report.parent_id,
    db=db,
  )

  if current_user.role != "admin":
    require_company_manager(current_user, company_id)

  try:
    changed_fields = await transcribe_and_extract_json(
      speech_text=payload.text,
      fields=report.fields,
      output_language=payload.output_language
    )
    return ReportSpeechResponse(field_values=changed_fields)
  except Exception as e:
    raise HTTPException(
      status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
      detail=f"Error extracting JSON: {str(e)}"
    )

@router.delete(
  "/{report_id}",
  status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_report(
  report_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = (
    select(Report)
    .where(Report.id == report_id)
    .options(selectinload(Report.fields))
  )

  result = await db.execute(stmt)
  report: Report | None = result.scalar_one_or_none()

  if report is None:
    raise HTTPException(status_code=404, detail="Report not found")

  company_id = await get_company_id(
    parent_type=report.parent_type,
    parent_id=report.parent_id,
    db=db,
  )

  if current_user.role != "admin":
    require_company_manager(current_user, company_id)

  for field in report.fields:
    await db.delete(field)

  await db.delete(report)

  await db.commit()

  return None

@router.delete(
  "/{report_id}/images/{image_id}",
  status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_report_image(
  report_id: UUID,
  image_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = select(Report).where(Report.id == report_id)
  result = await db.execute(stmt)
  report: Report | None = result.scalar_one_or_none()

  if not report:
    raise HTTPException(404, "Report not found")

  company_id = await get_company_id(
    parent_type=report.parent_type,
    parent_id=report.parent_id,
    db=db,
  )

  if current_user.role != "admin":
    require_company_manager(current_user, company_id)

  stmt = select(ReportImage).where(
    ReportImage.id == image_id,
    ReportImage.report_id == report_id,
  )
  result = await db.execute(stmt)
  image: ReportImage | None = result.scalar_one_or_none()

  if not image:
    raise HTTPException(404, "Image not found")

  stmt_links = select(ReportImageTagLink).where(
    ReportImageTagLink.report_image_id == image_id
  )
  result = await db.execute(stmt_links)
  links = result.scalars().all()

  for link in links:
    await db.delete(link)

  try:
    s3_client.delete_object(
      Bucket=BUCKET_NAME,
      Key=image.image_url,
    )
  except Exception as e:
    raise HTTPException(
      status_code=500,
      detail=f"Failed to delete image from storage: {str(e)}"
    )

  await db.delete(image)
  await db.commit()

  return None

@router.get(
  "/templates/{report_template_id}",
  response_model=ReportTemplateRead,
)
async def get_report_template(
  report_template_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role == "admin":
    stmt = (
      select(ReportTemplate)
      .where(ReportTemplate.id == report_template_id)
      .options(selectinload(ReportTemplate.fields))
    )
  elif current_user.role == "manager":
    stmt = (
      select(ReportTemplate)
      .join(CompanyReportTemplate, CompanyReportTemplate.report_template_id == ReportTemplate.id)
      .where(
        ReportTemplate.id == report_template_id,
        CompanyReportTemplate.company_id == current_user.company_id
      )
      .options(selectinload(ReportTemplate.fields))
    )
  else:
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Not authorized to view this report template",
    )

  result = await db.execute(stmt)
  template: ReportTemplate = result.scalar_one_or_none()

  if not template:
    raise HTTPException(status_code=404, detail="Report template not found")

  return template

@router.patch(
  "/templates/{report_template_id}",
  response_model=ReportTemplateRead,
)
async def update_report_template(
  report_template_id: UUID,
  payload: ReportTemplateUpdate,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = (
    select(ReportTemplate)
    .options(selectinload(ReportTemplate.fields))
    .where(ReportTemplate.id == report_template_id)
  )
  result = await db.execute(stmt)
  template = result.scalar_one_or_none()

  if not template:
    raise HTTPException(status_code=404, detail="Template not found")

  if template.is_global:
    if current_user.role != "admin":
      raise HTTPException(
        status_code=403,
        detail="Only admins can update global templates",
      )
  else:
    if current_user.role not in {"admin", "manager"}:
      raise HTTPException(status_code=403, detail="Not authorized")

    if current_user.role == "manager":
      link_stmt = select(CompanyReportTemplate).where(
        CompanyReportTemplate.company_id == current_user.company_id,
        CompanyReportTemplate.report_template_id == template.id,
      )
      link = (await db.execute(link_stmt)).scalar_one_or_none()

      if not link:
        raise HTTPException(
          status_code=403,
          detail="Cannot edit template belonging to another company",
        )

  if payload.name is not None:
    template.name = payload.name

  if payload.description is not None:
    template.description = payload.description

  if payload.parent_type is not None:
    template.parent_type = payload.parent_type

  if payload.fields is not None:
    for f in template.fields:
      await db.delete(f)

    await db.flush()

    template.fields = [
      ReportTemplateField(
        name=f.name,
        description=f.description,
        order=f.order
      )
      for f in payload.fields
    ]

  await db.commit()

  result = await db.execute(stmt)
  return result.scalar_one()

@router.post(
  "/templates/share",
  status_code=status.HTTP_201_CREATED,
)
async def share_report_template(
  payload: ShareReportTemplateRequest,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  if current_user.role != "admin":
    raise HTTPException(
      status_code=status.HTTP_403_FORBIDDEN,
      detail="Only admins can share report templates",
    )

  template_stmt = select(ReportTemplate).where(
    ReportTemplate.id == payload.template_id
  )
  template = (await db.execute(template_stmt)).scalar_one_or_none()

  if not template:
    raise HTTPException(status_code=404, detail="Report template not found")

  if not template.is_global:
    raise HTTPException(
      status_code=400,
      detail="Only global templates can be shared with companies",
    )

  company_stmt = select(Company).where(Company.id == payload.company_id)
  company = (await db.execute(company_stmt)).scalar_one_or_none()

  if not company:
    raise HTTPException(status_code=404, detail="Company not found")

  existing_stmt = select(CompanyReportTemplate).where(
    CompanyReportTemplate.company_id == payload.company_id,
    CompanyReportTemplate.report_template_id == payload.template_id,
  )
  existing = (await db.execute(existing_stmt)).scalar_one_or_none()

  if existing:
    raise HTTPException(
      status_code=409,
      detail="Company already has this template",
    )

  link = CompanyReportTemplate(
    company_id=payload.company_id,
    report_template_id=payload.template_id,
  )

  db.add(link)
  await db.commit()

  return {"message": "Template shared successfully"}

@router.delete(
  "/templates/{report_template_id}",
  status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_report_template(
  report_template_id: UUID,
  db: AsyncSession = Depends(get_db),
  current_user: User = Depends(get_current_user),
):
  stmt = (
    select(ReportTemplate)
    .options(selectinload(ReportTemplate.fields))
    .where(ReportTemplate.id == report_template_id)
  )

  result = await db.execute(stmt)
  template: ReportTemplate | None = result.scalar_one_or_none()

  if not template:
    raise HTTPException(status_code=404, detail="Template not found")

  if template.is_global:
    if current_user.role != "admin":
      raise HTTPException(
        status_code=403,
        detail="Only admins can delete global templates",
      )
  else:
    if current_user.role not in {"admin", "manager"}:
      raise HTTPException(status_code=403, detail="Not authorized")

    if current_user.role == "manager":
      link_stmt = select(CompanyReportTemplate).where(
        CompanyReportTemplate.company_id == current_user.company_id,
        CompanyReportTemplate.report_template_id == template.id,
      )
      link = (await db.execute(link_stmt)).scalar_one_or_none()

      if not link:
        raise HTTPException(
          status_code=403,
          detail="Cannot delete template belonging to another company",
        )

  link_stmt = select(CompanyReportTemplate).where(
    CompanyReportTemplate.report_template_id == template.id
  )
  links = (await db.execute(link_stmt)).scalars().all()

  for link in links:
    await db.delete(link)

  for field in template.fields:
    await db.delete(field)

  await db.delete(template)

  await db.commit()

  return None

@router.websocket("/images/ws")
async def report_image_ws(
  ws: WebSocket,
  access_token: str | None = None
):
  logger.info("WebSocket connection attempt started")
    
  token = access_token or ws.cookies.get("accessToken")
  logger.info(f"Token received: {bool(token)}")
  logger.info(f"Access token param: {bool(access_token)}")
  logger.info(f"Cookie token: {bool(ws.cookies.get('accessToken'))}")

  logger.info("Getting database connection...")
  if not token:
    logger.warning("No access token provided - closing connection")
    await ws.close(code=1008)
    return

  async for db in get_db():
    try:
      current_user = await get_current_user_ws(token, db)
    except Exception:
      await ws.close(code=1008)
      return
    break

  user_id = str(current_user.id)
  logger.info(f"🔗 WebSocket connecting user: '{user_id}' (type: {type(user_id)})")

  await manager.connect(user_id, ws)
  logger.info("WebSocket connection established successfully")
  try:
    while True:
      message = await ws.receive_text()
      logger.info(f"Received message: {message}")
  except WebSocketDisconnect:
    logger.info(f"WebSocket disconnected for user {user_id}")
    manager.disconnect(user_id, ws)
    await ws.close()