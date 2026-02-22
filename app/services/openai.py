from openai import AsyncOpenAI
import asyncio
import json

from pydantic import BaseModel
from typing import Callable, Awaitable, Literal, List
from fastapi import WebSocket

from app.core.config import settings
from app.db.models.report import ReportField

client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

async def transcribe_and_extract_json(
  ws: WebSocket,
  fields: List[ReportField],
  output_language: str,
  on_partial: Callable[[dict], Awaitable[None]],
  on_complete: Callable[[dict], Awaitable[None]],
):
  field_lines = [
    f'- id: "{f.id}"\n  name: "{f.name}"\n  description: "{f.description}"'
    for f in fields
  ]
  field_block = "\n".join(field_lines)

  prompt = f"""
You are an assistant that extracts structured report data from speech transcripts.

The speech may be in any language. Automatically detect the input language.

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

  session = await client.realtime.connect(model="gpt-4o-realtime")

  completed = asyncio.Event()
  cancelled = asyncio.Event()
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

        if msg.get("bytes") is not None:
          await session.send(
            {"type": "input_audio_buffer.append", "audio": msg["bytes"]}
          )
    finally:
        try:
          await session.send({"type": "input_audio_buffer.commit"})
        except:
          pass

  async def receive_events():
    while not cancelled.is_set():
      try:
        event = await asyncio.wait_for(session.__anext__(), timeout=0.5)
      except asyncio.TimeoutError:
        continue
      except StopAsyncIteration:
        break

      if event.type == "transcript.partial":
        full_text_parts.append(event.text)
        await ws.send_json({"type": "partial_transcript", "text": event.text})

        try:
          partial_text = " ".join(full_text_parts).strip()
          partial_json = await get_json_from_speech(
            partial_text, prompt, output_language
          )
          await on_partial(partial_json)
        except Exception:
          pass

      elif event.type == "transcript.final":
        full_text_parts.append(event.text)
        await ws.send_json({"type": "final_transcript", "text": event.text})
        full_text = " ".join(full_text_parts).strip()
        try:
          final_json = await get_json_from_speech(full_text, prompt, output_language)
          await on_complete(final_json)
        except Exception as e:
          await ws.send_json({"type": "error", "message": str(e)})
        return

  try:
    async with session:
      await asyncio.gather(receive_audio(), receive_events())
  finally:
    try:
      await ws.close()
    except:
      pass


async def get_json_from_speech(
    speech_text: str,
    prompt: str,
    output_language: str = "English"
) -> dict:
    """
    Convert speech text into structured JSON using ChatCompletion,
    enforcing the desired output language.
    """
    response = await client.chat.completions.create(
        model="gpt-4.1-nano",
        messages=[
            {"role": "system", "content": f"{prompt}\nOutput all values in {output_language}."},
            {"role": "user", "content": speech_text},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )

    msg = response.choices[0].message
    content = msg.content if isinstance(msg.content, str) else "".join(
        part.get("text", "") for part in msg.content
    )

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        raise ValueError("Failed to parse normalized daily report JSON")