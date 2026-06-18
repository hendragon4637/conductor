"""C3 — Session appears for Plan A (opencode plain) and completes.

Skips if cap_plan_A not in PLANS_SELECTION.
"""

from __future__ import annotations

import os

from playwright.sync_api import Page
from stages.common import find_session_row_by_text, wait_for_verdict

UI_BASE_URL = "http://localhost:3090"
PLAN_ID = "cap_plan_A"


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
    # session-table only renders when sessions.length > 0.
    # Watcher polls every 45s; give 2 cycles + margin.
    page.wait_for_selector("[data-testid='session-table']", timeout=120_000)

    row_testid = find_session_row_by_text(page, PLAN_ID)
    assert row_testid is not None, f"{PLAN_ID} session not found"

    row = page.locator(f"[data-testid='{row_testid}']")
    verdict_badge = row.locator("[data-testid='session-verdict-badge']")
    assert verdict_badge.is_visible(), "Verdict badge not visible"

    ok = wait_for_verdict(page, row_testid, timeout=300)
    assert ok, f"Session {row_testid} did not reach 'done' within 300s"

    backend_tag = row.locator("[data-testid='session-backend-tag']")
    assert backend_tag.is_visible(), "Backend tag not visible"

    return True
