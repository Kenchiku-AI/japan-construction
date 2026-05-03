from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import auth, users, companies, invitations, projects, reports, health
from app.core.config import settings
from app.middleware.logging import LoggingMiddleware

import asyncio
import os
import logging
from app.services.ws_listener import start_ws_listener

logger = logging.getLogger(__name__)

app = FastAPI()

_redis_listener_started = False

@app.on_event("startup")
async def start_ws_background_tasks():
  global _redis_listener_started

  worker_id = os.getpid()
  logger.info(f"🚀 FastAPI startup in worker {worker_id}")

  if not _redis_listener_started:
    logger.info(f"📡 Starting Redis listener in worker {worker_id}")
    _redis_listener_started = True
    asyncio.create_task(start_ws_listener()
  else:
    logger.info(f"⏭️ Redis listener already started, skipping in worker {worker_id}")

app.add_middleware(
  CORSMiddleware,
  allow_origins=[settings.WEB_CLIENT_URL],
  allow_credentials=True,
  allow_methods=["*"],
  allow_headers=["*"],
)

app.add_middleware(LoggingMiddleware)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(companies.router)
app.include_router(invitations.router)
app.include_router(projects.router)
app.include_router(reports.router)
app.include_router(health.router)