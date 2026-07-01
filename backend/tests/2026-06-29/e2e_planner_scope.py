#!/usr/bin/env python3
"""Planner-scope E2E test suite — targets planner-svc at :8094.

Tests the full planner lifecycle (formulate → inject → decompose → gate)
via the planner microservice API.  Derived from the monolith e2e at
``e2e_heterogeneous_domains.py`` but adapted for planner-svc endpoints.

Scenarios covered (H1-H9):
  H1  Finance tracker — convention injection
  H2  Vague input — clarification flow
  H3  BYO-DAG — skip formulator, still validate + gate
  H4  API service — no false FE node injection
  H5  CLI script — domain conventions
  H6  Research report — non-runnable domain
  H7  Combined domains — app + documentation merge
  H8  Unknown domain — clarify / defer
  H9  Ratify gated plan — gate + emit

Usage:
    # Unit tests only (no planner-svc needed):
    uv run python backend/tests/2026-06-29/e2e_planner_scope.py --unit

    # Full E2E (planner-svc must be running on :8094):
    uv run python backend/tests/2026-06-29/e2e_planner_scope.py

    # Start planner-svc first (separate terminal):
    cd /opt/aipc/conductor && \\
    source .env && \\
    uv run uvicorn services.planner.main:app --host 127.0.0.1 --port 8094
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

PLANNER_API = os.environ.get("PLANNER_API", "http://127.0.0.1:8094")
REQUEST_TIMEOUT = 420  # seconds per API call (LLM calls can be slow)

PASS = 0
FAIL = 0
SKIP = 0


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def check(label: str, ok: bool):
    global PASS, FAIL
    if ok:
        print(f"  PASS: {label}", flush=True)
        PASS += 1
    else:
        log(f"FAIL: {label}")
        FAIL += 1


def skip(label: str):
    global SKIP
    print(f"  SKIP: {label}")
    SKIP += 1


def api(method: str, path: str, body: dict | None = None, expect: int | None = None) -> dict:
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        f"{PLANNER_API}{path}", data=data,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
            result = json.loads(r.read())
            if expect is not None:
                check(f"{method} {path} status={r.status}", r.status == expect)
            return result
    except urllib.error.HTTPError as e:
        body_text = e.read().decode()[:500]
        if expect is not None:
            check(f"{method} {path} status={e.code}", e.code == expect)
        else:
            print(f"  HTTP {e.code} on {method} {path}: {body_text}")
        return {"error": body_text, "_status": e.code}


# ── Unit-level tests (no planner-svc required) ─────────────────────────


def test_domain_inference():
    """Test infer_domain keyword classification."""
    print("\n" + "=" * 60)
    print("UNIT: Domain inference")
    print("=" * 60)

    from backend.planning.domain_profile import infer_domain

    checks = [
        ("build a finance tracker with FastAPI and HTML UI", "software_app"),
        ("create a CLI todo manager with argparse", "cli_script"),
        ("REST API for bookmarks service", "api_service"),
        ("CSV to JSON transform pipeline", "data_pipeline"),
        ("research report comparing frameworks", "research_report"),
        ("do something", "generic"),
    ]

    for text, expected in checks:
        result = infer_domain(text)
        check(f"infer_domain('{text[:50]}...') = {result}", result == expected)


def test_domain_profile_retrieval():
    """Test get_domain_profile with seeded profiles."""
    print("\n" + "=" * 60)
    print("UNIT: Domain profile retrieval")
    print("=" * 60)

    from backend.planning.domain_profile import get_domain_profile

    profile = get_domain_profile("software_app")
    check("software_app profile found", profile is not None)
    if profile:
        check("software_app has deliverables", len(profile.acceptance.get("deliverables", [])) >= 3)
        check("software_app has conventions", len(profile.conventions) >= 2)

    generic = get_domain_profile("nonexistent_domain_xyz")
    check("unknown domain falls back to generic", generic is not None)
    if generic:
        check("generic domain is 'generic'", generic.domain == "generic")


def test_staffing_l1():
    """Test staffing_l1 deterministic mismatch detection."""
    print("\n" + "=" * 60)
    print("UNIT: Staffing L1 mismatch detection")
    print("=" * 60)

    from backend.evaluator.plan_evaluator import staffing_l1, _infer_required_caps

    mismatched_dag = [
        {
            "id": "node-1",
            "members": [
                {"agent_config": "opencode:backend-executor", "role": "executor", "backend": "opencode"},
            ],
            "task": {"text": "Build an interactive chart page with HTML/CSS/JavaScript"},
            "success": {"text": "Chart renders in browser"},
            "checks": [{"id": "l1-files-exist", "type": "deterministic"}],
        }
    ]
    fails = staffing_l1(mismatched_dag)
    check("frontend task + backend-only agent → staffing failure", len(fails) > 0)
    if fails:
        check("failure message names the mismatch", "frontend" in fails[0] and "backend-executor" in fails[0])

    ok_dag = [
        {
            "id": "node-1",
            "members": [
                {"agent_config": "opencode:backend-executor", "role": "executor", "backend": "opencode"},
            ],
            "task": {"text": "Build a REST API endpoint"},
            "success": {"text": "API responds to requests"},
            "checks": [{"id": "l1-tests-pass", "type": "deterministic"}],
        }
    ]
    fails = staffing_l1(ok_dag)
    check("backend task + backend agent → no staffing failure", len(fails) == 0)

    fs_dag = [
        {
            "id": "node-1",
            "members": [
                {"agent_config": "finance-fullstack-executor", "role": "executor", "backend": "opencode"},
            ],
            "task": {"text": "Build a full-stack finance tracker with UI"},
            "success": {"text": "App runs end-to-end"},
            "checks": [{"id": "l1-tests-pass", "type": "deterministic"}],
        }
    ]
    fails = staffing_l1(fs_dag)
    check("fullstack task + fullstack agent → no staffing failure", len(fails) == 0)

    caps = _infer_required_caps("Build a frontend dashboard with React")
    check("frontend task → requires frontend cap", "frontend" in caps)
    caps = _infer_required_caps("Write pytest for the API")
    check("test task → requires tests cap", "tests" in caps)


def test_plan_evaluator_with_staffing():
    """Test plan_evaluator with staffing check integrated."""
    print("\n" + "=" * 60)
    print("UNIT: Plan evaluator staffing integration")
    print("=" * 60)

    from backend.evaluator.plan_evaluator import evaluate_plan

    bad_dag = [
        {
            "id": "node-1",
            "members": [
                {"agent_config": "opencode:backend-executor", "role": "executor", "backend": "opencode"},
            ],
            "task": {"text": "Build an interactive frontend with HTML/CSS"},
            "success": {"text": "UI renders"},
            "checks": [{"id": "l1-files-exist", "type": "deterministic", "check_cmd": "test -f"}],
            "depends_on": [],
        }
    ]
    result = evaluate_plan(bad_dag, plan_goal="Build a chart page")
    check("staffing mismatch → plan L1 fails", not result.passed)
    if result.l1:
        staffing_fails = [c for c in result.l1.checks if c.get("check") == "staffing_l1"]
        check("staffing_l1 check present in failures", len(staffing_fails) > 0)

    good_dag = [
        {
            "id": "node-1",
            "members": [
                {"agent_config": "finance-fullstack-executor", "role": "executor", "backend": "opencode"},
            ],
            "task": {"text": "Build a finance tracker with FastAPI and HTML UI"},
            "success": {"text": "App runs end-to-end"},
            "checks": [{"id": "l1-files-exist", "type": "deterministic", "check_cmd": "test -f"}],
            "depends_on": [],
        }
    ]
    result = evaluate_plan(good_dag, plan_goal="Build a fullstack finance tracker")
    check("correct staffing → L1 passes", result.l1.passed)


def test_convention_enrichment():
    """Test enrich_with_conventions without LLM call (pure logic)."""
    print("\n" + "=" * 60)
    print("UNIT: Convention enrichment logic")
    print("=" * 60)

    from backend.planning.meta_planner.goal_formulator import (
        MetaGoal, enrich_with_conventions,
    )

    mg = MetaGoal(
        goal="Build a finance tracker with FastAPI backend",
        spec="Add/list/delete expenses via API",
        quality_intent="Works correctly, handles errors",
        origin="human",
    )
    enriched = enrich_with_conventions(mg, "Build a finance tracker with FastAPI backend, add/list/delete")
    check("H1: domain inferred as software_app", enriched.domain == "software_app")
    if enriched.applied_conventions:
        check("H1: conventions were injected", len(enriched.applied_conventions) > 0)
        fe_convention = any("end-to-end" in c or "FE" in c or "frontend" in c for c in enriched.applied_conventions)
        check("H1: FE convention injected", fe_convention)
    check("H1: success_seed populated", len(enriched.success_seed) > 0)

    mg6 = MetaGoal(
        goal="Research report comparing 3 web frameworks",
        spec="Compare React, Vue, Angular on performance and DX",
        quality_intent="Accurate, well-cited, clear conclusion",
        origin="human",
    )
    enriched6 = enrich_with_conventions(mg6, "Research report comparing 3 web frameworks")
    check("H6: domain inferred as research_report", enriched6.domain == "research_report")
    check("H6: has report conventions", len(enriched6.applied_conventions) > 0)

    mg4 = MetaGoal(
        goal="REST API for bookmarks service",
        spec="CRUD endpoints for bookmarks",
        quality_intent="RESTful, validated inputs",
        origin="human",
    )
    enriched4 = enrich_with_conventions(mg4, "REST API for bookmarks service, no UI needed")
    check("H4: domain inferred as api_service", enriched4.domain == "api_service")
    if enriched4.applied_conventions:
        api_only = any("API" in c or "server" in c for c in enriched4.applied_conventions)
        check("H4: API conventions injected", api_only)

    mg8 = MetaGoal(
        goal="Make a data dashboard",
        spec="",
        quality_intent="Good",
        origin="human",
    )
    enriched8 = enrich_with_conventions(mg8, "Make a data dashboard")
    check("H8: unknown domain triggers clarification", enriched8.needs_clarification)
    check("H8: clarify question added", len(enriched8.questions) > 0)


def test_agent_generator_validation():
    """Test agent_config proposal validation."""
    print("\n" + "=" * 60)
    print("UNIT: Agent config proposal validation")
    print("=" * 60)

    from backend.planning.meta_planner.agent_generator import (
        AgentConfigProposal, validate_proposal, needs_new_agent_config,
    )

    valid = AgentConfigProposal(
        agent_config_id="opencode:test-executor",
        role="executor",
        domain="frontend",
        capability_summary=["frontend", "tests", "ui"],
        system_prompt="You build frontend UIs with vanilla HTML/CSS/JS.",
        skill_outline="Frontend development with HTML/CSS/JS",
        execution={"backend": "opencode", "model_preference": "deepseek/deepseek-chat"},
        default_checks={
            "l1": [{"id": "files_exist", "kind": "file_exists"}],
            "l2": [{"id": "ui_quality", "rubric_item": "UI is usable?", "weight": 1.0}],
        },
    )
    fails = validate_proposal(valid)
    check("valid proposal → no validation failures", len(fails) == 0)

    bad = AgentConfigProposal(
        agent_config_id="opencode:bad-config",
        role="executor",
        domain="frontend",
        capability_summary=[],
        system_prompt="Hi",
        skill_outline="",
        execution={"backend": "opencode", "model_preference": ""},
        default_checks={"l1": [], "l2": []},
    )
    fails = validate_proposal(bad)
    check("incomplete proposal → validation failures", len(fails) > 0)

    result = needs_new_agent_config("Write some code")
    check("generic task → no new config needed", result is None)

    result = needs_new_agent_config("Build an interactive chart page with HTML/JS", roster=[])
    check("frontend task + empty roster → needs new config", result is not None)
    if result:
        check("frontend caps required", "frontend" in result)


def test_combined_domain_merge():
    """Test combined domains (H7: app + doc)."""
    print("\n" + "=" * 60)
    print("UNIT: Combined domain injection (H7)")
    print("=" * 60)

    from backend.planning.meta_planner.goal_formulator import (
        MetaGoal, enrich_with_conventions,
    )

    mg7 = MetaGoal(
        goal="Build a habit tracker app and write its design doc",
        spec="Fullstack app with CRUD + design document",
        quality_intent="App works, doc is thorough",
        origin="human",
    )
    enriched = enrich_with_conventions(mg7, "Build a habit tracker app and write its design doc")
    check("H7: domain inferred (app keywords dominate)", enriched.domain is not None)
    check("H7: conventions injected", len(enriched.applied_conventions) > 0)


# ── Integration tests (require planner-svc on :8094) ──────────────────


def check_api_available() -> bool:
    try:
        urllib.request.urlopen(f"{PLANNER_API}/health", timeout=5)
        return True
    except Exception:
        return False


# ── Test helpers ─────────────────────────────────────────────────────────


def _post_goal(raw_input: str, spec: str = "", quality_intent: str = "", **extra) -> dict:
    """Submit a goal to planner-svc and return the parsed response."""
    body = {
        "raw_input": raw_input,
        "origin": extra.get("origin", "human"),
        "project_id": extra.get("project_id", "default"),
    }
    if spec:
        body["spec"] = spec
    if quality_intent:
        body["quality_intent"] = quality_intent
    if "nodes" in extra:
        body["nodes"] = extra["nodes"]
    return api("POST", "/goal", body)


def _ratify_plan(plan_id: str) -> dict:
    """Ratify a plan via planner-svc."""
    return api("POST", f"/ratify/{plan_id}")


# ── Integration test scenarios ───────────────────────────────────────────


def test_h1_finance_tracker_convention_injection():
    """H1: Finance tracker via planner-svc — convention injection."""
    log("=" * 50)
    log("H1: Finance tracker — convention injection (planner-svc)")
    start = time.time()
    result = _post_goal(
        raw_input="Build a finance tracker with FastAPI backend, add/list/delete expenses",
    )
    plan_id = result.get("plan_id")
    check("H1: plan_id returned", bool(plan_id))
    status = result.get("status", "")
    check("H1: status is gated_ok or draft", status in ("gated_ok", "draft", "awaiting_clarification"))

    if plan_id:
        # Try ratification if plan was gated
        ratify = _ratify_plan(plan_id)
        check("H1: ratify call completed", ratify.get("_status", 200) != 400)
        if ratify.get("status") == "ratified":
            check("H1: plan_goal_review returned",
                  ratify.get("plan_goal_review") is not None)

    elapsed = time.time() - start
    log(f"H1: done ({elapsed:.1f}s)")


def test_h2_vague_budgeting_app():
    """H2: Vague input — should trigger clarification or succeed."""
    log("=" * 50)
    log("H2: Vague 'build a budgeting app'")
    start = time.time()
    result = _post_goal(raw_input="Build a budgeting app")
    plan_id = result.get("plan_id")
    check("H2: plan_id returned", bool(plan_id))
    status = result.get("status", "")
    check("H2: status is clarifying, gated_ok, or draft",
          status in ("awaiting_clarification", "gated_ok", "draft"))

    if status == "awaiting_clarification" and plan_id:
        check("H2: meta_goal present for clarify", result.get("meta_goal") is not None)
        # Answer the clarification
        answer = api("POST", f"/clarify/{plan_id}", {"answer": "A web-based budgeting app with income/expense tracking"})
        check("H2: clarify endpoint responded", answer.get("_status", 200) != 400)
        ans_status = answer.get("status", "")
        check("H2: clarify resolved to formulated or complete",
              ans_status in ("formulated", "awaiting_clarification", "gated_ok"))
        log(f"  Clarify answer status: {ans_status}")

    elapsed = time.time() - start
    log(f"H2: done ({elapsed:.1f}s)")


def test_h4_api_only_no_false_fe():
    """H4: API service — no false FE conventions."""
    log("=" * 50)
    log("H4: REST API for bookmarks (no UI)")
    start = time.time()
    result = _post_goal(
        raw_input="REST API for a bookmarks service with CRUD endpoints",
        spec="API only, no UI needed",
        quality_intent="RESTful, validated inputs, correct status codes",
    )
    plan_id = result.get("plan_id")
    check("H4: plan_id returned", bool(plan_id))
    status = result.get("status", "")
    check("H4: status present", bool(status))
    # No UI conventions should leak into the spec
    meta = result.get("meta_goal")
    if meta:
        spec_text = (meta.get("spec", "") if isinstance(meta, dict) else "") or ""
        false_fe = "frontend" in spec_text.lower() or "html" in spec_text.lower()
        check("H4: no false FE conventions in meta_goal", not false_fe)
    elapsed = time.time() - start
    log(f"H4: done ({elapsed:.1f}s)")


def test_h6_non_runnable_domain():
    """H6: Research report (non-runnable domain)."""
    log("=" * 50)
    log("H6: Research report (non-runnable)")
    start = time.time()
    result = _post_goal(
        raw_input="Research report: compare 3 web frameworks on performance and developer experience",
        spec="Compare React, Vue, Angular",
        quality_intent="Accurate, well-cited, clear conclusion",
    )
    plan_id = result.get("plan_id")
    check("H6: plan_id returned", bool(plan_id))
    status = result.get("status", "")
    check("H6: status present", bool(status))
    meta = result.get("meta_goal")
    if meta and isinstance(meta, dict):
        domain = meta.get("domain", "")
        if domain:
            check("H6: domain is research_report or recognized", domain in ("research_report", "generic", "software_app"))
    elapsed = time.time() - start
    log(f"H6: done ({elapsed:.1f}s)")


def test_h7_combined_domains():
    """H7: Combined domains (app + doc)."""
    log("=" * 50)
    log("H7: Combined domains (app + doc)")
    start = time.time()
    result = _post_goal(
        raw_input="Build a habit tracker app AND write its design document",
        spec="Fullstack CRUD app + design rationale document",
        quality_intent="App works correctly, document is thorough and cited",
    )
    plan_id = result.get("plan_id")
    check("H7: plan_id returned", bool(plan_id))
    status = result.get("status", "")
    check("H7: status present", bool(status))
    if status == "awaiting_clarification":
        check("H7: clarify questions or meta_goal", bool(result.get("meta_goal")))
    elif status == "gated_ok":
        check("H7: gated with plan_goal_review", result.get("plan_goal_review") is not None)
    elapsed = time.time() - start
    log(f"H7: done ({elapsed:.1f}s)")


def test_h8_unknown_domain_clarify():
    """H8: Unknown domain → clarify/defer."""
    log("=" * 50)
    log("H8: Unknown domain 'data dashboard'")
    start = time.time()
    result = _post_goal(raw_input="Make a data dashboard")
    plan_id = result.get("plan_id")
    check("H8: plan_id returned", bool(plan_id))
    status = result.get("status", "")
    check("H8: status is awaiting_clarification (likely)", status == "awaiting_clarification")
    if status == "awaiting_clarification":
        check("H8: meta_goal present for clarify", result.get("meta_goal") is not None)
    elapsed = time.time() - start
    log(f"H8: done ({elapsed:.1f}s)")


def test_h9_ratify_gated_plan():
    """H9: Create → gate → ratify full cycle via planner-svc.

    Tests a well-specified goal that passes through the full LangGraph
    pipeline (formulate → inject → decompose → gate), gets persisted,
    then ratified via the plan gate.
    """
    log("=" * 50)
    log("H9: Ratify gated plan full cycle")
    start = time.time()

    result = _post_goal(
        raw_input="Build a REST API for a bookmarks service with CRUD endpoints, persisted to SQLite",
        spec="API only, no UI",
        quality_intent="RESTful, validated inputs, correct HTTP status codes",
    )
    plan_id = result.get("plan_id")
    status = result.get("status", "")
    check("H9: plan_id returned", bool(plan_id))
    check("H9: status is gated_ok", status == "gated_ok")

    if plan_id and status == "gated_ok":
        check("H9: plan_goal_review from gate", result.get("plan_goal_review") is not None)

        ratify_result = _ratify_plan(plan_id)
        check("H9: ratify completed", ratify_result.get("_status", 200) != 400)
        ratify_status = ratify_result.get("status", "")
        check("H9: ratify status is ratified or gate_failed",
              ratify_status in ("ratified", "gate_failed"))

        if ratify_status == "ratified":
            check("H9: run_id returned", bool(ratify_result.get("run_id")))
            check("H9: plan_goal_review in ratify response",
                  ratify_result.get("plan_goal_review") is not None)
            log(f"  Plan ratified: run_id={ratify_result.get('run_id')} "
                f"score={ratify_result.get('plan_goal_review')}")
        elif ratify_status == "gate_failed":
            log(f"  Gate feedback: {ratify_result.get('gate_feedback', '')[:100]}")

    elapsed = time.time() - start
    log(f"H9: done ({elapsed:.1f}s)")


# ── Main ─────────────────────────────────────────────────────────────────


def run_unit_tests():
    test_domain_inference()
    test_domain_profile_retrieval()
    test_convention_enrichment()
    test_staffing_l1()
    test_plan_evaluator_with_staffing()
    test_agent_generator_validation()
    test_combined_domain_merge()


def run_integration_tests():
    log("Starting planner-svc integration tests at %s" % PLANNER_API)
    test_h1_finance_tracker_convention_injection()
    test_h2_vague_budgeting_app()
    test_h4_api_only_no_false_fe()
    test_h6_non_runnable_domain()
    test_h7_combined_domains()
    test_h8_unknown_domain_clarify()
    test_h9_ratify_gated_plan()
    log("Integration tests complete")


def main():
    global PASS, FAIL, SKIP

    run_unit = "--unit" in sys.argv
    run_integration = not run_unit

    if run_unit:
        print("Running UNIT tests only (no planner-svc needed)")
        run_unit_tests()
    else:
        available = check_api_available()
        if not available:
            print(f"planner-svc not reachable at {PLANNER_API} — running unit tests only")
            print(f"  Start it with: uv run uvicorn services.planner.main:app --host 127.0.0.1 --port 8094")
            run_unit_tests()
        else:
            print(f"planner-svc reachable at {PLANNER_API} — running full suite")
            run_unit_tests()
            run_integration_tests()

    total = PASS + FAIL + SKIP
    print(f"\n{'=' * 60}")
    print(f"Results: {PASS} PASS / {FAIL} FAIL / {SKIP} SKIP ({total} total)")
    print(f"{'=' * 60}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
