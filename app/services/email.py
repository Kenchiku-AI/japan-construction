from app.core.config import settings
import os
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email, To, Content

def send_invitation_email(email: str, company_name: str, invite_token: str) -> None:
  if not settings.SENDGRID_API_KEY:
    raise RuntimeError("SENDGRID_API_KEY environment variable is not set")

  invite_link = f"{settings.WEB_CLIENT_URL}/invitation?token={invite_token}"

  subject = f"You're invited to join {company_name}"
  content = f"""
  Hi there,

  You've been invited to join the company "{company_name}".
  Click the link below to accept the invitation:

  {invite_link}

  If you did not expect this invitation, you can safely ignore this email.

  Thanks,
  The {company_name} Team
  """

  message = Mail(
    from_email=settings.FROM_EMAIL,
    to_emails=email,
    subject=subject,
    plain_text_content=content
  )

  try:
    sg = SendGridAPIClient(settings.SENDGRID_API_KEY)
    response = sg.send(message)
    print(f"Sent invitation email to {email}, status code: {response.status_code}")
  except Exception as e:
    print(f"Error sending email to {email}: {e}")
    raise

def send_project_request_email(company_name: str, project_name: str, requested_by: str):
  if not settings.SENDGRID_API_KEY:
    raise RuntimeError("SENDGRID_API_KEY environment variable is not set")

  subject = f"New Project Request: {company_name}"
  body = f"""
  Company: {company_name}
  Project: {project_name}
  Requested by: {requested_by}
  """

def send_password_reset_email(email: str, reset_token: str) -> None:
  if not settings.SENDGRID_API_KEY:
    raise RuntimeError("SENDGRID_API_KEY environment variable is not set")

  reset_link = f"{settings.WEB_CLIENT_URL}/reset-password?token={reset_token}"

  subject = "Reset your password"

  content = f"""
  Hi there,

  We received a request to reset your password.

  Click the link below to set a new password:

  {reset_link}

  If you did not request this, you can safely ignore this email.

  This link will expire shortly for security reasons.

  Thanks,
  Support Team
  """

  message = Mail(
    from_email=settings.FROM_EMAIL,
    to_emails=email,
    subject=subject,
    plain_text_content=content
  )

  try:
    sg = SendGridAPIClient(settings.SENDGRID_API_KEY)
    response = sg.send(message)
    print(f"Sent password reset email to {email}, status code: {response.status_code}")
  except Exception as e:
    print(f"Error sending password reset email to {email}: {e}")
    raise