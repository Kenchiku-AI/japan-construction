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

値の整形ルール:
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

async def extract_conversation_items(
  message_text: str,
  conversation: LineConversation,
  recent_messages: list[LineMessage],
  item_types: list[ConversationItemType],
  existing_items: list[ConversationItem],
):
  history_lines = []

  for message in sorted(
    recent_messages,
    key=lambda m: m.line_timestamp or m.created_at,
  ):
    timestamp = message.line_timestamp or message.created_at

    line = (
      f'[{timestamp.strftime("%Y-%m-%d %H:%M")}] '
      f'{message.line_user_id or message.sender_line_user_id}\n'
      f'{message.text}'
    )

    if message.conversation_item_links:
      extracted = "\n".join(
        (
          f'    - {link.conversation_item.item_type.name}: '
          f'{link.conversation_item.name}'
        )
        for link in message.conversation_item_links
      )

      line += f"\n\n※抽出済み:\n{extracted}"

    history_lines.append(line)

  history_block = "\n\n".join(history_lines) or "なし"

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

descriptionには、そのフィールドに保存する値の形式や内容に関する指示が含まれています。

必ずdescriptionの指示に従ってください。

例えば、

- 数値のみ
- 日付のみ
- 箇条書き
- 一文
- 詳細な説明

などの指定がある場合は、その形式を厳守してください。

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

各メッセージには発言日時と発言者が表示されています。

発言の順序や発言者も考慮しながら、
新規作成・更新・何もしないを判断してください。

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

