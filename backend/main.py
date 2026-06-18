"""Conductor FastAPI entrypoint."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

from backend.api import (
    projects as projects_api,
    sessions as sessions_api,
    tasks as tasks_api,
    traces as traces_api,
    agent_configs as configs_api,
    spawn as spawn_api,
    labels as labels_api,
    memory as memory_api,
    skills as skills_api,
    triggers as triggers_api,
    hooks as hooks_api,
    auth as auth_api,
)

from backend.web.routes import plan as plan_routes
from backend.web.routes import scores as scores_routes
from backend.web.routes import chat as chat_routes
from backend.web.routes import ratchet as ratchet_routes
from backend.web.routes import worktrees as worktrees_routes
from backend.web.routes import settings as settings_routes
from backend.watcher.supervisor import get_watcher, bootstrap_from_db

app = FastAPI(title="AIPC Conductor", version="0.0.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3090", "http://127.0.0.1:3090"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "conductor"}


@app.on_event("startup")
async def _startup_watcher() -> None:
    watcher = get_watcher()
    watcher.start()
    bootstrap_from_db()


app.include_router(projects_api.router)
app.include_router(sessions_api.router)
app.include_router(tasks_api.router)
app.include_router(traces_api.router)
app.include_router(configs_api.router)
app.include_router(spawn_api.router)
app.include_router(labels_api.router)
app.include_router(memory_api.router)
app.include_router(skills_api.router)
app.include_router(triggers_api.router)
app.include_router(hooks_api.router)
app.include_router(auth_api.router)

# Web-specific routes (plan, chat, scores, ratchet, worktrees, settings)
app.include_router(plan_routes.router)
app.include_router(scores_routes.router)
app.include_router(chat_routes.router)
app.include_router(ratchet_routes.router)
app.include_router(worktrees_routes.router)
app.include_router(settings_routes.router)
