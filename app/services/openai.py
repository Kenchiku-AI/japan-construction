from __future__ import annotations
from openai import AsyncOpenAI
from typing import List, Iterable
import json
import re
import logging

from app.core.config import settings
from app.db.models.report import ReportField, ReportImageTag
from app.db.models.line_conversation import LineConversation
from app.db.models.line_message import LineMessage

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

async def extract_conversation_items(
  message_text: str,
  conversation: LineConversation,
  recent_messages: list[LineMessage],
  item_types: list[ConversationItemType],
  existing_items: list[ConversationItem],
):
  history_lines = []

  for message in recent_messages:
    line = f'- "{message.text}"'

    if message.conversation_item_links:
      extracted = "\n".join(
        (
          f'    - {link.conversation_item.item_type.name}: '
          f'{link.conversation_item.name}'
        )
        for link in message.conversation_item_links
      )

      line += f"\n  ※抽出済み:\n{extracted}"

    history_lines.append(line)

  history_block = "\n".join(history_lines) or "なし"

  item_types_block = "\n".join(
    f"""
- id: {item.id}
  name: {item.name}
  description: {item.description or ""}
""".strip()
    for item in item_types
  ) or "なし"

  existing_items_block = "\n".join(
    f"""
- id: {item.id}
  type_id: {item.conversation_item_type_id}
  name: {item.name}
  description: {item.description or ""}
""".strip()
    for item in existing_items
  ) or "なし"

  prompt = f"""
あなたは建設会社向けAIアシスタントです。

LINEグループの会話から、
設定済みの「トークから抽出する情報」を管理してください。

あなたは必ず次のいずれかを判断してください。

- 新しい情報を作成する
- 既存の情報を更新する
- 何もしない

## プロジェクト

{conversation.project.name if conversation.project else "なし"}

## LINEグループ

{conversation.name}

## トークから抽出する情報

以下は、このLINEグループで抽出対象として設定されている情報です。

それぞれ

- name
- description

をよく読み、その情報に該当する内容だけを抽出してください。

{item_types_block}

description は、その情報として何を保存したいかを説明しています。

必ず description を参考にしてください。

## 現在保存されている情報

{existing_items_block}

## ルール

## 重複防止

最近のトークで

※抽出済み:

と表示されているメッセージは、
そのメッセージからすでに以下の情報が抽出されています。

同じ情報を重複して create しないでください。

既存の情報に対する追加情報や変更であれば update してください。

新しい対象や別の内容であれば、新しい create を行ってください。

- 新しい対象が追加された
- 新しい情報が判明した
- 状況が変わった
- 進捗があった
- 内容が修正された

場合は、必要に応じて update または新しい create を行ってください。

### create

以下の場合は create してください。

- 新しい情報
- まだ存在しない情報
- 新しい対象
- 新しい設備
- 新しい場所
- 新しい担当
- 新しい内容

### update

以下の場合は update してください。

- 同じ対象について追加情報があった
- 状況が変わった
- 詳細が増えた
- 完了した
- 進捗があった

同じ情報であれば、新しく create せず update を行ってください。

すでに「現在保存されている情報」に存在する対象については、
新しく create するより update を優先してください。

更新する場合は description 全体を返してください。

追加部分だけではなく、
保存後の description 全体を返してください。

### none

抽出対象に関係ない会話は何もしません。

推測は禁止です。

## 最近のトーク

{history_block}

## 最新メッセージ

"{message_text}"

## 出力

新規作成

[
  {{
    "action": "create",
    "conversation_item_type_id": "<type id>",
    "name": "<名前>",
    "description": "<内容>"
  }}
]

更新

[
  {{
    "action": "update",
    "conversation_item_id": "<item id>",
    "name": "<name（更新後）>",
    "description": "<更新後全文>"
  }}
]

何もしない

[]

JSONのみ返してください。
""".strip()

  response = await client.responses.create(
    model="gpt-4.1-mini",
    input=[
      {
        "role": "user",
        "content": prompt,
      }
    ],
    temperature=0,
  )

  try:
    return safe_json_loads(response.output_text)
  except Exception:
    return []

def safe_json_loads(text: str):
  match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
  if not match:
    raise ValueError(f"No JSON found in: {text}")

  return json.loads(match.group())