async def extract_report_fields_from_line_conversations(
  conversation_segments: list[tuple[LineConversation, list[LineMessage]]],
  fields: list[ReportField],
  output_language: str,
) -> dict:
  logger.info(
    "Preparing extraction: %d conversations, %d report fields",
    len(conversation_segments),
    len(fields),
  )

  field_block = "\n".join(
    f"""
- id: {field.id}
  name: {field.name}
  description: {field.description}
""".strip()
    for field in fields
  ) or "なし"

  conversation_blocks = []

  for conversation, messages in conversation_segments:
    sorted_messages = sorted(
      messages,
      key=lambda m: m.line_timestamp or m.created_at,
    )

    history_lines = []

    for message in sorted_messages:
      timestamp = message.line_timestamp or message.created_at

      history_lines.append(
        f"""[{timestamp.strftime("%Y-%m-%d %H:%M")}] {message.line_user_id or message.sender_line_user_id}

{message.text}"""
      )

    history = "\n\n".join(history_lines) or "なし"

    if sorted_messages:
      start = sorted_messages[0].line_timestamp or sorted_messages[0].created_at
      end = sorted_messages[-1].line_timestamp or sorted_messages[-1].created_at

      if start.date() == end.date():
        date_range = start.strftime("%Y-%m-%d")
      else:
        date_range = (
          f"{start.strftime('%Y-%m-%d')} ～ "
          f"{end.strftime('%Y-%m-%d')}"
        )
    else:
      date_range = "不明"

    conversation_blocks.append(
      f"""
### LINEグループ

{conversation.name}

### プロジェクト

{conversation.project.name if conversation.project else "なし"}

### 対象期間

{date_range}

### 会話

{history}
""".strip()
    )

  conversations_block = "\n\n---\n\n".join(conversation_blocks)

  prompt = f"""
あなたは建設会社向けAIアシスタントです。

LINE会話履歴から、工事報告書の各Report Fieldに入力する情報だけを抽出してください。

重要:
各Report Fieldは独立して判断してください。
あるフィールドに関係する情報だけを探し、それ以外の情報は無視してください。

会話履歴には指定された期間内のLINEメッセージのみが含まれています。
対象期間内に明示されている情報だけを利用してください。

各会話には異なる工事やプロジェクトに関する情報が含まれる場合があります。
会話ごとの文脈を維持し、別の会話や別の話題の情報を混同しないでください。

---

## 抽出手順

各Report Fieldについて:

1. descriptionを理解する
2. LINE会話から、そのフィールドに直接関係する発言だけを探す
3. 複数の候補がある場合:
   - 後から送信されたメッセージを優先する
   - 訂正・変更・取消の発言がある場合は最新の内容を使用する
   - 古い情報は使用しない
4. 明確な情報がない場合は、そのフィールドを返さない

---

## 重要な判断ルール

- 会話に明示されている情報のみ使用する
- 推測しない
- 文脈から推測できそうでも補完しない
- 別のフィールドに使えそうな情報を流用しない
- 関係ない会話内容に影響されない
- 各Report Fieldは独立して判断する
- 同じフィールドについて複数の発言がある場合は、最新の確定情報を使用する
- 後から送信された訂正・変更・取消の発言は、以前の発言より優先する

例:

### 例1: 数量の訂正

会話:

「資材は50個搬入しました」
「確認したところ、実際は45個でした」

Report Field:
搬入数量

出力:
45

理由:
後の発言で数量が訂正されているため。


---

### 例2: 作業内容の変更

会話:

「本日は外壁塗装を開始しました」
「天候不良のため、本日の塗装作業は中止しました」

Report Field:
実施業務

出力:
天候不良のため外壁塗装作業を中止


理由:
後の発言で作業状況が変更されているため。


---

### 例3: 完了状態の更新

会話:

「3階の配管作業を進めています」
「3階の配管作業が完了しました」

Report Field:
作業状況

出力:
3階配管作業完了


理由:
後の発言が最新の状態を示しているため。


---

### 例4: 関係ない情報を除外

会話:

「明日は雨予報です」
「昨日の現場では足場設置を完了しました」

Report Field:
実施業務

出力:
足場設置完了


理由:
天候情報は実施業務のフィールドには関係しないため。


---

### 例5: 推測禁止

会話:

「職長から確認待ちです」

Report Field:
作業完了日時

出力:
なし


理由:
完了日時は明示されていないため。


---

### 例6: 複数メッセージの統合

会話:

「4階の床施工を開始しました」
「午後に床材の搬入が完了しました」
「夕方には施工まで終わりました」

Report Field:
実施業務

出力:
4階床施工完了


理由:
複数の発言から同じ作業に関する情報を整理しているため。

ただし、会話に存在しない情報は追加しない。

---

## Report Fields

{field_block}

description は、その項目に何を記録するかを示します。

必ずdescriptionを参考にし、そのフィールドに一致する情報だけを抽出してください。

---

## LINE会話

{conversations_block}

---

## 出力ルール

必ずJSONオブジェクトのみ返してください。

形式:

{{
  "<field_id>": <value>
}}

厳守事項:

- 有効なJSONのみ返す
- キーは必ずfield id
- field名は禁止
- descriptionは禁止
- 会話に存在しない情報は禁止
- 推測は禁止
- 該当情報がないフィールドは返さない
- 空のJSON {{}} は、該当する情報が一切ない場合のみ使用する

---

## 値の整形ルール

description:
「数値のみで入力してください」

入力:
「今日は62人でした」

出力:
62


---

description:
「日付のみ入力してください」

入力:
「7月3日に完了しました」

出力:
2026-07-03


---

description:
「一文で記載してください」

入力:
「屋根終わりました」

出力:
屋根工事完了


---

文章形式の場合:

- 工事報告書向けの自然で正式な表現に整形する
- 元の意味を変更しない
- 新しい情報を追加しない
- 不要な主語や代名詞は省略する
- 客観的・記録的な表現を使用する
- 推測しない

複数のメッセージに同じフィールドに関する情報が分散している場合:
- 内容を統合して最終的な報告内容を作成する
- 重複する内容は一度だけ記載する
- ただし、異なるフィールドの情報を混ぜない

---

出力言語:
{output_language}
""".strip()

  logger.info(
    "===== OpenAI Prompt =====\n%s\n===== End Prompt =====",
    prompt,
  )

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

  logger.info(
    "===== OpenAI Raw Response =====\n%s\n===== End Response =====",
    response.output_text,
  )

  try:
    parsed = safe_json_loads(response.output_text)

    logger.info(
      "===== Parsed JSON =====\n%s\n===== End Parsed JSON =====",
      parsed,
    )

    return parsed

  except Exception:
    logger.exception(
      "Failed to parse OpenAI response:\n%s",
      response.output_text,
    )
    return {}

def safe_json_loads(text: str):
  match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
  if not match:
    raise ValueError(f"No JSON found in: {text}")

  return json.loads(match.group())
