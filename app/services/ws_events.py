import redis.asyncio as redis
import json
import ssl
from app.core.config import settings

r = redis.from_url(
  settings.REDIS_URL,
  ssl_cert_reqs=ssl.CERT_NONE,
  ssl_check_hostname=False,
  decode_responses=True
)

async def publish_image_tags_ready(user_id: str, payload: dict):
  await r.publish(
    "image_events",
    json.dumps(
      {
        "user_id": user_id,
        "payload": payload
      },
      default=str
    )
  )