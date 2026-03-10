import boto3
import json
import asyncio
import uuid
import logging
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import insert

from app.db.session import async_session
from app.db.models import ReportImage, ReportImageTag, ReportImageTagLink
from app.services.openai import get_image_tags
from app.services.s3 import s3_client, BUCKET_NAME
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
  try:
    body = json.loads(message["Body"])
    record = body["Records"][0]
    bucket = record["s3"]["bucket"]["name"]
    key = record["s3"]["object"]["key"]

    report_id, image_id = parse_s3_key(key)

    image_url = s3_client.generate_presigned_url(
      ClientMethod="get_object",
      Params={"Bucket": BUCKET_NAME, "Key": key},
      ExpiresIn=3600,
    )
    logger.info(f"Processing image {image_id}")

    async with async_session() as db:
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

    try:
        tags = await get_image_tags(image_url)
      except Exception as e:
        async with async_session() as db:
          image.status = "failed"
          await db.commit()
        logger.exception(f"AI tagging failed for image {image_id}: {e}")
        return

    async with async_session() as db:
      stmt = select(ReportImage).where(ReportImage.id == image_id)
      result = await db.execute(stmt)
      image = result.scalar_one()

      for tag_data in tags:
        stmt_tag = select(ReportImageTag).where(
          ReportImageTag.name == tag_data["name"],
          ReportImageTag.company_id == image.report.company_id
        )
        existing_tag = (await db.execute(stmt_tag)).scalar_one_or_none()

        if existing_tag:
          tag = existing_tag
        else:
          tag = ReportImageTag(
            name=tag_data["name"],
            description=tag_data.get("description", ""),
            company_id=image.report.company_id
          )
          db.add(tag)
          await db.flush()

        stmt_link = select(ReportImageTagLink).where(
          ReportImageTagLink.report_image_id == image.id,
          ReportImageTagLink.tag_id == tag.id
        )
        existing_link = (await db.execute(stmt_link)).scalar_one_or_none()

        if not existing_link:
          link = ReportImageTagLink(
            report_image_id=image.id,
            tag_id=tag.id
          )
          db.add(link)

      image.status = "completed"
      await db.commit()

    logger.info(f"Image {image_id} processed successfully")
  except Exception as e:
    logger.exception(f"Failed to process message: {e}")

    try:
      async with async_session() as db:
        stmt = select(ReportImage).where(ReportImage.id == image_id)
        result = await db.execute(stmt)
        image = result.scalar_one_or_none()

        if image:
          image.status = "failed"
          await db.commit()
    except Exception:
        pass

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