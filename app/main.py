from fastapi import FastAPI
from app.api import auth, users, companies, invitations, projects
from app.middleware.auth import auth_middleware

app = FastAPI()

app.middleware("http")(auth_middleware)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(companies.router)
app.include_router(invitations.router)
app.include_router(projects.router)