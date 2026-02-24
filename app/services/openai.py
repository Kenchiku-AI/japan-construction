from openai import AsyncOpenAI
import asyncio
import base64
import json
import time

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

  cancelled = asyncio.Event()
  audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()


  full_text_parts: list[str] = []
  last_sent_fields: dict[str, str] = {}
  last_processed_length = 0

  async with client.realtime.connect(
    model="gpt-realtime-mini",
    audio={
      "input": {
        "format": {"type": "audio/pcm", "rate": 24000},
        "transcription": {"model": "gpt-4o-mini-transcribe"},
        "turn_detection": None,
      }
    }
  ) as connection:
    # await connection.session.update(
    #   session={
    #     "audio": {
    #       "input": {
    #         "transcription": {
    #           "model": "gpt-4o-mini-transcribe",
    #           "partial_results": True
    #         }
    #       },
    #       "turn_detection": {"type": "none"}
    #     }
    #   }
    # )

    async def receive_audio():
      nonlocal last_audio_time

      try:
        while not cancelled.is_set():
          msg = await ws.receive()

          if msg["type"] == "websocket.disconnect":
            cancelled.set()
            break

          text = msg.get("text")

          if text == "STOP":
            await audio_queue.put(None)
            break

          if msg.get("bytes") is not None:
            await audio_queue.put(msg["bytes"])
      finally:
        await audio_queue.put(None)
    
    async def send_audio():
      try:
        while True:
          chunk = await audio_queue.get()

          if chunk is None:
            await connection.send({
              "type": "input_audio_buffer.commit"
            })
            return

          await connection.send({
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(chunk).decode()
          })
      except Exception as e:
        cancelled.set()
        await safe_send(ws, {"type": "error", "message": str(e)})

    async def receive_events():
      nonlocal last_processed_length

      async for event in connection:
        if cancelled.is_set():
          break

        etype = getattr(event, "type", None)
-
        if etype == "response.output_audio_transcript.delta":
          delta = event.delta
          if not delta:
            continue

          full_text_parts.append(delta)

          combined = "".join(full_text_parts)

          if len(combined) <= last_processed_length:
            continue

          last_processed_length = len(combined)

          await safe_send(ws, {
            "type": "partial_transcript",
            "text": combined
          })

          try:
            partial_json = await get_json_from_speech(
              combined, prompt, output_language
            )

            changed = {
              k: v for k, v in partial_json.items()
              if last_sent_fields.get(k) != v
            }

            if changed:
              last_sent_fields.update(changed)
              await on_partial(changed)
          except Exception:
            pass
        elif etype == "response.output_audio_transcript.done":
          final_text = event.transcript or ""
          full_text_parts.append(final_text)

          combined = "".join(full_text_parts)

          await safe_send(ws, {
            "type": "final_transcript",
            "text": combined
          })

          try:
            final_json = await get_json_from_speech(
              combined, prompt, output_language
            )
            await on_complete(final_json)
          except Exception as e:
            await safe_send(ws, {"type": "error", "message": str(e)})
          return
        elif etype == "error":
          cancelled.set()
          await safe_send(ws, {
            "type": "error",
            "message": getattr(event.error, "message", "Unknown error")
          })
          return

    tasks = [
      asyncio.create_task(receive_audio()),
      asyncio.create_task(send_audio()),
      asyncio.create_task(receive_events()),
    ]

    done, pending = await asyncio.wait(
      tasks,
      return_when=asyncio.FIRST_COMPLETED
    )

    for t in pending:
      t.cancel()

  try:
    await ws.close()
  except:
    pass

async def get_json_from_speech(
  speech_text: str,
  prompt: str,
  output_language: str = "Japanese"
) -> dict:
  response = await client.chat.completions.create(
    model="gpt-4.1-nano",
    messages=[
      {"role": "system", "content": prompt},
      {"role": "user", "content": speech_text},
    ],
    temperature=0,
    response_format={"type": "json_object"},
  )

  print("JSON from speech", response)

  msg = response.choices[0].message
  content = msg.content if isinstance(msg.content, str) else "".join(
    part.get("text", "") for part in msg.content
  )

  try:
    return json.loads(content)
  except json.JSONDecodeError:
    raise ValueError("Failed to parse normalized daily report JSON")

async def safe_send(ws, data):
  try:
    await ws.send_json(data)
  except RuntimeError:
    pass