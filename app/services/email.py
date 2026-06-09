from app.core.config import settings
import boto3
import logging

ses = boto3.client(
  "ses",
  region_name=settings.AWS_REGION,
)

logger = logging.getLogger(__name__)

EMAIL_LOGO = """
<svg
  xmlns="http://www.w3.org/2000/svg"
  width="50"
  height="50"
  viewBox="0 0 113.05766 144.49409"
>
  <g transform="translate(-48.980034,-46.83656)">
    <path
      fill="#000000"
      d="m 48.980034,151.83958 16.326678,11.51536 v -42.4839 l 22.767014,16.0755 0.03649,42.6884 16.701334,11.69572 0.0644,-65.54326 -39.318059,-27.632463 39.184029,-27.5 40.43992,28.2 0.0982,64.400003 16.47967,-11.61293 0.27799,-64.814271 -57.29578,-39.991179 -39.435208,27.880942 -0.25118,-27.766731 -16.075498,11.33 z"
    />
    <path
      fill="#000000"
      d="m 119.33715,115.65494 11.34288,-7.9 -0.0934,65.8 -11.27628,7.88711 z"
    />
    <path
      fill="#000000"
      transform="matrix(0.81784145,-0.57544362,0.81784145,0.57544362,0,0)"
      d="m -28.253254,142.33411 h 14.117453 v 14.11745 h -14.117453 z"
    />
  </g>
</svg>
"""

def _build_email_template(title: str, message: str, button_text: str, button_url: str) -> str:
  return f"""
<html>
  <body style="margin:0;padding:0;font-family:Arial,sans-serif;background-color:#FDFDFD;">
    <table width="100%" cellpadding="0" cellspacing="0" style="padding:40px;">
      <tr>
        <td align="center">

          <div style="margin-bottom:24px;">
            {EMAIL_LOGO}
          </div>

          <h2 style="color:#333;text-align:center;margin-top:0;">
            {title}
          </h2>

          <p style="color:#555;line-height:1.6;text-align:center;">
            {message}
          </p>

          <div style="text-align:center;margin:30px 0;">
            <a href="{button_url}"
                style="background-color:#6FB37A;color:#ffffff;padding:12px 24px;
                      text-decoration:none;font-weight:600;border-radius:10px;display:inline-block;">
              {button_text}
            </a>
          </div>

          <p style="color:#999;font-size:12px;text-align:center;">
            このメールに心当たりがない場合は、本メールを無視してください。
          </p>

        </td>
      </tr>
    </table>
  </body>
</html>
"""

def send_invitation_email(email: str, company_name: str, invite_token: str) -> None:
  invite_link = f"{settings.WEB_CLIENT_URL}/signup?invitationToken={invite_token}"

  subject = f"{company_name} への招待"

  message = f"""
{company_name} に参加するよう招待されています。<br/>
以下のボタンをクリックして、招待を承認してください。
"""

  html_body = _build_email_template(
    title="招待のお知らせ",
    message=message,
    button_text="招待を承認する",
    button_url=invite_link,
  )

  text_body = f"""
{company_name} に参加するよう招待されています。
以下のリンクから承認してください：
{invite_link}
"""

  try:
    ses.send_email(
      Source=settings.NO_REPLY_EMAIL,
      Destination={"ToAddresses": [email]},
      ReplyToAddresses=[settings.SUPPORT_EMAIL],
      Message={
        "Subject": {"Data": subject},
        "Body": {
          "Text": {"Data": text_body},
          "Html": {"Data": html_body},
        },
      },
    )
    logger.info(f"Sent invitation email to {email}")
  except Exception:
    logger.exception("Error sending email to %s", email)
    raise

def send_project_request_email(company_name: str, project_name: str, requested_by: str):
  subject = f"新規プロジェクトリクエスト: {company_name}"

  html_body = f"""
<html>
  <body style="font-family:Arial,sans-serif;">
    <h3>新規プロジェクトリクエスト</h3>
    <p><strong>会社名:</strong> {company_name}</p>
    <p><strong>プロジェクト名:</strong> {project_name}</p>
    <p><strong>依頼者:</strong> {requested_by}</p>
  </body>
</html>
"""

  text_body = f"""
新規プロジェクトリクエスト

会社名: {company_name}
プロジェクト名: {project_name}
依頼者: {requested_by}
"""

  try:
    ses.send_email(
      Source=settings.NO_REPLY_EMAIL,
      Destination={"ToAddresses": [settings.SUPPORT_EMAIL]},
      Message={
        "Subject": {"Data": subject},
        "Body": {
          "Text": {"Data": text_body},
          "Html": {"Data": html_body},
        },
      },
    )
  except Exception:
    logger.exception("Error sending project request email")
    raise


