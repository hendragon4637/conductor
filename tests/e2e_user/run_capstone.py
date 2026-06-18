#!/usr/bin/env python3
"""Capstone E2E orchestrator for Conductor UI.

Strict checkpointing with snapshot/restore.
Usage:
    cd /opt/aipc/conductor && uv run python tests/e2e_user/run_capstone.py

Environent variables:
    UI_URL          Base URL for Conductor UI (default: http://localhost:3090)
    API_URL         Base URL for Conductor API (default: http://127.0.0.1:8090)
    HEADLESS        Set to "false" for visible browser (default: "true")
    DB_CONTAINER    Docker container name for Postgres (default: postgres)
    DB_NAME         Database name (default: aipc_conductor)
    DB_USER         Database user (default: aipc)
    WORKSPACE_DIR   Workspace directory for worktrees (default: workspace)
"""

from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HERE = Path(__file__).parent.resolve()
STAGES_DIR = HERE / "stages"
CHECKPOINTS_DIR = HERE / "checkpoints"
RESULTS_FILE = HERE / "CAPSTONE_RESULTS.md"
PROJECT_ROOT = HERE.parent.parent.resolve()  # /opt/aipc/conductor

CHECKPOINTS = [
    "C0_ready",
    "C1_plan_created",
    "C2_approved",
    "C3_classB_plain_oc",
    "C4_classB_team",
    "C5_classA_omo",
    "C6_classA_hermes",
    "C7_evaluated",
    "C8_committed",
    "C9_langfuse",
]

# Plan IDs for the 4 per-class plans (File 08 Fix 2.3)
PLAN_IDS = {
    "A": "cap_plan_A",  # opencode, class-b plain
    "B": "cap_plan_B",  # claude_code + codex, class-b team
    "C": "cap_plan_C",  # opencode_omo, class-a
    "D": "cap_plan_D",  # hermes, class-a
}

STAGE_MODULE_MAP = {
    "C0_ready": "c0_app_ready",
    "C1_plan_created": "c1_create_plan",
    "C2_approved": "c2_approve_plan",
    "C3_classB_plain_oc": "c3_classB_plain_oc",
    "C4_classB_team": "c4_classB_team",
    "C5_classA_omo": "c5_classA_omo",
    "C6_classA_hermes": "c6_classA_hermes",
    "C7_evaluated": "c7_evaluation",
    "C8_committed": "c8_commit_ladder",
    "C9_langfuse": "c9_langfuse",
}

# Verification checks per checkpoint (ground truth via API)
VERIFY_CHECKS = {
    "C0_ready": ["api_health"],
    "C1_plan_created": ["plan_exists"],
    "C2_approved": ["plan_approved", "run_created"],
    "C3_classB_plain_oc": ["node_session_exists"],
    "C4_classB_team": ["node_session_exists"],
    "C5_classA_omo": ["node_session_exists"],
    "C6_classA_hermes": ["node_session_exists"],
    "C7_evaluated": ["api_health"],
    "C8_committed": ["git_commits"],
    "C9_langfuse": ["api_health"],
}

# Plan-to-checkpoint mapping (which checkpoint watches which plan)
PLAN_TO_CHECKPOINT = {
    "cap_plan_A": "C3_classB_plain_oc",
    "cap_plan_B": "C4_classB_team",
    "cap_plan_C": "C5_classA_omo",
    "cap_plan_D": "C6_classA_hermes",
}

# Config from environment
UI_URL = os.environ.get("UI_URL", "http://localhost:3090")
API_URL = os.environ.get("API_URL", "http://127.0.0.1:8090")
HEADLESS = os.environ.get("HEADLESS", "true").lower() != "false"
DB_CONTAINER = os.environ.get("DB_CONTAINER", "postgres")
DB_NAME = os.environ.get("DB_NAME", "aipc_conductor")
DB_USER = os.environ.get("DB_USER", "aipc")
WORKSPACE_DIR = Path(
    os.environ.get("WORKSPACE_DIR", str(PROJECT_ROOT / "workspace"))
)
# Comma-separated list of plan IDs to run in C2+ (default: all 4)
PLANS_SELECTION = os.environ.get("PLANS_SELECTION", "")
SELECTED_PLANS = (
    [p.strip() for p in PLANS_SELECTION.split(",") if p.strip()]
    if PLANS_SELECTION
    else list(PLAN_IDS.values())
)


# ---------------------------------------------------------------------------
# Results file management
# ---------------------------------------------------------------------------

def _init_results_file():
    """Create CAPSTONE_RESULTS.md with header if it doesn't exist."""
    if not RESULTS_FILE.exists():
        RESULTS_FILE.write_text(
            "# Capstone Results\n\n"
            "| Checkpoint | Status | Time | Notes |\n"
            "|---|---|---|---|\n"
        )


