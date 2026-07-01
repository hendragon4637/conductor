"""Conductor Web UI — FastAPI app serving frontend + API routes on port 3090."""
import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

_HERE = Path(__file__).resolve().parent.parent.parent
load_dotenv(_HERE / ".env")
os.environ.setdefault("WORKSPACE_ROOT", str(_HERE / "workspace"))

# ── Existing internal API routers ────────────────────────────────────────
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
)

# ── Web-specific route modules ───────────────────────────────────────────
from backend.web.routes import chat
from backend.web.routes import plan
from backend.web.routes import scores
from backend.web.routes import ratchet as ratchet_routes
from backend.web.routes import worktrees
from backend.web.routes import settings as settings_routes

app = FastAPI(title="AIPC Conductor — Web UI", version="0.0.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Health ───────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "conductor-web"}


# ── LiteLLM gateway health gate ─────────────────────────────────────────
@app.on_event("startup")
async def _check_litellm_gateway():
    """Fail fast if the LiteLLM gateway is unreachable on startup."""
    import urllib.request
    import json

    base = os.environ.get("LITELLM_BASE", "http://litellm:4000/v1")
    health_url = base.rstrip("/").replace("/v1", "").replace("/v1/", "") + "/health/readiness"

    # Strip any /chat/completions suffix
    if health_url.endswith("/chat/completions"):
        health_url = health_url.replace("/chat/completions", "")

    try:
        req = urllib.request.Request(health_url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = json.loads(resp.read().decode())
            if body.get("status") != "healthy":
                print(f"WARNING: LiteLLM gateway at {health_url} returned: {body}", flush=True)
            else:
                print(f"LiteLLM gateway healthy at {health_url}", flush=True)
    except Exception as exc:
        print(
            f"WARNING: LiteLLM gateway at {health_url} unreachable on startup: {exc}. "
            f"LLM calls will fail until the gateway is available.",
            flush=True,
        )

# ── Existing API routers (re-used as-is) ─────────────────────────────────
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

# ── Web-specific routes ──────────────────────────────────────────────────
app.include_router(chat.router)
app.include_router(plan.router)
app.include_router(scores.router)
app.include_router(ratchet_routes.router)
app.include_router(worktrees.router)
app.include_router(settings_routes.router)

# ── Static frontend (React SPA) ──────────────────────────────────────────
_STATIC = os.environ.get(
    "CONDUCTOR_WEB_STATIC",
    str(Path(__file__).resolve().parent.parent.parent / "ui" / "dist"),
)
if os.path.isdir(_STATIC):
    app.mount("/", StaticFiles(directory=_STATIC, html=True), name="static")
else:
    @app.get("/")
    async def no_frontend():
        return {
            "status": "warning",
            "message": f"Frontend build not found at {_STATIC}. "
                        "Run `npm run build` in the ui/ directory.",
        }