def send_password_reset_email(email: str, reset_token: str) -> None:
  reset_link = f"{settings.WEB_CLIENT_URL}/reset-password?token={reset_token}"

  subject = "パスワード再設定のご案内"

  message = """
パスワード再設定のリクエストを受け付けました。<br/>
以下のボタンをクリックして、新しいパスワードを設定してください。<br/><br/>
※ このリンクはセキュリティ上、一定時間後に無効になります。
"""

  html_body = _build_email_template(
    title="パスワード再設定",
    message=message,
    button_text="パスワードを再設定する",
    button_url=reset_link,
  )

  text_body = f"""
パスワード再設定のリクエストを受け付けました。
以下のリンクから設定してください：
{reset_link}

※ このリンクは一定時間後に無効になります。
"""

  try:
    ses.send_email(
      Source=settings.NO_REPLY_EMAIL,
      Destination={"ToAddresses": [email]},
      ReplyToAddresses=[settings.SUPPORT_EMAIL],
      Message={
        "Subject": {"Data": subject},
        "Body": {
          "Text": {"Data": text_body},
          "Html": {"Data": html_body},
        },
      },
    )
    logger.info(f"Sent password reset email to {email}")
  except Exception:
    logger.exception("Error sending email to %s", email)
    raise

def send_project_guest_access_email(email: str, project_name: str) -> None:
  subject = f"プロジェクト「{project_name}」へのアクセス権が付与されました"

  message = f"""
プロジェクト「{project_name}」へのゲストアクセス権が付与されました。<br/>
以下のボタンからログインして、プロジェクトをご確認ください。
"""

  login_url = f"{settings.WEB_CLIENT_URL}/login"

  html_body = _build_email_template(
    title="プロジェクトへのアクセス権付与",
    message=message,
    button_text="ログインする",
    button_url=login_url,
  )

  text_body = f"""
プロジェクト「{project_name}」へのゲストアクセス権が付与されました。
以下のリンクからログインしてご確認ください：
{login_url}
"""

  try:
    ses.send_email(
      Source=settings.NO_REPLY_EMAIL,
      Destination={"ToAddresses": [email]},
      ReplyToAddresses=[settings.SUPPORT_EMAIL],
      Message={
        "Subject": {"Data": subject},
        "Body": {
          "Text": {"Data": text_body},
          "Html": {"Data": html_body},
        },
      },
    )
    logger.info(f"Sent guest access email to {email}")
  except Exception:
    logger.exception("Error sending guest access email to %s", email)
    raise

def send_guest_invitation_email(email: str, project_name: str, set_password_token: str) -> None:
  set_password_link = f"{settings.WEB_CLIENT_URL}/reset-password?token={set_password_token}&newUser=true"

  subject = f"プロジェクト「{project_name}」への招待"

  message = f"""
プロジェクト「{project_name}」にゲストとして招待されました。<br/>
以下のボタンをクリックしてパスワードを設定し、メールアドレス（{email}）でご利用いただくアカウントを有効化してください。<br/><br/>
※ このリンクはセキュリティ上、24時間後に無効になります。
"""

  html_body = _build_email_template(
    title="プロジェクトへのご招待",
    message=message,
    button_text="パスワードを設定してはじめる",
    button_url=set_password_link,
  )

  text_body = f"""
プロジェクト「{project_name}」にゲストとして招待されました。
以下のリンクからパスワードを設定し、メールアドレス（{email}）でご利用いただくアカウントを有効化してください：

{set_password_link}

※ このリンクは24時間後に無効になります。
"""

  try:
    ses.send_email(
      Source=settings.NO_REPLY_EMAIL,
      Destination={"ToAddresses": [email]},
      ReplyToAddresses=[settings.SUPPORT_EMAIL],
      Message={
        "Subject": {"Data": subject},
        "Body": {
          "Text": {"Data": text_body},
          "Html": {"Data": html_body},
        },
      },
    )
    logger.info(f"Sent guest invitation email to {email}")
  except Exception:
    logger.exception("Error sending guest invitation email to %s", email)
    raise

def send_existing_user_invitation_email(email: str, company_name: str, invite_token: str) -> None:
  invite_link = f"{settings.WEB_CLIENT_URL}/accept-invitation?invitationToken={invite_token}"

  subject = f"{company_name} への招待"

  message = f"""
{company_name} に参加するよう招待されています。<br/>
以下のボタンをクリックして、招待を承認してください。
"""

  html_body = _build_email_template(
    title="招待のお知らせ",
    message=message,
    button_text="招待を承認する",
    button_url=invite_link,
  )

  text_body = f"""
{company_name} に参加するよう招待されています。
以下のリンクから承認してください：
{invite_link}
"""

  try:
    ses.send_email(
      Source=settings.NO_REPLY_EMAIL,
      Destination={"ToAddresses": [email]},
      ReplyToAddresses=[settings.SUPPORT_EMAIL],
      Message={
        "Subject": {"Data": subject},
        "Body": {
          "Text": {"Data": text_body},
          "Html": {"Data": html_body},
        },
      },
    )
    logger.info(f"Sent existing user invitation email to {email}")
  except Exception:
    logger.exception("Error sending existing user invitation email to %s", email)
    raise