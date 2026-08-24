import asyncio
import json
import logging

import boto3

from app.core.config import settings
from app.db.session import async_session
from app.services.forms.service import FormJobService
from app.services.forms.storage import FormStorage
from app.services.s3 import BUCKET_NAME


logging.basicConfig(level=logging.INFO)

logger = logging.getLogger(__name__)


QUEUE_URL = settings.SQS_FORM_QUEUE_URL

sqs = boto3.client(
  "sqs",
  region_name=settings.AWS_REGION,
)


async def process_message(
  message: dict,
) -> None:
  body = json.loads(
    message["Body"],
  )

  form_job_id = body["form_job_id"]

  async with async_session() as db:
    storage = FormStorage(
      bucket_name=BUCKET_NAME,
    )

    service = FormJobService(
      db=db,
      storage=storage,
    )

    await service.process(
      form_job_id=form_job_id,
    )


async def run_worker():
  logger.info(
    "Form worker started",
  )

  while True:
    try:
      response = sqs.receive_message(
        QueueUrl=QUEUE_URL,
        MaxNumberOfMessages=1,
        WaitTimeSeconds=20,
      )

      messages = response.get(
        "Messages",
        [],
      )

      for message in messages:
        try:
          await process_message(
            message,
          )

          sqs.delete_message(
            QueueUrl=QUEUE_URL,
            ReceiptHandle=message[
              "ReceiptHandle"
            ],
          )

        except Exception:
          logger.exception(
            "Failed to process form job message",
          )

    except Exception:
      logger.exception(
        "Form worker polling error",
      )

      await asyncio.sleep(5)


if __name__ == "__main__":
  asyncio.run(
    run_worker(),
  )