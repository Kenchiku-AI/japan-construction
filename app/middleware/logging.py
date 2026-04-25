import json
import logging
import time
import uuid
import os
from typing import Callable, Dict, Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_BODY = os.getenv("LOG_BODY", "false").lower() == "true"
LOG_BODY_MAX_BYTES = int(os.getenv("LOG_BODY_MAX_BYTES", "5000"))
LOG_SAMPLE_RATE = float(os.getenv("LOG_SAMPLE_RATE", "1.0"))  # 0.0 - 1.0

SENSITIVE_KEYS = {
  "password",
  "token",
  "access_token",
  "refresh_token",
  "authorization",
  "email",
}

logger = logging.getLogger("app")
logger.setLevel(LOG_LEVEL)

handler = logging.StreamHandler()

class JsonFormatter(logging.Formatter):
  def format(self, record: logging.LogRecord) -> str:
      log_record = {
          "timestamp": int(time.time() * 1000),
          "level": record.levelname,
          "message": record.getMessage(),
      }

      if hasattr(record, "extra"):
          log_record.update(record.extra)

      return json.dumps(log_record)

handler.setFormatter(JsonFormatter())
logger.handlers = [handler]

def should_sample() -> bool:
  import random
  return random.random() < LOG_SAMPLE_RATE

def truncate_body(body: bytes) -> str:
  if not body:
    return ""
  if len(body) > LOG_BODY_MAX_BYTES:
    return body[:LOG_BODY_MAX_BYTES].decode("utf-8", errors="ignore") + "...[truncated]"
  return body.decode("utf-8", errors="ignore")

def try_parse_json(data: str) -> Any:
  try:
    return json.loads(data)
  except Exception:
    return data

def mask_sensitive(data: Any) -> Any:
  if isinstance(data, dict):
    return {
      k: ("***" if k.lower() in SENSITIVE_KEYS else mask_sensitive(v))
      for k, v in data.items()
    }
  elif isinstance(data, list):
    return [mask_sensitive(v) for v in data]
  return data

async def get_request_body(request: Request) -> bytes:
  body = await request.body()

  async def receive() -> dict:
    return {"type": "http.request", "body": body}

  request._receive = receive
  return body

class LoggingMiddleware(BaseHTTPMiddleware):
  def __init__(self, app: ASGIApp):
    super().__init__(app)

  async def dispatch(self, request: Request, call_next: Callable) -> Response:
    start_time = time.time()

    request_id = str(uuid.uuid4())
    request.state.request_id = request_id

    log_body = LOG_BODY and should_sample()

    request_body = b""
    if log_body:
      request_body = await get_request_body(request)

    try:
      response = await call_next(request)
    except Exception as e:
      duration = int((time.time() - start_time) * 1000)

      logger.error(
        "request_failed",
        extra={
          "extra": {
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "duration_ms": duration,
            "error": str(e),
          }
        },
      )
      raise

    duration = int((time.time() - start_time) * 1000)

    response_body = b""
    if log_body and hasattr(response, "body"):
      response_body = response.body or b""

    request_body_parsed = None
    response_body_parsed = None

    if log_body:
      req_text = truncate_body(request_body)
      res_text = truncate_body(response_body)

      request_body_parsed = mask_sensitive(try_parse_json(req_text))
      response_body_parsed = mask_sensitive(try_parse_json(res_text))

    log_payload: Dict[str, Any] = {
      "request_id": request_id,
      "method": request.method,
      "path": request.url.path,
      "query": str(request.url.query),
      "status_code": response.status_code,
      "duration_ms": duration,
      "client_ip": request.client.host if request.client else None,
      "user_agent": request.headers.get("user-agent"),
    }

    if log_body:
      log_payload["request_body"] = request_body_parsed
      log_payload["response_body"] = response_body_parsed

    logger.info(
      "request_complete",
      extra={"extra": log_payload},
    )

    response.headers["X-Request-ID"] = request_id

    return response