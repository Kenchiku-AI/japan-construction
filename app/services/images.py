import asyncio
import httpx
import uuid
from io import BytesIO
from PIL import Image as PILImage
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
  Company,
  Image,
  ImageTag,
  ImageTagLink,
  LineMessage,
  LineMessageImageLink,
)
from app.services.openai import get_image_tags_and_description
from app.services.s3 import BUCKET_NAME, s3_client

async def process_image(
  image: Image,
  image_url: str,
  db: AsyncSession,
):
  if image.processing_type is None:
    await add_description_and_tags(
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

async def add_description_and_tags(
  image: Image,
  image_url: str,
  db: AsyncSession,
):
  stmt = select(Company).where(
    Company.id == image.company_id
  )
  result = await db.execute(stmt)
  company = result.scalar_one_or_none()

  if not company:
    raise ValueError(f"Company not found: {image.company_id}")

  stmt = select(ImageTag).where(
    ImageTag.company_id == company.id
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

async def create_image_from_line_message(
  *,
  line_message_id,
  db: AsyncSession,
):
  line_message = await _get_line_message(
    line_message_id,
    db,
  )

  if not line_message:
    return None

  existing = await _get_existing_image(
    line_message.id,
    db,
  )

  if existing:
    if existing.status != "completed":
      existing.status = "processing"
      await db.commit()

      image_url = create_presigned_image_url(existing.image_url)

      try:
        await process_image(
          image=existing,
          image_url=image_url,
          db=db,
        )
        existing.status = "completed"
      except Exception:
        existing.status = "failed"
        raise
      finally:
        await db.commit()

    return existing

  company = await db.get(
    Company,
    line_message.company_id,
  )

  if not company:
    raise ValueError("Company not found")

  content = await download_line_message_content(
    channel_access_token=company.line_channel_access_token,
    message_id=line_message.line_platform_message_id,
  )

  image = await _create_image_from_bytes(
    image_bytes=content,
    company_id=company.id,
    created_by=None,
    db=db,
  )

  db.add(
    LineMessageImageLink(
      line_message_id=line_message.id,
      image_id=image.id,
    )
  )

  await db.commit()

  image_url = create_presigned_image_url(
    image.image_url,
  )

  await process_image(
    image=image,
    image_url=image_url,
    db=db,
  )
  image.status = "completed"
  await db.commit()

  return image

async def _get_line_message(
  line_message_id,
  db: AsyncSession,
):
  result = await db.execute(
    select(LineMessage).where(
      LineMessage.id == line_message_id,
    )
  )

  return result.scalar_one_or_none()

async def _get_existing_image(
  line_message_id,
  db: AsyncSession,
):
  result = await db.execute(
    select(Image)
    .join(LineMessageImageLink)
    .where(
      LineMessageImageLink.line_message_id
      == line_message_id
    )
  )

  return result.scalar_one_or_none()

async def _create_image_from_bytes(
  *,
  image_bytes: bytes,
  company_id,
  created_by,
  db: AsyncSession,
):
  with PILImage.open(BytesIO(image_bytes)) as pil:
    width, height = pil.size

  image_id = uuid.uuid4()

  key = f"images/{image_id}.jpg"

  upload_bytes_to_s3(
    image_bytes,
    key,
    content_type="image/jpeg",
  )

  image = Image(
    id=image_id,
    company_id=company_id,
    created_by=created_by,
    image_url=key,
    width=width,
    height=height,
    status="processing",
  )

  db.add(image)

  await db.flush()

  return image

def create_presigned_image_url(
  key: str,
) -> str:
  return s3_client.generate_presigned_url(
    "get_object",
    Params={
      "Bucket": BUCKET_NAME,
      "Key": key,
    },
    ExpiresIn=600,
  )

async def download_line_message_content(
  *,
  channel_access_token: str,
  message_id: str,
) -> bytes:
  async with httpx.AsyncClient() as client:
    response = await client.get(
      f"https://api-data.line.me/v2/bot/message/{message_id}/content",
      headers={
        "Authorization": f"Bearer {channel_access_token}",
      },
    )

    response.raise_for_status()

    return response.content

def upload_bytes_to_s3(
  data: bytes,
  key: str,
  content_type: str,
):
  s3_client.put_object(
    Bucket=BUCKET_NAME,
    Key=key,
    Body=data,
    ContentType=content_type,
    CacheControl="public, max-age=31536000, immutable",
  )