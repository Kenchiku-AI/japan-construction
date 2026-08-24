import json

import boto3

from app.core.config import settings


sqs = boto3.client(
  "sqs",
  region_name=settings.AWS_REGION,
)


def enqueue_form_job(
  form_job_id,
) -> None:
  sqs.send_message(
    QueueUrl=settings.SQS_FORM_QUEUE_URL,
    MessageBody=json.dumps(
      {
        "form_job_id": str(form_job_id),
      },
    ),
  )