import asyncio
import uuid
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
  Company,
  Image,
  ImageTag,
  ImageTagLink,
)
from app.services.openai import get_image_tags_and_description
from app.services.reports import get_company_id

async def process_image(
  image: Image,
  image_url: str,
  db: AsyncSession,
):
  if image.processing_type is None:
    await process_report_image(
      image=image,
      image_url=image_url,
      db=db,
    )
    return

  if image.processing_type == "status":
    await process_status_image(
      image=image,
      image_url=image_url,
      db=db,
    )
    return

  raise ValueError(
    f"Unknown processing_type: {image.processing_type}"
  )

async def process_report_image(
  image: Image,
  image_url: str,
  db: AsyncSession,
):
  company_id = await get_company_id(
    image.report.parent_type,
    image.report.parent_id,
    db,
  )

  stmt = select(Company).where(
    Company.id == company_id
  )
  result = await db.execute(stmt)
  company = result.scalar_one_or_none()

  if not company:
    raise ValueError(f"Company not found: {company_id}")

  stmt = select(ImageTag).where(
    ImageTag.company_id == company_id
  )
  result = await db.execute(stmt)
  tags_list = result.scalars().all()

  ai_result = None

  for attempt in range(3):
    try:
      ai_result = await get_image_tags_and_description(
        image_url=image_url,
        tags=tags_list,
        include_description=company.image_descriptions_enabled,
      )
      break

    except Exception as e:
      if attempt == 2:
        raise

      await asyncio.sleep(1)

  tag_ids = ai_result.get("tags", [])
  description = ai_result.get("description")

  tag_lookup = {
    str(tag.id): tag
    for tag in tags_list
  }

  links = []

  for tag_id in tag_ids:
    tag_obj = tag_lookup.get(tag_id)

    if not tag_obj:
      continue

    links.append({
      "id": uuid.uuid4(),
      "image_id": image.id,
      "tag_id": tag_id,
    })

  if links:
    stmt = (
      insert(ImageTagLink)
      .values(links)
      .on_conflict_do_nothing()
    )

    await db.execute(stmt)

  if description is not None:
    image.description = description

async def process_status_image(
  image: Image,
  image_url: str,
  db: AsyncSession,
):
  pass