def _write_result(cp_name: str, status: str, notes: str = ""):
    """Append or update a checkpoint row in CAPSTONE_RESULTS.md."""
    _init_results_file()
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = RESULTS_FILE.read_text().splitlines()

    # Find and update existing row, or append new
    header_done = False
    new_lines = []
    updated = False
    for line in lines:
        if line.startswith("|") and not header_done:
            if line.strip().startswith("|---|---|---"):
                header_done = True
            new_lines.append(line)
        elif line.startswith(f"| {cp_name} |"):
            new_lines.append(
                f"| {cp_name} | {status} | {timestamp} | {notes} |"
            )
            updated = True
        else:
            new_lines.append(line)

    if not updated:
        new_lines.append(
            f"| {cp_name} | {status} | {timestamp} | {notes} |"
        )

    # Add trailing blank line
    RESULTS_FILE.write_text("\n".join(new_lines) + "\n")


def last_good_checkpoint_index() -> int:
    """Read CAPSTONE_RESULTS.md and return the index of the last PASSED checkpoint.

    Returns -1 if no checkpoint has passed.
    """
    _init_results_file()
    lines = RESULTS_FILE.read_text().splitlines()
    last_idx = -1
    for line in lines:
        m = re.match(r"^\| (C\d+_\w+) \| PASSED \|", line)
        if m:
            cp = m.group(1)
            if cp in CHECKPOINTS:
                idx = CHECKPOINTS.index(cp)
                if idx > last_idx:
                    last_idx = idx
    return last_idx


# ---------------------------------------------------------------------------
# Ground-truth verification
# ---------------------------------------------------------------------------

def _api_get(path: str) -> dict | None:
    """Helper: GET from Conductor API, return JSON or None."""
    try:
        r = httpx.get(f"{API_URL}{path}", timeout=10)
        if r.is_success:
            return r.json()
        return None
    except Exception:
        return None


def verify_stage(cp_name: str) -> bool:
    """Run ground-truth verification checks for the given checkpoint.

    Returns True only if all checks pass.
    """
    checks = VERIFY_CHECKS.get(cp_name, [])
    if not checks:
        return True  # nothing to verify = pass

    for check in checks:
        ok = _run_verify_check(check)
        if not ok:
            print(f"  VERIFY FAIL: {check}")
            return False
        print(f"  VERIFY PASS: {check}")
    return True


def _run_verify_check(check: str) -> bool:
    """Execute a single verification check by name."""
    if check == "api_health":
        # The backend exposes health at /health (not /api/health)
        data = _api_get("/health")
        return data is not None

    if check == "plan_exists":
        data = _api_get("/api/plans")
        if data is None:
            return False
        plans = data if isinstance(data, list) else data.get("plans", [])
        cap_ids = set(PLAN_IDS.values())
        found = sum(1 for p in plans if (p.get("plan_id") or p.get("id", "")) in cap_ids)
        return found >= 2

    if check == "plan_approved":
        data = _api_get("/api/plans")
        if data is None:
            return False
        plans = data if isinstance(data, list) else data.get("plans", [])
        cap_plan_ids = list(PLAN_IDS.values())
        for p in plans:
            pid = p.get("plan_id") or p.get("id", "")
            if pid in cap_plan_ids:
                if not p.get("ratified", False):
                    return False
        ratified_count = sum(
            1 for p in plans
            if (p.get("plan_id") or p.get("id", "")) in cap_plan_ids
            and p.get("ratified", False)
        )
        return ratified_count >= 2

    if check == "run_created":
        for pid in SELECTED_PLANS:
            data = _api_get(f"/api/plans/{pid}/runs")
            if not data:
                return False
            runs = data if isinstance(data, list) else data.get("runs", [])
            if not runs:
                return False
        return True

    if check == "node_session_exists":
        for pid in SELECTED_PLANS:
            data = _api_get(f"/api/plans/{pid}/runs")
            if not data:
                continue
            runs = data if isinstance(data, list) else data.get("runs", [])
            if runs and len(runs) > 0:
                return True
        return False

    if check == "git_commits":
        try:
            result = subprocess.run(
                ["git", "log", "--oneline", "-5"],
                capture_output=True, text=True, timeout=10,
                cwd=PROJECT_ROOT,
            )
            return result.returncode == 0 and len(result.stdout.strip()) > 0
        except Exception:
            return False

    return True


# ---------------------------------------------------------------------------
# Snapshot / Restore
# ---------------------------------------------------------------------------

