import openai
import asyncio
import json

from pydantic import BaseModel
from typing import Callable, Awaitable, Literal, List
from sqlalchemy.orm import Session

from app.core.config import settings
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

  completed = asyncio.Event()
  cancelled = asyncio.Event()
  final_received = asyncio.Event()

  full_text_parts: list[str] = []

  async def receive_audio():
    try:
      while not cancelled.is_set():
        msg = await ws.receive()

        if msg["type"] == "websocket.disconnect":
          cancelled.set()
          break

        text = msg.get("text")

        if text == "CANCEL":
          cancelled.set()
          break

        if text == "COMPLETE":
          completed.set()
          break

        if completed.is_set() or cancelled.is_set():
          continue

        if msg.get("bytes") is not None:
          await stream.send_audio(msg["bytes"])

    finally:
      try:
        await stream.finish()
      except:
        pass

      if completed.is_set() and not cancelled.is_set():
        try:
          await asyncio.wait_for(final_received.wait(), timeout=2)
        except asyncio.TimeoutError:
          pass

  async def receive_events():
    while not cancelled.is_set():
      if cancelled.is_set():
        return

      try:
        event = await asyncio.wait_for(
          stream.__anext__(),
          timeout=0.5
        )
      except asyncio.TimeoutError:
        continue
      except StopAsyncIteration:
        return

      if event.type == "transcript.partial":
        await ws.send_json({
          "type": "partial_transcript",
          "text": event.text,
        })

      if event.type == "transcript.final":
        if cancelled.is_set():
          return

        full_text_parts.append(event.text)
        final_received.set()

        await ws.send_json({
          "type": "final_transcript",
          "text": event.text,
        })

        if completed.is_set():
          full_text = " ".join(full_text_parts).strip()
          await on_complete(full_text)
          return

  try:
    async with stream:
      await asyncio.gather(
        receive_audio(),
        receive_events()
      )

  except Exception as e:
    print("transcription error:", e)

  finally:
    try:
      await ws.close()
    except:
      pass

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

  msg = response.choices[0].message
  content = msg.content if isinstance(msg.content, str) else "".join(
    part.get("text","") for part in msg.content
  )

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
- Output must be valid RFC8259 JSON

Fields:
{field_block}

Output format example:
{{
  "field_id_1": "Normalized value",
  "field_id_2": "Another value"
}}
""".strip()