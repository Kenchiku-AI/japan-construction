from openai import AsyncOpenAI
from typing import List
import json

from app.core.config import settings
from app.db.models.report import ReportField

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

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
You extract structured report data from speech.

Rules:
- Return ONLY valid JSON
- Keys must match listed field IDs
- Omit fields not mentioned
- Do not hallucinate
- Normalize wording professionally
- Output language: {output_language}

Fields:
{field_block}
""".strip()

  response = await client.chat.completions.create(
    model="gpt-4.1-nano",
    messages=[
      {"role": "system", "content": prompt},
      {"role": "user", "content": speech_text},
    ],
    temperature=0,
    response_format={"type": "json_object"},
  )

  msg = response.choices[0].message
  content = msg.content or ""
  if not isinstance(content, str):
    content = "".join(part.get("text", "") for part in content)

  try:
    parsed = json.loads(content)
  except json.JSONDecodeError:
    raise ValueError("Failed to parse normalized JSON from speech")

  return parsed