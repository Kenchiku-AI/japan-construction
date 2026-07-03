from __future__ import annotations
from openai import AsyncOpenAI
from typing import List, Iterable
import json
import re
import logging

from app.core.config import settings
from app.db.models.report import ReportField, ReportImageTag

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

logger = logging.getLogger(__name__)

async def transcribe_and_extract_json(
  speech_text: str,
  fields: List[ReportField],
  output_language: str,
) -> dict:
  field_lines = [
    f'- id: "{f.id}"\n  name: "{f.name}"\n  description: "{f.description}"'
    for f in fields
  ]
  field_block = "\n".join(field_lines)

  prompt = f"""
あなたは厳密なJSON抽出エンジンです。

必ず以下の形式のJSONオブジェクトのみを返してください。

{{
  "<field_id>": "<音声内容を整形した値>"
}}

厳守事項:
- 有効なJSONのみを返すこと
- キーには必ず field id を使用すること
- 値には抽出された内容のみを含めること
- field名や説明文を返さないこと
- fieldのメタデータを返さないこと
- 音声内に存在しない項目は含めないこと
- 該当する項目がない場合は {{ "field_values": {{}} }} を返すこと

文章整形ルール:
- 会話的な表現を、工事報告書に適した正式な文章へ変換すること
- 元の意味は変えないこと
- 不要な主語や代名詞は省略すること
- 可能な限り客観的・記録的な表現を使用すること
- 新しい情報を追加しないこと
- 推測しないこと

変換例:

音声:
「屋根終わりました」

出力:
「屋根工事完了」

音声:
「今日は雨でした」

出力:
「終日降雨を確認」

音声:
「配線やりました」

出力:
「電気配線施工完了」

出力言語: {output_language}

対象フィールド:
{field_block}
""".strip()

  try:
    response = await client.responses.create(
      model="gpt-4.1-mini",
      input=[
        {"role": "system", "content": prompt},
        {"role": "user", "content": speech_text},
      ],
      temperature=0
    )
  except Exception as e:
    print("Error:", e)

  content = response.output_text.strip()

  try:
    parsed = json.loads(content)
  except json.JSONDecodeError:
    raise ValueError("Failed to parse normalized JSON from speech")

  return parsed

async def get_image_tags_and_description(
  image_url: str,
  tags: Iterable[ReportImageTag],
  include_description: bool
) -> dict:
  tag_list = [
    {
      "id": str(tag.id),
      "name": tag.name,
      "description": tag.description
    }
    for tag in tags
  ]

  tag_list_json = json.dumps(tag_list, indent=2)

  if include_description:
    description_instruction = """
また、工事報告書に適した簡潔で専門的な説明文を
1〜2文で含めてください。

厳守事項:
- 説明文は必ず日本語で記述すること
- 画像に実際に写っている内容のみ記述すること
- 「この画像は〜」「写真には〜」「画像には〜」などの
  前置き表現は使用しないこと
- 作業内容、使用機械、資材、安全状況など、
  視認可能な事実のみ記述すること
- 推測や補完をしないこと
- 曖昧な表現（「いくつかの」「様々な」「など」）は使用しないこと
- 建設・工事記録に適した簡潔で正式な表現を使用すること

重要:
- 画像に工事・建設に関連する内容が一切含まれていない場合は、
  "description" は空文字列（""）を返すこと
- 「工事に関連する内容は写っていない」などの説明文は絶対に出力しないこと

良い例:
- 「鉄筋コンクリート壁に沿って鉄筋組立作業を実施」
- 「油圧ショベルによる掘削作業が進行中」
- 「足場上で外装パネルの設置作業を実施」
"""
    json_format = """
以下の形式の有効なJSONのみを返してください:

{
  "tags": ["tag_id_1", "tag_id_2"],
  "description": "日本語による簡潔な工事説明"
}
"""
  else:
    description_instruction = ""
    json_format = """
以下の形式の有効なJSONのみを返してください:

{
  "tags": ["tag_id_1", "tag_id_2"]
}
"""

  response = await client.responses.create(
    model="gpt-4.1-mini",
    temperature=0,
    input=[
      {
        "role": "system",
        "content": "You are a construction site image classifier."
      },
      {
        "role": "user",
        "content": [
          {
            "type": "input_text",
            "text": f"""
以下は、この画像に適用される可能性のあるタグ一覧です。

{tag_list_json}

{json_format}

画像に明確に写っている内容に基づいて、
該当するタグをすべて選択してください。

厳守事項:
- 画像で明確に確認できる内容のみ選択すること
- 推測や補完をしないこと
- 視認できない内容は選択しないこと
- 該当するタグがない場合は空配列を返すこと
- 必ずJSONのみを返すこと
- JSON以外の説明や補足は一切含めないこと

{description_instruction}
"""
          },
          {
            "type": "input_image",
            "image_url": image_url
          }
        ]
      }
    ]
  )

  return safe_json_loads(response.output_text)

