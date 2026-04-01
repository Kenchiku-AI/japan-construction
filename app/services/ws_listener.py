import redis.asyncio as redis
import json

from app.core.config import settings
from app.services.ws_manager import manager

r = redis.from_url(settings.REDIS_URL)

async def start_ws_listener():

  pubsub = r.pubsub()

  await pubsub.subscribe("image_events")

  async for message in pubsub.listen():

    if message["type"] != "message":
      continue

    data = json.loads(message["data"])

    user_id = data["user_id"]
    payload = data["payload"]

    await manager.send_to_user(user_id, payload)