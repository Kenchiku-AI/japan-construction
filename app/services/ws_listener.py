import redis.asyncio as redis
import json
import logging
import ssl

from app.core.config import settings
from app.services.ws_manager import manager

r = redis.from_url(
  settings.REDIS_URL,
  ssl_cert_reqs=ssl.CERT_NONE,
  ssl_check_hostname=False,
  decode_responses=True
)

logger = logging.getLogger(__name__)

async def start_ws_listener():
  logger.info("🚀 WebSocket Redis listener starting...")
  logger.info(f"🔗 Redis URL: {settings.REDIS_URL[:30]}...")

  try:
    # Test Redis connection first
    await r.ping()
    logger.info("✅ Redis connection successful")
  except Exception as e:
    logger.error(f"❌ Redis connection failed: {e}")
    return

  pubsub = r.pubsub()

  logger.info("👂 Listening for Redis messages...")

  try:
    await pubsub.subscribe("image_events")
    logger.info("✅ Successfully subscribed to 'image_events' channel")
  except Exception as e:
    logger.warning(f"Redis not available: {e}")

  async for message in pubsub.listen():
    logger.info(f"📥 Raw Redis message received: {message}")
    
    if message["type"] != "message":
      continue

    data = json.loads(message["data"])

    user_id = data["user_id"]
    payload = data["payload"]

    await manager.send_to_user(user_id, payload)