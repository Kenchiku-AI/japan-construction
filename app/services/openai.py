from openai import AsyncOpenAI
from typing import List, Iterable
import json

from app.core.config import settings
from app.db.models.report import ReportField, ReportImageTag

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
    schema = {
      "type": "object",
      "properties": {
        "tags": {
          "type": "array",
          "items": {"type": "string"}
        },
        "description": {
          "type": "string"
        }
      },
      "required": ["tags", "description"]
    }

    description_instruction = """
Also include a professional, concise description (1-2 sentences)
suitable for a construction report. Focus only on visible facts
such as work being performed, equipment, and safety conditions.
Do not speculate. Use precise, formal language suitable for construction documentation.
Avoid vague terms like "some", "various", or "etc."
"""
  else:
    schema = {
      "type": "object",
      "properties": {
        "tags": {
          "type": "array",
          "items": {"type": "string"}
        }
      },
      "required": ["tags"]
    }

    description_instruction = ""

  response = await client.responses.create(
    model="gpt-4.1-mini",
    temperature=0,
    response_format={
      "type": "json_schema",
      "json_schema": {
        "name": "image_analysis",
        "schema": schema
      }
    },
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
Below is a list of available tags that may apply to this image.

{tag_list_json}

Select all tags that clearly apply to the image.
Only select tags that are visually present.
Do not infer or guess beyond what is visible.
If no tags apply, return an empty array.

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

  return response.output[0].content[0].json