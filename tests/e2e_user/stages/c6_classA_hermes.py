"""C6 — Session appears for Plan D (hermes) and completes.

Skips if cap_plan_D not in PLANS_SELECTION.
"""

from __future__ import annotations

import os

from playwright.sync_api import Page
from .common import find_session_row_by_text, wait_for_verdict

UI_BASE_URL = "http://localhost:3090"
PLAN_ID = "cap_plan_D"


def _is_selected() -> bool:
    raw = os.environ.get("PLANS_SELECTION", "")
    if not raw:
        return True
    return PLAN_ID in [p.strip() for p in raw.split(",")]


def run(page: Page) -> bool:
    if not _is_selected():
        print(f"  SKIP: {PLAN_ID} not in PLANS_SELECTION")
        return True

    page.goto(f"{UI_BASE_URL}/#/sessions")
    page.wait_for_selector("[data-testid='session-table']", timeout=120_000)

    row_testid = find_session_row_by_text(page, PLAN_ID)
    assert row_testid is not None, f"{PLAN_ID} session not found"

    row = page.locator(f"[data-testid='{row_testid}']")
    assert row.is_visible(), "Session row not visible"

    verdict = row.locator("[data-testid='session-verdict-badge']")
    assert verdict.is_visible(), "Verdict badge not visible"

    ok = wait_for_verdict(page, row_testid, timeout=300)
    assert ok, f"Session {row_testid} did not reach 'done' within 300s"

    return True