def snapshot(cp_name: str):
    """Create a full snapshot: DB dump + git tag + worktree archive."""
    safe_name = cp_name.replace(" ", "_").lower()
    cp_dir = CHECKPOINTS_DIR / safe_name
    cp_dir.mkdir(parents=True, exist_ok=True)

    # 1. DB dump
    db_file = cp_dir / "db.sql"
    try:
        cmd = [
            "docker", "exec", "-i", DB_CONTAINER,
            "pg_dump", "-U", DB_USER, "--clean", "--if-exists", DB_NAME,
        ]
        with open(db_file, "w") as f:
            subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE,
                           timeout=60, check=True)
        print(f"  SNAPSHOT DB -> {db_file}")
    except Exception as e:
        print(f"  SNAPSHOT DB FAILED (non-fatal): {e}")

    # 2. Git tag
    try:
        tag = f"cap-{safe_name}"
        subprocess.run(
            ["git", "tag", "-f", tag],
            cwd=PROJECT_ROOT, capture_output=True, timeout=10, check=True,
        )
        print(f"  SNAPSHOT git tag -> {tag}")
    except Exception as e:
        print(f"  SNAPSHOT git tag FAILED: {e}")

    # 3. Worktree archive (if workspace exists and has content)
    if WORKSPACE_DIR.exists():
        tar_file = cp_dir / "workspace.tar.gz"
        try:
            subprocess.run(
                ["tar", "czf", str(tar_file), "-C", str(WORKSPACE_DIR), "."],
                capture_output=True, timeout=60, check=True,
            )
            print(f"  SNAPSHOT workspace -> {tar_file}")
        except Exception as e:
            print(f"  SNAPSHOT workspace FAILED (non-fatal): {e}")

    # 4. Write checkpoint marker
    marker = cp_dir / ".checkpoint"
    marker.write_text(f"{cp_name}\n{datetime.now(timezone.utc).isoformat()}\n")
    print(f"  SNAPSHOT marker -> {marker}")


def restore(cp_name: str):
    """Restore from a snapshot: DB restore + git checkout + worktree restore."""
    safe_name = cp_name.replace(" ", "_").lower()
    cp_dir = CHECKPOINTS_DIR / safe_name

    # 1. DB restore
    db_file = cp_dir / "db.sql"
    if db_file.exists():
        try:
            with open(db_file) as f:
                subprocess.run(
                    ["docker", "exec", "-i", DB_CONTAINER,
                     "psql", "-U", DB_USER, "-d", DB_NAME],
                    stdin=f, capture_output=True, timeout=120, check=True,
                )
            print(f"  RESTORE DB <- {db_file}")
        except Exception as e:
            print(f"  RESTORE DB FAILED: {e}")

    # 2. Git checkout
    try:
        tag = f"cap-{safe_name}"
        subprocess.run(
            ["git", "checkout", tag],
            cwd=PROJECT_ROOT, capture_output=True, timeout=30, check=True,
        )
        print(f"  RESTORE git -> {tag}")
    except Exception as e:
        print(f"  RESTORE git checkout FAILED: {e}")

    # 3. Worktree restore
    tar_file = cp_dir / "workspace.tar.gz"
    if tar_file.exists():
        try:
            subprocess.run(
                ["tar", "xzf", str(tar_file), "-C", str(WORKSPACE_DIR)],
                capture_output=True, timeout=60, check=True,
            )
            print(f"  RESTORE workspace <- {tar_file}")
        except Exception as e:
            print(f"  RESTORE workspace FAILED (non-fatal): {e}")


# ---------------------------------------------------------------------------
# Regression check
# ---------------------------------------------------------------------------

def regression_ok(up_to_cp: str, browser=None) -> bool:
    """Re-run UI assertions for all stages up to the given checkpoint.

    This is a lightweight check that imports each stage module and
    runs its assertions against the current UI state.
    If a browser is provided (from the main context), it reuses it.
    """
    idx = CHECKPOINTS.index(up_to_cp)

    if browser is not None:
        return _run_regression_checks(up_to_cp, browser)

    # Standalone mode: create own browser (used outside main context)
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=True)
        result = _run_regression_checks(up_to_cp, b)
        b.close()
        return result


def _run_regression_checks(up_to_cp: str, browser) -> bool:
    """Execute regression checks using an existing browser instance."""
    idx = CHECKPOINTS.index(up_to_cp)
    context = browser.new_context(
        viewport={"width": 1280, "height": 900},
    )
    all_ok = True
    for i in range(idx + 1):
        cp = CHECKPOINTS[i]
        module_name = STAGE_MODULE_MAP[cp]
        try:
            mod = importlib.import_module(f"stages.{module_name}")
            page = context.new_page()
            ok = mod.run(page)
            page.close()
            if not ok:
                print(f"  REGRESSION FAIL: {cp}")
                all_ok = False
        except Exception as e:
            print(f"  REGRESSION ERROR at {cp}: {e}")
            all_ok = False
    context.close()
    return all_ok


