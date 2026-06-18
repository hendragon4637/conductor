from __future__ import annotations

import sys

sys.path.insert(0, "/opt/aipc/conductor")

from backend.orchestration.orchestrator_brief import build_orchestrator_brief, POLL_MIN_SECONDS


PASS = 0
FAIL = 0


def check(desc: str, ok: bool):
    global PASS, FAIL
    if ok:
        print(f"  PASS: {desc}")
        PASS += 1
    else:
        print(f"  FAIL: {desc}")
        FAIL += 1


node = {
    "task": "Implement the finance tracker application end to end.",
    "success": "The implementation is complete and verified.",
}
members = [
    {
        "agent_config_id": "finance-executor",
        "role": "executor",
        "task": "Implement the FastAPI CRUD + tests",
        "success": "CRUD endpoints and tests pass",
        "depends_on": [],
    },
    {
        "agent_config_id": "finance-reviewer",
        "role": "reviewer",
        "task": "Review and run the app",
        "success": "Review report and readiness verdict",
        "depends_on": ["finance-executor"],
    },
]
brief = build_orchestrator_brief(node, members, "git diff from dependency")

print("\n=== Gate 21 brief checks ===")
check("brief forbids self implementation", "do NOT implement" in brief or "do NOT write code" in brief)
check("brief forbids own subagents", "Do NOT spawn your own subagents" in brief)
check("brief lists team members", "finance-executor" in brief and "finance-reviewer" in brief)
check("brief includes assignment plan", "Assignment plan:" in brief)
check("brief includes monitoring throttle", f"AT MOST once every {POLL_MIN_SECONDS} seconds" in brief)
check("brief includes dependency context", "git diff from dependency" in brief)

print(f"\nGate 21 brief results: {PASS} passed, {FAIL} failed")
if FAIL:
    raise SystemExit(1)
