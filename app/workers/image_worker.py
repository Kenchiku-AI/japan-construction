import asyncio
import json
import logging
import uuid

import boto3
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.db.models import Image
from app.db.session import AsyncSessionLocal
from app.services.images import process_image
from app.services.s3 import BUCKET_NAME, s3_client

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

    if "Records" not in body:
      return

    record = body["Records"][0]
    key = record["s3"]["object"]["key"]

    _, image_id = parse_s3_key(key)

    logger.info(f"Processing image {image_id}")

    async with AsyncSessionLocal() as db:
      stmt = (
        select(Image)
        .options(selectinload(Image.report))
        .where(Image.id == image_id)
      )

      result = await db.execute(stmt)
      image = result.scalar_one_or_none()

      if not image:
        logger.warning(f"Image not found: {image_id}")
        return

      if image.status != "pending":
        logger.info(
          f"Image already processed or in progress: {image_id}"
        )
        return

      image.status = "processing"
      await db.commit()

      image_url = s3_client.generate_presigned_url(
        ClientMethod="get_object",
        Params={
          "Bucket": BUCKET_NAME,
          "Key": key,
        },
        ExpiresIn=600,
      )

      await process_image(
        image=image,
        image_url=image_url,
        db=db,
      )

      image.status = "completed"
      await db.commit()

    logger.info(f"Image {image_id} processed successfully")

  except Exception as e:
    logger.exception(f"Failed to process image: {e}")

    if image_id:
      try:
        async with AsyncSessionLocal() as db:
          stmt = (
            select(Image)
            .where(Image.id == image_id)
          )

          result = await db.execute(stmt)
          image = result.scalar_one_or_none()

          if image:
            image.status = "failed"
            await db.commit()

      except Exception:
        logger.exception(
          f"Failed to mark image {image_id} as failed"
        )

    raise


async def run_worker():
  logger.info("Image worker started")

  while True:
    try:
      response = sqs.receive_message(
        QueueUrl=QUEUE_URL,
        MaxNumberOfMessages=5,
        WaitTimeSeconds=20,
      )

      messages = response.get("Messages", [])

      for message in messages:
        await process_message(message)

        sqs.delete_message(
          QueueUrl=QUEUE_URL,
          ReceiptHandle=message["ReceiptHandle"],
        )

    except Exception as e:
      logger.exception(f"SQS polling error: {e}")
      await asyncio.sleep(5)


if __name__ == "__main__":
  asyncio.run(run_worker())