from app.core.config import settings
from urllib.parse import urlencode
import boto3
import logging

ses = boto3.client(
  "ses",
  region_name=settings.AWS_REGION
)

logger = logging.getLogger(__name__)

EMAIL_LOGO_URL = "https://www.kenchiku.ai/logo.png"

def _build_email_template(title: str, message: str, button_text: str, button_url: str) -> str:
  return f"""
<html>
  <body style="margin:0;padding:0;font-family:Arial,sans-serif;background-color:#FDFDFD;">
    <table width="100%" cellpadding="0" cellspacing="0" style="padding:40px;">
      <tr>
        <td align="center">

          <div style="margin-bottom:24px;">
            <img
              src="{EMAIL_LOGO_URL}"
              alt="Kenchiku"
              width="32"
              height="32"
              style="display:block;margin:0 auto;"
            />
          </div>

          <h2 style="color:#333;text-align:center;margin-top:0;">
            {title}
          </h2>

          <p style="color:#555;line-height:1.6;text-align:center;">
            {message}
          </p>

          <div style="text-align:center;margin:30px 0;">
            <a href="{button_url}"
                style="background-color:#4d9458;color:#ffffff;padding:12px 24px;
                      text-decoration:none;font-weight:600;border-radius:10px;display:inline-block;
                      width:360px;box-sizing:border-box;text-align:center;">
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
  params = urlencode({
    "invitationToken": invite_token,
    "email": email,
  })

  invite_link = f"{settings.WEB_CLIENT_URL}/signup?{params}"

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

def send_password_reset_email(email: str, reset_token: str) -> None:
  reset_link = f"{settings.WEB_CLIENT_URL}/new-password?token={reset_token}"

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

def send_project_guest_access_email(email: str, project_name: str, company_name: str) -> None:
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

def send_guest_invitation_email(email: str, project_name: str, company_name: str, set_password_token: str) -> None:
  set_password_link = f"{settings.WEB_CLIENT_URL}/new-password?token={set_password_token}&newUser=true"

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

def send_line_group_linked_email(email: str, company_name: str, project_name: str) -> None:
  subject = f"LINEグループが「{project_name}」に連携されました — {company_name}"

  message = f"""
{company_name} のプロジェクト「{project_name}」がLINEグループと連携されました。<br/>
今後、このLINEグループから送信されたメッセージは自動的にプロジェクトの報告書に反映されます。
"""

  html_body = _build_email_template(
    title="LINEグループ連携完了",
    message=message,
    button_text="建築AIを開く",
    button_url=settings.WEB_CLIENT_URL,
  )

  text_body = f"""
{company_name} のプロジェクト「{project_name}」がLINEグループと連携されました。
今後、このLINEグループから送信されたメッセージは自動的にプロジェクトの報告書に反映されます。

{settings.WEB_CLIENT_URL}
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
    logger.info(f"Sent LINE group linked email to {email}")
  except Exception:
    logger.exception("Error sending LINE group linked email to %s", email)
    raise

def send_company_created_email(email: str, company_name: str, invite_token: str) -> None:
  params = urlencode({
    "invitationToken": invite_token,
    "email": email,
  })

  signup_link = f"{settings.WEB_CLIENT_URL}/signup?{params}"

  subject = "Kenchiku AIへようこそ"

  message = f"""
Kenchiku AIの無料トライアルにお申し込みいただきありがとうございます。<br/>
{company_name}のアカウントへの登録を完了するには、以下のボタンをクリックしてください。
"""

  html_body = _build_email_template(
    title="Kenchiku AIへようこそ",
    message=message,
    button_text="アカウント登録を完了する",
    button_url=signup_link,
  )

  text_body = f"""
Kenchiku AIの無料トライアルにお申し込みいただきありがとうございます。

{company_name}のアカウントへの登録を完了するには、以下のリンクをクリックしてください。
{signup_link}
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
    logger.info(f"Sent Kenchiku AI welcome email to {email}")
  except Exception:
    logger.exception("Error sending email to %s", email)
    raise

def send_company_invitation_email(
  email: str,
  company_name: str,
  invite_token: str,
) -> None:
  params = urlencode({
    "invitationToken": invite_token,
    "email": email,
  })

  signup_link = f"{settings.WEB_CLIENT_URL}/signup?{params}"

  subject = f"{company_name}からKenchiku AIへの招待"

  message = f"""
{company_name}のメンバーからKenchiku AIへの招待が届いています。<br/>
以下のボタンをクリックして、アカウント登録を完了してください。
"""

  html_body = _build_email_template(
    title="Kenchiku AIへの招待が届いています",
    message=message,
    button_text="アカウント登録を完了する",
    button_url=signup_link,
  )

  text_body = f"""
{company_name}のメンバーからKenchiku AIへの招待が届いています。

以下のリンクからアカウント登録を完了してください。
{signup_link}
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
    logger.info(f"Sent company invitation email to {email}")
  except Exception:
    logger.exception("Error sending email to %s", email)
    raise

def send_company_created_admin_email(
  email: str,
  company_id: str,
  company_name: str,
  manager_email: str | None,
) -> None:
  company_url = f"{settings.WEB_CLIENT_URL}/companies/{company_id}"

  subject = f"新しい会社が作成されました — {company_name}"

  message = f"""新しい会社が作成されました。<br/><br/>

会社名：{company_name}<br/>
管理者メール：{manager_email or "未設定"}<br/>
課金：免除（有効）<br/><br/>

以下のボタンから会社情報をご確認ください。
"""

  html_body = _build_email_template(
    title="新しい会社が作成されました",
    message=message,
    button_text="会社を確認する",
    button_url=company_url,
  )

  text_body = f"""
新しい会社が作成されました。

会社名: {company_name}
管理者メール: {manager_email or "未設定"}
課金: 免除（有効）

確認:
{company_url}
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

    logger.info(
      "Sent company creation notification email to %s",
      email,
    )

  except Exception:
    logger.exception(
      "Error sending company creation notification email to %s",
      email,
    )
    raise