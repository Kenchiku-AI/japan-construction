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
  output_language: str
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

  print("prompt:", prompt)

  cancelled = asyncio.Event()
  audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
  last_sent_fields: dict[str, str] = {}

  SILENCE_TIMEOUT = 10
  last_audio_time = asyncio.get_event_loop().time()

  async with client.realtime.connect(model="gpt-realtime-mini") as connection:
    await connection.session.update(
      session={
        "type": "realtime",
        "audio": {
          "input": {
            "transcription": {
              "model": "gpt-4o-mini-transcribe"
            }
          }
        }
      }
    )

    async def receive_audio():
      nonlocal last_audio_time

      try:
        while not cancelled.is_set():
          try:
            msg = await asyncio.wait_for(ws.receive(), timeout=1.0)
          except asyncio.TimeoutError:
            if asyncio.get_event_loop().time() - last_audio_time > SILENCE_TIMEOUT:
              cancelled.set()
              await audio_queue.put(None)
              return
            continue

          if msg["type"] == "websocket.disconnect":
            cancelled.set()
            break

          text = msg.get("text")

          if text == "STOP":
            await audio_queue.put(None)
            break

          if msg.get("bytes") is not None:
            last_audio_time = asyncio.get_event_loop().time()
            await audio_queue.put(msg["bytes"])
      except Exception as e:
        cancelled.set()
        await audio_queue.put(None)
        await safe_send(ws, {"type": "error", "message": str(e)})
    
    async def send_audio():
      try:
        while not cancelled.is_set():
          chunk = await audio_queue.get()

          if chunk is None:
            await connection.send({"type": "input_audio_buffer.commit"})
            continue

          await connection.send({
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(chunk).decode()
          })
      except Exception as e:
        cancelled.set()
        await safe_send(ws, {"type": "error", "message": str(e)})

    async def receive_events():
      buffer = ""
      last_emit_time = asyncio.get_event_loop().time()
      last_processed_length = 0
      MIN_INTERVAL = 1
      SENTENCE_END = (".", "?", "!", "。", "？", "！")

      async for event in connection:
        if cancelled.is_set():
          break

        etype = getattr(event, "type", None)

        if etype == "conversation.item.input_audio_transcription.delta":
          delta = event.delta
          if not delta:
            continue

          buffer += delta
          confidence = getattr(event, "confidence", None)

          await safe_send(ws, {"type": "partial_transcript", "text": buffer})

          now = asyncio.get_event_loop().time()

          ended_sentence = buffer.endswith(SENTENCE_END)

          time_trigger = (
            (now - last_emit_time) > MIN_INTERVAL
            and len(buffer) - last_processed_length > 20
          )

          should_process = ended_sentence or time_trigge

          if should_process:
            last_emit_time = now

            new_text = buffer[last_processed_length:].strip()

            if not new_text:
              continue

            last_processed_length = len(buffer)

            try:
              partial_json = await get_json_from_speech(new_text, prompt)

              changed_fields = {
                k: v for k, v in partial_json.items()
                if last_sent_fields.get(k) != v
              }

              if changed_fields:
                last_sent_fields.update(changed_fields)
                await safe_send(ws, {"type": "partial_json", "data": changed_fields})
            except Exception:
              pass
        elif etype == "conversation.item.input_audio_transcription.completed":
          final_text = buffer.strip()
          buffer = ""

          await safe_send(ws, {"type": "final_transcript", "text": final_text})

          if final_text:
            try:
              final_json = await get_json_from_speech(final_text, prompt)
              await safe_send(ws, {"type": "final_json", "data": final_json})
            except Exception as e:
              await safe_send(ws, {"type": "error", "message": str(e)})
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

    await cancelled.wait()

    for t in tasks:
      t.cancel()

  try:
    await ws.close()
  except:
    pass

async def get_json_from_speech(
  speech_text: str,
  prompt: str
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

  msg = response.choices[0].message
  content = msg.content or ""
  if not isinstance(content, str):
    content = "".join(part.get("text", "") for part in content)

  try:
    return json.loads(content)
  except json.JSONDecodeError:
    raise ValueError("Failed to parse normalized daily report JSON")

async def safe_send(ws, data):
  try:
    await ws.send_json(data)
  except RuntimeError:
    pass