import boto3
import json
import asyncio
import uuid
import logging

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.future import select
from sqlalchemy.dialects.postgresql import insert

from app.db.session import AsyncSessionLocal
from app.db.models import ReportImage, ReportImageTag, ReportImageTagLink, Company
from app.services.openai import get_image_tags_and_description
from app.services.s3 import s3_client, BUCKET_NAME
from app.services.reports import get_company_id
from app.services.ws_events import publish_image_tags_ready
from app.core.config import settings

QUEUE_URL = settings.SQS_QUEUE_URL
AWS_REGION = settings.AWS_REGION

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

sqs = boto3.client("sqs", region_name=AWS_REGION)

def parse_s3_key(key: str):
  parts = key.split("/")

  if len(parts) < 3:
    raise ValueError(f"Invalid S3 key format: {key}")

  report_id = parts[1]
  filename = parts[2]
  image_id = filename.split(".")[0]

  return report_id, uuid.UUID(image_id)

async def process_message(message):
  image_id = None

  try:
    body = json.loads(message["Body"])
    record = body["Records"][0]
    bucket = record["s3"]["bucket"]["name"]
    key = record["s3"]["object"]["key"]

    report_id, image_id = parse_s3_key(key)
    logger.info(f"Processing image {image_id}")

    async with AsyncSessionLocal() as db:
      stmt = (
        select(ReportImage)
        .options(selectinload(ReportImage.report))
        .where(ReportImage.id == image_id)
      )
      result = await db.execute(stmt)
      image = result.scalar_one_or_none()

      if not image:
        logger.warning(f"Image not found: {image_id}")
        return
      if image.status != "pending":
        logger.info(f"Image already processed or in progress: {image_id}")
        return

      image.status = "processing"
      await db.commit()

      image_url = s3_client.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": BUCKET_NAME, "Key": key},
        ExpiresIn=600,
      )

      company_id = await get_company_id(
        image.report.parent_type,
        image.report.parent_id,
        db
      )

      stmt = select(Company).where(Company.id == company_id)
      result = await db.execute(stmt)
      company = result.scalar_one_or_none()

      if not company:
        raise ValueError(f"Company not found: {company_id}")

      stmt = select(ReportImageTag).where(ReportImageTag.company_id == company_id)
      result = await db.execute(stmt)
      tags_list = result.scalars().all()

      ai_result = None

      for attempt in range(3):
        try:
          ai_result = await get_image_tags_and_description(
            image_url,
            tags_list,
            company.image_descriptions_enabled
          )
          break
        except Exception as e:
          logger.warning(f"OpenAI attempt {attempt+1} failed: {e}")
          if attempt == 2:
            raise
          await asyncio.sleep(1)

      tag_ids = ai_result.get("tags", [])
      description = ai_result.get("description")

      tag_lookup = {str(t.id): t for t in tags_list}
      links = []
      tags_payload = []

      for tag_id in tag_ids:
        tag_obj = tag_lookup.get(tag_id)

        if not tag_obj:
          logger.warning("Tag ID returned by OpenAI not found in DB: %s", tag_id)
          continue

        link_id = uuid.uuid4()
        links.append({
          "id": link_id,
          "report_image_id": image.id,
          "tag_id": tag_id
        })

        tags_payload.append({
          "tag_id": tag_id,
          "link_id": link_id,
          "name": tag_obj.name
        })

      if links:
        link_insert = insert(ReportImageTagLink).values(links).on_conflict_do_nothing()
        await db.execute(link_insert)

      if description is not None:
        image.description = description

      image.status = "completed"
      await db.commit()

      await publish_image_tags_ready(
        str(image.created_by),
        {
          "type": "image_tags_ready",
          "image_id": image.id,
          "tags": tags_payload,
          "description": image.description
        }
      )

    logger.info(f"Image {image_id} processed successfully")
  except Exception as e:
    logger.exception(f"Failed to process message: {e}")

    if image_id:
      try:
        async with AsyncSessionLocal() as db:
          stmt = (
            select(ReportImage)
            .options(selectinload(ReportImage.report))
            .where(ReportImage.id == image_id)
          )
          result = await db.execute(stmt)
          image = result.scalar_one_or_none()
          if image:
            image.status = "failed"
            await db.commit()
      except Exception:
        pass
    raise e

async def run_worker():
  logger.info("Image worker started")
  
  while True:
    try:
      response = sqs.receive_message(
        QueueUrl=QUEUE_URL,
        MaxNumberOfMessages=5,
        WaitTimeSeconds=20
      )
      messages = response.get("Messages", [])
      for message in messages:
        await process_message(message)
        sqs.delete_message(
          QueueUrl=QUEUE_URL,
          ReceiptHandle=message["ReceiptHandle"]
        )
    except Exception as e:
      logger.exception(f"SQS polling error: {e}")
      await asyncio.sleep(5)

if __name__ == "__main__":
  asyncio.run(run_worker())