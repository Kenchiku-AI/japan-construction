import openai
import asyncio
import json

from pydantic import BaseModel
from typing import Callable, Awaitable, Literal, List
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.prompts import NORMALIZE_DAILY_REPORT_PROMPT
from app.schema.agent import PartialTranscript, FinalTranscript
from app.models.report import ReportTemplateField

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
      audio_chunk = await ws.receive_bytes()
      await stream.send_audio(audio_chunk)

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

  async with stream:
    await asyncio.gather(receive_audio(), receive_events())

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

def get_prompt_from_fields(fields: List[ReportTemplateField]) -> str:
  return ""