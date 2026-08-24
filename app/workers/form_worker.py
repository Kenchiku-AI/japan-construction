import asyncio
import json

import boto3

from app.core.config import settings
from app.services.forms.service import FormJobService
from app.services.forms.storage import FormStorage
from app.db.session import async_session


sqs = boto3.client(
  "sqs",
  region_name=settings.AWS_REGION,
)

QUEUE_URL = settings.SQS_FORM_QUEUE_URL


async def process_message(
  message: dict,
) -> None:

  body = json.loads(
    message["Body"]
  )

  form_job_id = body["form_job_id"]

  async with async_session() as db:

    storage = FormStorage(
      bucket_name=settings.S3_BUCKET_NAME,
    )

    service = FormJobService(
      db=db,
      storage=storage,
    )

    await service.process(
      form_job_id=form_job_id,
    )


async def main():
  while True:
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
          ReceiptHandle=message["ReceiptHandle"],
        )

      except Exception:
        pass


if __name__ == "__main__":
  asyncio.run(main())