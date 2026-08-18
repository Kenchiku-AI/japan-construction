from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
  auth, 
  users, 
  companies, 
  invitations, 
  projects, 
  reports, 
  health, 
  webhooks, 
  billing_plans,
  conversations,
  conversation_items,
  custom_fields,
  custom_relationships,
  custom_objects,
)
from app.core.config import settings
from app.middleware.logging import LoggingMiddleware

import asyncio
import os
import stripe

stripe.api_key = settings.STRIPE_SECRET_KEY
stripe.api_version = "2026-05-27.dahlia"

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
app.include_router(webhooks.router)
app.include_router(billing_plans.router)
app.include_router(conversations.router)
app.include_router(conversation_items.router)
app.include_router(custom_fields.router)
app.include_router(custom_relationships.router)
app.include_router(custom_objects.router)