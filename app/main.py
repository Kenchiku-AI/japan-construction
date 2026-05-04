from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import auth, users, companies, invitations, projects, reports, health
from app.core.config import settings
from app.middleware.logging import LoggingMiddleware

import asyncio
import os

app = FastAPI()

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