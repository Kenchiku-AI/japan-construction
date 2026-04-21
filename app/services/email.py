from app.core.config import settings
import boto3
import logging

ses = boto3.client(
  "ses",
  region_name=settings.AWS_REGION,
  aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
  aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
)

logger = logging.getLogger(__name__)

def send_invitation_email(email: str, company_name: str, invite_token: str) -> None:
  invite_link = f"{settings.WEB_CLIENT_URL}/signup?invitationToken={invite_token}"

  subject = f"You're invited to join {company_name}"
  body = f"""
Hi there,

You've been invited to join the company "{company_name}".
Click the link below to accept the invitation:

{invite_link}

If you did not expect this invitation, you can safely ignore this email.
"""

  try:
    ses.send_email(
      Source=settings.NO_REPLY_EMAIL,
      Destination={"ToAddresses": [email]},
      ReplyToAddresses=[settings.SUPPORT_EMAIL],
      Message={
        "Subject": {"Data": subject},
        "Body": {
          "Text": {"Data": body}
        },
      },
    )

    logger.info(f"Sent invitation email to {email}")
  except Exception as e:
    logger.exception("Error sending email to %s", email)
    raise

def send_project_request_email(company_name: str, project_name: str, requested_by: str):
    subject = f"New Project Request: {company_name}"

    body = f"""
Company: {company_name}
Project: {project_name}
Requested by: {requested_by}
"""

    try:
      ses.send_email(
        Source=settings.NO_REPLY_EMAIL,
        Destination={"ToAddresses": [settings.SUPPORT_EMAIL]},
        Message={
          "Subject": {"Data": subject},
          "Body": {"Text": {"Data": body}},
        },
      )
    except Exception as e:
      logger.exception("Error sending email to %s", email)
      raise

def send_password_reset_email(email: str, reset_token: str) -> None:
  reset_link = f"{settings.WEB_CLIENT_URL}/reset-password?token={reset_token}"

  subject = "Reset your password"

  body = f"""
Hi there,

We received a request to reset your password.

Click the link below to set a new password:

{reset_link}

If you did not request this, you can safely ignore this email.

This link will expire shortly for security reasons.
"""

  try:
    ses.send_email(
      Source=settings.NO_REPLY_EMAIL,
      Destination={"ToAddresses": [email]},
      ReplyToAddresses=[settings.SUPPORT_EMAIL],
      Message={
        "Subject": {"Data": subject},
        "Body": {
          "Text": {"Data": body}
        },
      },
    )

    logger.info(f"Sent password reset email to {email}")
  except Exception as e:
    logger.exception("Error sending email to %s", email)
    raise