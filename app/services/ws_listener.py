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
  decode_responses=True,
  socket_connect_timeout=10,  # Add timeout
  socket_keepalive=True,
  socket_keepalive_options={},
  retry_on_timeout=True
)

logger = logging.getLogger(__name__)

async def start_ws_listener():
  logger.info("🚀 WebSocket Redis listener starting...")
  logger.info(f"🔗 Redis URL: {settings.REDIS_URL[:30]}...")

  try:
    logger.info("🧪 Testing Redis connection...")
    await r.ping()
    logger.info("✅ Redis connection successful")

    logger.info("📡 Creating Redis pubsub...")
    pubsub = r.pubsub()

    logger.info("📻 Subscribing to 'image_events' channel...")
    await pubsub.subscribe("image_events")
    logger.info("✅ Successfully subscribed to 'image_events' channel")

    logger.info("👂 Listening for Redis messages...")

    async for message in pubsub.listen():
      logger.info(f"📥 Raw Redis message received: {message}")

      if message["type"] != "message":
        logger.info(f"⏭️ Skipping non-message type: {message['type']}")
        continue

      try:
        data = json.loads(message["data"])
        user_id = data["user_id"]
        payload = data["payload"]
        
        logger.info(f"📨 Parsed message for user {user_id}: {payload}")
        logger.info(f"🔍 Active connections: {list(manager.connections.keys())}")
        
        await manager.send_to_user(user_id, payload)
        logger.info(f"📤 Message sent to user {user_id}")
      except Exception as e:
        logger.error(f"❌ Error processing Redis message: {e}")
        logger.error(f"📋 Traceback: {traceback.format_exc()}")

  except redis.ConnectionError as e:
    logger.error(f"❌ Redis connection error: {e}")
    logger.error(f"📋 Traceback: {traceback.format_exc()}")
  except redis.TimeoutError as e:
    logger.error(f"❌ Redis timeout error: {e}")
    logger.error(f"📋 Traceback: {traceback.format_exc()}")
  except Exception as e:
    logger.error(f"💥 Fatal error in Redis listener: {e}")
    logger.error(f"📋 Traceback: {traceback.format_exc()}")
    
  logger.error("🛑 WebSocket Redis listener stopped")