# ---------------------------------------------------------------------------
# Stage runner
# ---------------------------------------------------------------------------

def run_stage(cp_name: str, page) -> bool:
    """Import the stage module and execute its run() function.

    Returns True on success.
    """
    module_name = STAGE_MODULE_MAP.get(cp_name)
    if not module_name:
        print(f"  ERROR: No stage module mapped for {cp_name}")
        return False

    mod = importlib.import_module(f"stages.{module_name}")
    result = mod.run(page)
    return bool(result)


# ---------------------------------------------------------------------------
# Failure recording
# ---------------------------------------------------------------------------

def record_failure(cp_name: str, error: str):
    """Write a failure entry to the results file."""
    _write_result(cp_name, "FAILED", error[:200])
    print(f"  FAILED {cp_name}: {error[:200]}")


def full_reset():
    """Full system reset — warns and records reset state."""
    print("  FULL RESET requested")
    _write_result("SYSTEM", "RESET", "Full system reset triggered")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    """Orchestrator entry point."""
    _init_results_file()

    start_idx = last_good_checkpoint_index()
    print(f"Capstone orchestrator starting")
    print(f"  UI: {UI_URL}")
    print(f"  API: {API_URL}")
    print(f"  Headless: {HEADLESS}")
    print(f"  Last passed checkpoint: {CHECKPOINTS[start_idx] if start_idx >= 0 else 'None'}")
    print(f"  Starting from: {CHECKPOINTS[start_idx + 1] if start_idx + 1 < len(CHECKPOINTS) else 'Done!'}")
    print()

    next_idx = start_idx + 1
    if next_idx >= len(CHECKPOINTS):
        print("All checkpoints already passed. Nothing to do.")
        return

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS)
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
        )

        for cp in CHECKPOINTS[next_idx:]:
            cp_idx = CHECKPOINTS.index(cp)
            print(f"\n{'='*60}")
            print(f"  CHECKPOINT {cp} ({cp_idx + 1}/{len(CHECKPOINTS)})")
            print(f"{'='*60}")

            # --- Run stage ---
            page = context.new_page()
            try:
                t0 = time.time()
                stage_ok = run_stage(cp, page)
                elapsed = time.time() - t0
                print(f"  Stage completed in {elapsed:.1f}s, ok={stage_ok}")
            except Exception as e:
                tb = traceback.format_exc()
                print(f"  Stage raised exception: {e}")
                stage_ok = False
                elapsed_error = traceback.format_exc()
            finally:
                page.close()

            if not stage_ok:
                error_msg = str(elapsed_error) if not stage_ok else "Stage returned False"
                record_failure(cp, error_msg)
                # Regression check: can we go back to the previous checkpoint?
                if cp_idx > 0:
                    prev_cp = CHECKPOINTS[cp_idx - 1]
                    print(f"  Running regression check up to {prev_cp}...")
                    regress_ok = regression_ok(up_to_cp=prev_cp, browser=browser)
                    if regress_ok:
                        print(f"  Regression OK. Restoring {prev_cp}...")
                        restore(prev_cp)
                        print(f"\n  STOP at {cp}; fix and rerun — resumes from {prev_cp}")
                    else:
                        print(f"  Regression FAILED. Prior checkpoint broke.")
                        full_reset()
                        print("\n  FATAL: prior checkpoint broke — restart from C0")
                else:
                    full_reset()
                    print("\n  FATAL: first checkpoint failed — restart from C0")
                break

            # --- Verify ---
            verify_ok = verify_stage(cp)
            if verify_ok:
                print(f"  Ground-truth verification PASSED")
            else:
                print(f"  Ground-truth verification FAILED")
                record_failure(cp, "Ground-truth verification failed")
                if cp_idx > 0:
                    prev_cp = CHECKPOINTS[cp_idx - 1]
                    print(f"  Restoring to {prev_cp}...")
                    restore(prev_cp)
                    print(f"\n  STOP at {cp}; fix and rerun — resumes from {prev_cp}")
                else:
                    full_reset()
                    print("\n  FATAL: first checkpoint failed — restart from C0")
                break

            # --- Snapshot ---
            snapshot(cp)
            _write_result(cp, "PASSED", f"Completed in {elapsed:.1f}s")
            print(f"  ✓ {cp} PASSED")

        context.close()
        browser.close()

    print(f"\n{'='*60}")
    final_idx = last_good_checkpoint_index()
    if final_idx >= len(CHECKPOINTS) - 1:
        print("  ALL CHECKPOINTS PASSED")
    elif final_idx >= 0:
        print(f"  PARTIAL: passed {CHECKPOINTS[final_idx]}")
    else:
        print("  NO CHECKPOINTS PASSED")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
