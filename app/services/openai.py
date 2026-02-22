import openai
import asyncio
import json

from pydantic import BaseModel
from typing import Callable, Awaitable, Literal, List
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.prompts import NORMALIZE_DAILY_REPORT_PROMPT
from app.db.models.report import ReportField

openai.api_key = settings.OPENAI_API_KEY

class PartialTranscript(BaseModel):
  type: Literal["partial_transcript"]
  text: str

class FinalTranscript(BaseModel):
  type: Literal["final_transcript"]
  text: str

async def transcribe_audio(
  ws,
  on_complete: Callable[[str], Awaitable[None]]
):
  stream = openai.audio.transcriptions.stream(
    model="gpt-4o-transcribe",
    sample_rate=16000,
    encoding="pcm16",
  )

  async def receive_audio():
    while True:
      msg = await ws.receive()

      if msg["type"] == "websocket.disconnect":
        break

      if "text" in msg and msg["text"] == "STOP":
        break

      if "bytes" in msg:
        await stream.send_audio(msg["bytes"])

  async def receive_events():
    async for event in stream:
      if event.type == "transcript.partial":
        await ws.send_json(
          PartialTranscript(
            type="partial_transcript",
            text=event.text,
          ).model_dump()
        )

      if event.type == "transcript.final":
        await ws.send_json(
          FinalTranscript(
            type="final_transcript",
            text=event.text,
          ).model_dump()
        )

        await on_complete(event.text)

  try:
    async with stream:
      await asyncio.gather(receive_audio(), receive_events())
  except Exception:
    pass
  finally:
    await ws.close()

async def get_json_from_speech(speech_text: str, prompt: str) -> dict:
  response = openai.ChatCompletion.create(
    model="gpt-4.1-nano",
    messages=[
      {
        "role": "system",
        "content": prompt,
      },
      {
        "role": "user",
        "content": speech_text,
      },
    ],
    temperature=0,
    response_format={"type": "json_object"},
  )

  content = response.choices[0].message.content

  try:
    return json.loads(content)
  except json.JSONDecodeError:
    raise ValueError("Failed to parse normalized daily report JSON")

def get_prompt_from_fields(fields: list[ReportField], output_language: str) -> str:
  field_lines = []

  for f in fields:
    field_lines.append(
      f'- id: "{f.id}"\n'
      f'  name: "{f.name}"\n'
      f'  description: "{f.description}"'
    )

  field_block = "\n".join(field_lines)

  return f"""
You are an assistant that extracts structured report data from speech transcripts.

The speech may be in English or Japanese.
Automatically detect the input language.

Your task:
Convert the speech into a JSON object.

Rules:
- Return ONLY valid JSON
- Do not include explanations
- Keys must be the field IDs listed below
- Values must be concise, professional, factual text
- Remove filler words, rambling, and casual phrasing
- Normalize wording into formal report language
- Do not invent information
- If a field is not mentioned, omit it entirely
- Do not output null values
- Do not include fields not listed
- Output all values in {output_language}

Fields:
{field_block}

Output format example:
{{
  "field_id_1": "Normalized value",
  "field_id_2": "Another value"
}}
""".strip()