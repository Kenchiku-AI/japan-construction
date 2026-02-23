from openai import AsyncOpenAI
import asyncio
import base64
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

  completed = asyncio.Event()
  cancelled = asyncio.Event()

  full_text_parts: list[str] = []
  audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

  last_processed_length = 0
  last_audio_time = asyncio.get_event_loop().time()
  last_sent_fields: dict[str, str] = {}

  async with client.realtime.connect(model="gpt-realtime-mini") as connection:
    await connection.session.update(
      session={
        "audio": {
          "input": {
            "transcription": {
              "model": "gpt-4o-mini-transcribe",
              "partial_results": True
            }
          },
          "turn_detection": {"type": "none"}
        }
      }
    )

    async def receive_audio():
      nonlocal last_audio_time

      try:
        while not cancelled.is_set():
          try:
            msg = await ws.receive()
          except Exception:
            cancelled.set()
            break

          if msg["type"] == "websocket.disconnect":
            cancelled.set()
            break

          text = msg.get("text")
          if text == "CANCEL":
            cancelled.set()
            await audio_queue.put(None)
            break
          if text == "COMPLETE":
            completed.set()
            break

          if msg.get("bytes") is not None:
            last_audio_time = asyncio.get_event_loop().time()
            await audio_queue.put(msg["bytes"])
      finally:
        await audio_queue.put(None)
    
    async def send_audio():
      try:
        while True:
          chunk = await audio_queue.get()

          if chunk is None:
            break

          await connection.send({
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(chunk).decode()
          })

          await asyncio.sleep(0)

        await connection.send({"type": "input_audio_buffer.commit"})
      except Exception as e:
        cancelled.set()
        await safe_send(ws, {"type": "error", "message": str(e)})

    async def send_field_diffs(new_json: dict, is_final=False):  # NEW
      changed = {}

      for k, v in new_json.items():
        if last_sent_fields.get(k) != v:
          changed[k] = v
          last_sent_fields[k] = v

      if changed:
        await safe_send(ws, {
          "type": "field_update" if not is_final else "final_fields",
          "fields": changed
        })

        await on_partial(changed) if not is_final else await on_complete(new_json)

    async def receive_events():
      nonlocal last_processed_length

      while not cancelled.is_set():
        if asyncio.get_event_loop().time() - last_audio_time > 30:
          cancelled.set()
          return

        try:
          event = await asyncio.wait_for(connection.recv(), timeout=0.5)
        except asyncio.TimeoutError:
          continue
        except StopAsyncIteration:
          break

        print("event received - type:", event.type)

        if event.type == "transcript.partial":
          current_partial = event.text
          await safe_send(ws, {"type": "partial_transcript", "text": event.text})

          try:
            partial_text = " ".join(full_text_parts + [current_partial])

            if len(partial_text) - last_processed_length < 25:
              continue

            last_processed_length = len(partial_text)

            partial_json = await get_json_from_speech(
              partial_text, prompt, output_language
            )

            await send_field_diffs(partial_json)
          except Exception:
            pass
        elif event.type == "transcript.final":
          full_text_parts.append(event.text)
          await safe_send(ws, {"type": "final_transcript", "text": event.text})
          full_text = " ".join(full_text_parts).strip()
          
          try:
            final_json = await get_json_from_speech(full_text, prompt, output_language)
            await send_field_diffs(final_json, is_final=True)
          except Exception as e:
            await safe_send(ws, {"type": "error", "message": str(e)})
          return

    try:
      tasks = [
        asyncio.create_task(receive_audio()),
        asyncio.create_task(send_audio()),
        asyncio.create_task(receive_events()),
      ]

      done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

      for t in pending:
        t.cancel()
    except Exception as e:
      await safe_send(ws, {"type": "error", "message": str(e)})
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