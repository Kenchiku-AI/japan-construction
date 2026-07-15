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

async def extract_action_item(
  message_text: str,
  project: Project,
  recent_messages: list[LineMessage],
  recent_action_items: list[ActionItem],
) -> dict | None:

  history = "\n".join(
    f'- "{m.text}"{"  ※作業項目作成済み(id: " + str(m.triggered_action_item_id) + ")" if m.triggered_action_item_id else ""}'
    for m in recent_messages
  )

  action_items_block = "\n".join(
    f'- id: "{w.id}" | name: "{w.name}" | description: "{w.description or ""}"'
    for w in recent_action_items
  ) if recent_action_items else "なし"

  prompt = f"""
あなたは建設会社のプロジェクト管理アシスタントです。

あなたの仕事は、LINEグループの会話を読み、
プロジェクトのアクション項目（Action Item）を最新の状態に保つことです。

あなたは必ず次のいずれかを実行してください。

1. 新しいアクション項目を作成する
2. 既存のアクション項目を更新する
3. 何もしない

## アクション項目とは

アクション項目とは、プロジェクトを進めるために誰かが対応・実施・確認・判断・連絡・手配・変更・調査などの行動を行う必要がある事項です。

作業の規模は問いません。

施工だけでなく、設計・事務・発注・連絡・確認・調整などもアクション項目になります。

アクション項目は、後から担当者が見返して「何をすべきか」が分かる内容である必要があります。

施工に限らず、プロジェクトを前に進めるために誰かが行動する必要がある内容であれば、アクション項目として扱ってください。

以下はすべてアクション項目になります。

- 図面を更新する
- 屋根を修理する
- エアコンを設置する
- 資材を発注する
- 業者へ連絡する
- 施工方法を確認する
- 現場を調査する
- 建築確認申請を提出する
- 見積もりを作成する
- 工程を変更する
- お客様へ確認する
- 部品を交換する
- 足場を設置する
- 不具合を確認する
- 写真を撮り直す
- 現場を清掃する

## 新しいアクション項目を作成する

以下のような内容は、新しいアクション項目として作成してください。

- 作業の依頼
- 指示
- 対応依頼
- 修正依頼
- 調査依頼
- 点検依頼
- 確認依頼
- 連絡依頼
- 発注依頼
- 誰かが今後実施すべき内容
- 新たな対応事項
- やるべきこと
- 解決すべき問題

命令文だけでなく、以下のような表現も対象になります。

- 「〜してください」
- 「〜お願いします」
- 「〜する必要があります」
- 「〜した方がいい」
- 「〜しましょう」
- 「〜を修正」
- 「〜を追加」
- 「〜を変更」
- 「〜を確認」
- 「〜を手配」
- 「〜が必要」
- 「〜しないといけない」
- 「〜しておいて」

新しい対応事項として管理した方がよい内容であれば、新しいアクション項目を作成してください。

迷った場合は、新しいアクション項目を作成してください。

アクション項目を見逃すことは、不要なアクション項目を1件作成することより望ましくありません。

## 既存のアクション項目を更新する

以下の場合は、新しいアクション項目を作成せず、既存のアクション項目を更新してください。

- 作業の進捗
- 対応状況
- 作業内容の詳細
- スケジュール
- 日程変更
- 空き状況
- 担当者情報
- 補足情報
- 既存アクション項目に関する追加情報

現在のアクション項目には status が含まれています。

status の意味

- new：未着手
- in_progress：対応中
- completed：完了

更新対象は、「現在のアクション項目」の中から最も関連性の高い action_item_id を選択してください。

原則として new または in_progress のアクション項目を更新してください。

completed のアクション項目は更新しないでください。

ただし、完了済みの作業を再度実施することが明確な場合は、completed を更新するのではなく、新しいアクション項目を作成してください。

同じ内容についての追加情報であれば、新規作成ではなく更新してください。

スケジュールや空き状況などは description に追記してください。

scheduled_date などの日付フィールドは設定せず、自然な文章として description に記載してください。

例

- 「田中さんは来週水曜以降対応可能」
- 「資材は来週月曜日に搬入予定」
- 「業者から金曜日に完了予定との連絡あり」

description は既存の内容を踏まえた更新後の全文を返してください。

追加する文章だけではなく、更新後に保存すべき description 全体を返してください。

## 何もしない

以下の場合は何もしません。

- 雑談
- 挨拶
- 単なる状況報告
- リアクションのみ
- プロジェクト管理に関係ない内容

また、会話履歴で

※アクション項目作成済み(action_item_id: xxx)

と表示されているメッセージについては、そのメッセージから重複して新しいアクション項目を作成しないでください。

ただし、その後の会話で新しい依頼や新しい対応事項が発生した場合は、新しいアクション項目を作成して構いません。

## プロジェクト

{project.name}

## 最近の会話

{history}

## 現在のアクション項目

{action_items_block}

各アクション項目は次の形式です。

id | status | name | description

## 最新メッセージ

"{message_text}"

## 出力

新しいアクション項目を作成する場合

{{
  "action": "create",
  "name": "<簡潔で分かりやすいアクション項目名>",
  "description": "<必要に応じて詳細説明>"
}}

既存のアクション項目を更新する場合

{{
  "action": "update",
  "action_item_id": "<更新対象ID>",
  "description": "<更新後のdescription全文>"
}}

何もしない場合

{{
  "action": "none"
}}

JSON以外は一切出力しないでください。
""".strip()

  response = await client.responses.create(
    model="gpt-4.1-mini",
    input=[{"role": "user", "content": prompt}],
    temperature=0,
  )

  try:
    parsed = json.loads(response.output_text.strip())
  except json.JSONDecodeError:
    return None

  if not parsed or parsed.get("action") == "none":
    return None

  return parsed

async def extract_conversation_items(
  message_text: str,
  project: Project,
  item_types: list[ConversationItemType],
  existing_items: list[ConversationItem],
):

  item_types_block = "\n".join(
    f"""
- id: {item.id}
  name: {item.name}
  description: {item.description or ""}
"""
    for item in item_types
  )


  existing_items_block = "\n".join(
    f"""
- id: {item.id}
  type_id: {item.conversation_item_type_id}
  name: {item.name}
  description: {item.description or ""}
"""
    for item in existing_items
  )


  prompt = f"""
あなたは建設会社のプロジェクト管理AIです。

LINE会話から、管理対象の情報（Conversation Item）を抽出してください。

## 管理対象項目

{item_types_block}


## ルール

- 会話から新しい情報が取得できる場合のみ作成してください
- 既存項目と同じ情報の場合は更新してください
- 関係ない会話は何もしません
- 推測は禁止です


## 現在保存されている項目

{existing_items_block}


## 最新メッセージ

"{message_text}"


## 出力

作成:

[
{{
 "action":"create",
 "conversation_item_type_id":"<type id>",
 "name":"<項目名>",
 "description":"<内容>"
}}
]


更新:

[
{{
 "action":"update",
 "conversation_item_id":"<item id>",
 "description":"<更新後全文>",
 "status":"new|in_progress|closed"
}}
]


何もしない:

[
]
 
JSONのみ返してください。
"""

  response = await client.responses.create(
    model="gpt-4.1-mini",
    input=[
      {
        "role":"user",
        "content":prompt
      }
    ],
    temperature=0,
  )

  try:
    return json.loads(response.output_text)

  except json.JSONDecodeError:
    return []

def safe_json_loads(text: str):
  match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
  if not match:
    raise ValueError(f"No JSON found in: {text}")

  return json.loads(match.group())
