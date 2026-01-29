from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import auth, users, companies, invitations, projects
from app.core.config import settings
from app.middleware.auth import auth_middleware

app = FastAPI()

app.middleware("http")(auth_middleware)

app.add_middleware(
  CORSMiddleware,
  allow_origins=[settings.WEB_CLIENT_URL],
  allow_credentials=True,     
  allow_methods=["*"],       
  allow_headers=["*"],        
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(companies.router)
app.include_router(invitations.router)
app.include_router(projects.router)