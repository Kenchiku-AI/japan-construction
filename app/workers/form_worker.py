import asyncio
import json

import boto3

from app.services.forms.service import FormJobService
from app.services.forms.storage import FormStorage
from app.db.session import async_session


sqs = boto3.client("sqs")


async def process_message(
  message: dict,
) -> None:

  body = json.loads(
    message["Body"]
  )

  form_job_id = body["form_job_id"]

  async with async_session() as db:

    storage = FormStorage(
      bucket_name="YOUR_BUCKET_NAME",
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
      QueueUrl="YOUR_QUEUE_URL",
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
          QueueUrl="YOUR_QUEUE_URL",
          ReceiptHandle=message["ReceiptHandle"],
        )

      except Exception:
        # Leave message in queue so SQS
        # retry / DLQ handling can occur.
        pass


if __name__ == "__main__":
  asyncio.run(main())