async def filter_reports_by_context(
  message_text: str,
  reports: list[Report],
  project_map: dict,
) -> list[Report]:
  candidates = "\n".join(
    f'- id: "{r.id}"'
    f' | report name: "{r.name}"'
    f' | project name: "{project_map[r.parent_id].name if r.parent_id in project_map else ""}"'
    f' | project description: "{project_map[r.parent_id].description if r.parent_id in project_map else ""}"'
    f' | fields: {", ".join(f.description for f in r.fields)}'
    for r in reports
  )

  prompt = f"""
あなたは建設現場の報告書管理システムです。
以下のメッセージが関係する可能性のある報告書をすべて選んでください。
報告書にはそれぞれフィールドがあります。メッセージの内容がどのフィールドに当てはまるかを考慮して選択してください。

メッセージ:
{message_text}

候補の報告書（id | 報告書名 | フィールド説明）:
{candidates}

必ず以下の形式のJSONのみを返してください:
{{"report_ids": ["<id1>", "<id2>"]}}

どの報告書にも該当しない場合は:
{{"report_ids": []}}
""".strip()

  response = await client.responses.create(
    model="gpt-4.1-mini",
    input=[{"role": "user", "content": prompt}],
    temperature=0,
  )

  try:
    parsed = json.loads(response.output_text.strip())
  except json.JSONDecodeError:
    return []

  report_ids = parsed.get("report_ids", [])
  return [r for r in reports if str(r.id) in report_ids]

async def extract_work_item(
  message_text: str,
  project: Project,
  sender: User,
  recent_messages: list[LineMessage],
) -> dict | None:
  history = "\n".join(
    f'- "{m.text}"{"  ※作業項目作成済み" if m.triggered_work_item else ""}'
    for m in recent_messages
  )

  prompt = f"""
あなたは建設現場のタスク管理システムです。
以下のメッセージと会話履歴を読み、新しい作業項目（WorkItem）を作成すべきか判断してください。

作業項目を作成すべき場合のみ、JSONを返してください。
作業の依頼・指示・タスクの割り当てを示すメッセージのみ対象とします。
単なる報告・状況共有・雑談は対象外です。
※作業項目作成済み と記載されたメッセージはすでに処理済みです。再度作業項目を作成しないでください。

プロジェクト: {project.name}
送信者: {sender.first_name} {sender.last_name}

直近の会話履歴:
{history}

最新メッセージ:
"{message_text}"

作業項目を作成すべき場合は以下の形式のJSONのみを返してください:
{{
  "name": "<作業名（簡潔に）>",
  "description": "<詳細説明（任意）>"
}}

作業項目を作成すべきでない場合は以下を返してください:
{{"create": false}}
""".strip()

  response = await client.responses.create(
    model="gpt-4.1-mini",
    input=[{"role": "user", "content": prompt}],
    temperature=0,
  )

  logger.info(
    "Work item OpenAI response: %s",
    response.output_text,
  )

  try:
    parsed = json.loads(response.output_text.strip())
  except json.JSONDecodeError:
    return None

  if not parsed or parsed.get("create") is False:
    return None

  return parsed

def safe_json_loads(text: str):
  match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
  if not match:
    raise ValueError(f"No JSON found in: {text}")

  return json.loads(match.group())
