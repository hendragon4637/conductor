"""Common helpers for e2e scenario scripts."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import urllib.request
import base64

# ── Service URLs ──────────────────────────────────────────────────────────
CONDUCTOR = os.environ.get("CONDUCTOR_URL", "http://127.0.0.1:3090")
AIONUI = os.environ.get("AIONUI_URL", "http://127.0.0.1:40937")
LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST", "http://127.0.0.1:3001")
LANGFUSE_PK = os.environ.get("LANGFUSE_PUBLIC_KEY", "pk-lf-local")
LANGFUSE_SK = os.environ.get("LANGFUSE_SECRET_KEY", "sk-lf-local")
WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", "/opt/aipc/conductor/workspace"))

_LF_AUTH = base64.b64encode(f"{LANGFUSE_PK}:{LANGFUSE_SK}".encode()).decode()
_RESULTS: list[dict] = []


# ── HTTP helpers ──────────────────────────────────────────────────────────

def _request(method: str, url: str, body: Any = None, headers: dict | None = None) -> dict | list:
    hdrs = {"content-type": "application/json", **(headers or {})}
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def conductor(path: str, method: str = "GET", body: Any = None) -> dict | list:
    return _request(method, f"{CONDUCTOR}{path}", body)


def aionui(path: str, method: str = "GET", body: Any = None) -> dict | list:
    return _request(method, f"{AIONUI}{path}", body)


def aionui_create_conversation(
    workspace: str,
    preset_agent_type: str = "acp",
    model: str | None = None,
) -> str:
    """Create an AionUi conversation matching the body shape client.py sends."""
    body: dict[str, Any] = {
        "name": f"e2e-{int(time.time())}",
        "type": preset_agent_type,
        "extra": {"workspace": workspace},
    }
    if model:
        body["extra"]["current_model_id"] = model
    if preset_agent_type == "acp":
        body["extra"]["backend"] = "opencode"
    resp = aionui("/api/conversations", "POST", body)
    return resp["data"]["id"]


def langfuse(path: str) -> dict | list:
    req = urllib.request.Request(
        f"{LANGFUSE_HOST}{path}",
        headers={"Authorization": f"Basic {_LF_AUTH}"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


# ── Assertion helpers ─────────────────────────────────────────────────────

_pass_count = 0
_fail_count = 0


def ok(msg: str, evidence: str = "") -> None:
    global _pass_count
    _pass_count += 1
    _RESULTS.append({"status": "PASS", "check": msg, "evidence": evidence})
    print(f"  ✓ {msg}" + (f"  [{evidence}]" if evidence else ""))


def fail(msg: str, evidence: str = "") -> None:
    global _fail_count
    _fail_count += 1
    _RESULTS.append({"status": "FAIL", "check": msg, "evidence": evidence})
    print(f"  ✗ {msg}" + (f"  [{evidence}]" if evidence else ""))


def assert_file(path: str) -> bool:
    p = Path(path)
    if p.exists():
        ok(f"File exists: {path}", f"{p.stat().st_size} bytes")
        return True
    fail(f"File missing: {path}")
    return False


def assert_contains(path: str, pattern: str) -> bool:
    p = Path(path)
    if not p.exists():
        fail(f"File missing for content check: {path}")
        return False
    if pattern in p.read_text():
        ok(f"File {path} contains expected text", pattern[:60])
        return True
    fail(f"File {path} missing text: {pattern[:60]}")
    return False


PYTHON_BIN = os.environ.get(
    "PYTHON_BIN",
    str(Path(__file__).resolve().parent.parent.parent / ".venv" / "bin" / "python3"),
)


def assert_pytest(directory: str) -> bool:
    res = subprocess.run(
        [PYTHON_BIN, "-m", "pytest", "-q", "--no-header"],
        cwd=directory, capture_output=True, text=True, timeout=60,
    )
    if res.returncode == 0:
        ok("pytest passes", res.stdout.strip().split("\n")[-1] if res.stdout else "ok")
        return True
    fail("pytest failed", (res.stderr or res.stdout)[:200])
    return False


def wait_seconds(seconds: int, reason: str = "") -> None:
    print(f"  ⏱  waiting {seconds}s" + (f" — {reason}" if reason else ""))
    time.sleep(seconds)


def get_langfuse_scores(name: str = "goal_review", limit: int = 50) -> list[dict]:
    data = langfuse(f"/api/public/scores?name={name}&limit={limit}")
    return data.get("data", [])


# ── Result reporting ──────────────────────────────────────────────────────

def print_results(scenario: str) -> tuple[int, int]:
    print(f"\n{'='*50}")
    print(f"Scenario {scenario}: {_pass_count} passed, {_fail_count} failed")
    print(f"{'='*50}")
    return _pass_count, _fail_count
