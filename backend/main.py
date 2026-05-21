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
)

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


app.include_router(projects_api.router)
app.include_router(sessions_api.router)
app.include_router(tasks_api.router)
app.include_router(traces_api.router)
app.include_router(configs_api.router)
app.include_router(spawn_api.router)
app.include_router(labels_api.router)
