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
You are a strict JSON extraction engine.

Return ONLY a JSON object in this exact format:

{{
  "<field_id>": "<spoken value>"
}}

STRICT RULES:
- Output ONLY valid JSON
- All keys MUST be a field id
- Values must be the spoken value only
- NEVER return field names or descriptions
- NEVER return field metadata
- If a field is not spoken, omit it
- If none spoken, return {{ "field_values": {{}} }}

STYLE RULES:
- Rewrite statements as neutral, formal report entries
- Remove conversational wording
- Remove pronouns when possible
- Do NOT use first person or third person
- Prefer passive or declarative form
- Keep original meaning exactly
- Do NOT add new information

Examples:
Speech: "we finished the roof"
Output: "Roof installation completed"

Speech: "it rained today"
Output: "Rain occurred throughout the day"

Speech: "we installed the wiring"
Output: "Electrical wiring installed"

Output language: {output_language}

FIELDS:
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

async def get_image_tags(image_url: str) -> list[dict]:
  prompt = f"""
You are an expert in construction site images.

Look at the image at the URL: {image_url}

Return a JSON array of objects with the following fields:

[
  {{
    "name": "<short tag name>",
    "description": "<detailed description of what this tag represents in the context of construction reports>"
  }}
]

Include all relevant elements visible in the image (equipment, progress, safety, site conditions, etc.).
Return ONLY valid JSON.
"""

  response = await client.responses.create(
    model="gpt-4.1-mini",
    input=[
      {"role": "system", "content": "You are a construction site image analyzer."},
      {"role": "user", "content": prompt}
    ],
    temperature=0
  )

  content = response.output_text.strip()

  import json
  try:
    tags = json.loads(content)
  except json.JSONDecodeError:
    raise ValueError(f"Failed to parse image tags JSON: {content}")

  valid_tags = []
  for t in tags:
    if "name" in t and "description" in t:
      valid_tags.append({"name": t["name"], "description": t["description"]})

  return valid_tags