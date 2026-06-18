"""C1 — Create + ratify all 4 plans. NO runs created yet.

C1 stops BEFORE backend spawn (ratify only — no create_run/approve_run/start_run).
This keeps C1 fast and safe for reruns. Actual execution happens in C2+.
"""

from __future__ import annotations

import httpx
from playwright.sync_api import Page

API_BASE_URL = "http://127.0.0.1:8090"
UI_BASE_URL = "http://localhost:3090"

PLANS = [
    {
        "id": "cap_plan_A",
        "title": "Cap A: OpenCode plain",
        "description": "Build a CLI task manager with add/list/complete/delete commands and JSON file persistence",
        "spec": "Build a Python CLI task manager that supports: adding tasks (with title, description, priority), listing tasks (with optional status/priority filters), marking tasks complete, deleting tasks, and showing task details. Tasks persist to ~/.task-manager/tasks.json. Use argparse or click for CLI. Include unit tests.",
        "quality_intent": "Node 1: Build CLI task manager with add/list/complete/delete/show commands, priority levels, JSON persistence, and tests (backend=opencode, class-b)",
        "backend_type": "opencode",
        "nodes": [{
            "title": "Build CLI Task Manager",
            "description": "Build a Python CLI task manager with: add (title, description, priority low/medium/high), list (filterable by status/priority), complete, delete, and show commands. Persist to ~/.task-manager/tasks.json using JSON. Use argparse for CLI. Include error handling, type hints, and tests.",
            "success_criterion": "All 5 CLI commands work (add/list/complete/delete/show). Tasks persist to JSON file across restarts. Priority filtering and status filtering work correctly. Tests pass for all commands and storage.",
            "backend": "opencode"
        }],
    },
    {
        "id": "cap_plan_B",
        "title": "Cap B: Team (Claude Code + Codex)",
        "description": "Build a markdown blog engine using Claude Code + Codex team",
        "spec": "A markdown-to-HTML blog engine with watch mode and template support",
        "quality_intent": "Node 1: Build markdown blog engine (backend=aionui, class-b, members=claude-code+codex)",
        "backend_type": "aionui",
        "nodes": [{"title": "Build markdown blog engine", "backend": "aionui", "members": ["claude-code", "codex"]}],
    },
    {
        "id": "cap_plan_C",
        "title": "Cap C: OpenCode OMO",
        "description": "Build a URL shortener service using OpenCode OMO",
        "spec": "A URL shortener API with create/redirect/stats endpoints, SQLite backend",
        "quality_intent": "Node 1: Build URL shortener (backend=opencode_omo, class-a)",
        "backend_type": "opencode_omo",
        "nodes": [{"title": "Build URL shortener", "backend": "opencode_omo"}],
    },
    {
        "id": "cap_plan_D",
        "title": "Cap D: Hermes",
        "description": "Build a JSON schema validator using Hermes",
        "spec": "A JSON schema validation library with CLI and Python API",
        "quality_intent": "Node 1: Build JSON schema validator (backend=hermes, class-a)",
        "backend_type": "hermes",
        "nodes": [{"title": "Build JSON schema validator", "backend": "hermes"}],
    },
]


def _create_plan(payload: dict) -> str:
    body = {**payload, "plan_id": payload.get("id")}
    r = httpx.post(f"{API_BASE_URL}/api/plans", json=body, timeout=15)
    assert r.is_success, f"Create plan {payload['id']} failed: {r.status_code} {r.text}"
    plan = r.json()
    pid = plan.get("plan_id") or plan.get("id")
    assert pid, f"No plan_id in response: {plan}"
    assert plan.get("nodes"), f"Plan {pid} has no nodes (decomposition failed)"
    print(f"  Created plan {pid}: {len(plan['nodes'])} node(s)")
    return pid


def _ratify_plan(plan_id: str) -> None:
    r = httpx.post(f"{API_BASE_URL}/api/plans/{plan_id}/ratify",
                   json={"ratified": True}, timeout=15)
    assert r.is_success, f"Ratify {plan_id} failed: {r.status_code} {r.text}"
    print(f"  Ratified {plan_id}")


def run(page: Page) -> bool:
    plan_ids: list[str] = []

    for p in PLANS:
        pid = _create_plan(p)
        plan_ids.append(pid)

    # Ratify all plans — no runs yet
    for pid in list(plan_ids):
        _ratify_plan(pid)

    page.goto(f"{UI_BASE_URL}/#/plan")
    page.wait_for_timeout(2000)

    for pid in plan_ids:
        list_item = page.locator(f"[data-testid='plan-list-item-{pid}']")
        assert list_item.is_visible(), f"Plan {pid} not visible in sidebar list"
        list_item.click()
        page.wait_for_timeout(1000)

        detail = page.locator("[data-testid='plan-detail']")
        assert detail.is_visible(), f"Plan detail for {pid} not visible"

        try:
            page.wait_for_selector("[data-testid^='node-card-']", timeout=10000)
        except Exception:
            pass

        node_cards = page.locator("[data-testid^='node-card-']")
        count = node_cards.count()
        assert count > 0, f"Expected at least 1 node card for {pid}, got {count}"

        for i in range(count):
            card = page.locator(f"[data-testid='node-card-{i}']")
            if card.is_visible():
                backend_tag = card.locator("[data-testid='node-backend-tag']")
                assert backend_tag.is_visible(), f"Node card {i} missing backend tag"

    return True