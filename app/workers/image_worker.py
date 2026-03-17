import boto3
import json
import asyncio
import uuid
import logging
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import insert

from app.db.session import AsyncSessionLocal
from app.db.models import ReportImage, ReportImageTag, ReportImageTagLink
from app.services.openai import get_image_tags
from app.services.s3 import s3_client, BUCKET_NAME
from app.services.reports import get_company_id
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
      stmt = select(ReportImage).where(ReportImage.id == image_id)
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

      tags = await get_image_tags(image_url)

      company_id = await get_company_id(
        parent_type=image.report.parent_type,
        parent_id=image.report.parent_id,
        db=db,
      )

      for tag_data in tags:
        insert_stmt = (
          insert(ReportImageTag)
          .values(
            company_id=company_id,
            name=tag_data["name"],
            description=tag_data.get("description")
          )
          .on_conflict_do_nothing(
            index_elements=["company_id", "name"]
          )
          .returning(ReportImageTag.id)
        )

        result = await db.execute(insert_stmt)
        tag_id = result.scalar_one_or_none()

        if tag_id:
          tag = await db.get(ReportImageTag, tag_id)
        else:
          stmt = select(ReportImageTag).where(
            ReportImageTag.company_id == company_id,
            ReportImageTag.name == tag_data["name"],
          )
          tag = (await db.execute(stmt)).scalar_one()

        link_insert = (
          insert(ReportImageTagLink)
          .values(
            report_image_id=image.id,
            tag_id=tag.id
          )
          .on_conflict_do_nothing()
        )

        await db.execute(link_insert)

      image.status = "completed"
      await db.commit()

    logger.info(f"Image {image_id} processed successfully")
  except Exception as e:
    logger.exception(f"Failed to process message: {e}")

    if image_id:
      try:
        async with AsyncSessionLocal() as db:
          stmt = select(ReportImage).where(ReportImage.id == image_id)
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