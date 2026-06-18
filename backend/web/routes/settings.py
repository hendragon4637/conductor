"""Settings routes — connections, adapters, budgets."""
from __future__ import annotations

import os

from fastapi import APIRouter

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
async def get_settings():
    return {
        "aionui": {
            "url": os.environ.get("AIONUI_URL", "http://127.0.0.1:40937"),
            "status": _check_url("AIONUI_URL", "http://127.0.0.1:40937/health"),
        },
        "langfuse": {
            "host": os.environ.get("LANGFUSE_HOST", "http://127.0.0.1:3001"),
            "status": _check_url("LANGFUSE_HOST", "http://127.0.0.1:3001"),
        },
        "brain": {
            "endpoint": os.environ.get("BRAIN_ENDPOINT", "http://127.0.0.1:11434/v1"),
            "model": os.environ.get("BRAIN_MODEL", "qwen2.5-coder-7b-instruct"),
            "status": _check_url("BRAIN_CHECK_URL", "http://127.0.0.1:11434"),
        },
        "conductor": {
            "version": "0.0.1",
            "workspace_root": os.environ.get("WORKSPACE_ROOT", "/opt/aipc/conductor/workspace"),
        },
    }


def _check_url(env_key: str, default: str) -> str:
    """Quick connectivity check — returns 'ok', 'unreachable', or 'unknown'."""
    url = os.environ.get(env_key, default)
    import urllib.request
    try:
        urllib.request.urlopen(url, timeout=3)
        return "ok"
    except Exception:
        return "unreachable"
