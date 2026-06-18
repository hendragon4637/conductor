"""C2 — Run selected plans: create_run -> approve_run -> start_run.

PLANS_SELECTION env var controls which plans run
(comma-separated plan IDs, default: all 4).
"""

from __future__ import annotations

import os

import httpx
from playwright.sync_api import Page

API_BASE_URL = "http://127.0.0.1:8090"
API_PLANS_URL = f"{API_BASE_URL}/api/plans"
UI_BASE_URL = "http://localhost:3090"

CAP_PLAN_IDS = ["cap_plan_A", "cap_plan_B", "cap_plan_C", "cap_plan_D"]


def _get_selected_plans() -> list[str]:
    raw = os.environ.get("PLANS_SELECTION", "")
    if raw:
        return [p.strip() for p in raw.split(",") if p.strip()]
    return CAP_PLAN_IDS


def _create_run(plan_id: str) -> str:
    r = httpx.post(f"{API_PLANS_URL}/{plan_id}/runs", timeout=15)
    assert r.is_success, f"Create run for {plan_id} failed: {r.status_code} {r.text}"
    run_data = r.json()
    run_id = run_data.get("run_id") or run_data.get("id")
    assert run_id, f"No run_id in response: {run_data}"
    print(f"  Run created {run_id} for {plan_id}")
    return run_id


def _approve_run(run_id: str) -> None:
    r = httpx.post(f"{API_PLANS_URL}/runs/{run_id}/approve", timeout=15)
    assert r.is_success, f"Approve run {run_id} failed: {r.status_code} {r.text}"
    print(f"  Approved run {run_id}")


def _start_run(run_id: str) -> None:
    r = httpx.post(f"{API_PLANS_URL}/runs/{run_id}/start", timeout=15)
    assert r.is_success, f"Start run {run_id} failed: {r.status_code} {r.text}"
    print(f"  Started run {run_id}")


def run(page: Page) -> bool:
    selected = _get_selected_plans()
    print(f"  Selected plans for execution: {selected}")

    run_ids: list[str] = []
    for plan_id in selected:
        run_id = _create_run(plan_id)
        run_ids.append(run_id)

    for run_id in run_ids:
        _approve_run(run_id)

    for run_id in run_ids:
        _start_run(run_id)

    page.goto(f"{UI_BASE_URL}/#/plan")
    page.wait_for_timeout(2000)

    for plan_id in selected:
        list_item = page.locator(f"[data-testid='plan-list-item-{plan_id}']")
        assert list_item.is_visible(), f"Plan {plan_id} not visible"
        list_item.click()
        page.wait_for_timeout(1000)
        status_pill = page.locator("[data-testid='plan-status-pill']")
        assert status_pill.is_visible(), f"Status pill not visible for {plan_id}"

    return True
