"""Conductor FastAPI entrypoint. Just /health for now."""
from fastapi import FastAPI

app = FastAPI(title="AIPC Conductor", version="0.0.1")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "conductor"}
